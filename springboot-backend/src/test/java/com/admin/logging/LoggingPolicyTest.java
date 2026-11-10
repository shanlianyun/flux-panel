package com.admin.logging;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.LoggerContext;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.joran.GenericConfigurator;
import ch.qos.logback.core.rolling.RollingFileAppender;
import ch.qos.logback.core.rolling.TimeBasedFileNamingAndTriggeringPolicyBase;
import ch.qos.logback.core.rolling.TimeBasedRollingPolicy;
import ch.qos.logback.core.status.Status;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.logging.LoggingInitializationContext;
import org.springframework.boot.env.YamlPropertySourceLoader;
import org.springframework.core.env.SystemEnvironmentPropertySource;
import org.springframework.core.io.ClassPathResource;
import org.springframework.mock.env.MockEnvironment;

import java.nio.file.Files;
import java.nio.file.Path;
import java.io.RandomAccessFile;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.stream.Collectors;
import java.util.zip.GZIPInputStream;
import java.util.zip.GZIPOutputStream;

import static org.junit.jupiter.api.Assertions.*;

class LoggingPolicyTest {
    @TempDir
    Path directory;
    private LoggerContext context;

    @AfterEach
    void stop() {
        if (context != null) context.stop();
    }

    @Test
    void defaultConfigurationRotatesAndCompressesWithinOneDay() throws Exception {
        configure(new MockEnvironment());
        Logger logger = context.getLogger("retention-test");
        String payload = "x".repeat(4096);
        for (int i = 0; i < 12000; i++) logger.info("segment {} {}", i, payload);
        int history = policy().getMaxHistory();
        boolean cleanOnStart = policy().isCleanHistoryOnStart();
        context.stop(); // Wait for asynchronous compression and cleanup.

        List<Path> archives = archives();
        assertFalse(archives.isEmpty(), "A busy day must rotate instead of growing a single file indefinitely");
        assertTrue(Files.size(directory.resolve("backend.log")) > 0);
        try (GZIPInputStream input = new GZIPInputStream(Files.newInputStream(archives.get(0)))) {
            assertTrue(new String(input.readNBytes(256)).contains("segment 0"));
        }
        assertEquals(7, history);
        assertTrue(cleanOnStart);
    }

    @Test
    void smallerSegmentOverrideTakesEffect() throws Exception {
        configure(new MockEnvironment().withProperty("log-file-max-size", "1KB"));
        Logger logger = context.getLogger("retention-test");
        for (int i = 0; i < 500; i++) logger.info("{}", "y".repeat(1024));
        context.stop();
        assertFalse(archives().isEmpty(), "An overridden segment limit must affect actual rotation");
    }

    @Test
    void environmentOverridesResolveThroughTheShippedApplicationYaml() throws Exception {
        MockEnvironment environment = new MockEnvironment();
        environment.getPropertySources().addFirst(new SystemEnvironmentPropertySource("test-environment", Map.of(
                "LOG_FILE_MAX_SIZE", "1KB", "LOG_FILE_MAX_HISTORY", "2", "LOG_FILE_TOTAL_SIZE_CAP", "2KB")));
        for (var source : new YamlPropertySourceLoader().load("application", new ClassPathResource("application.yml"))) {
            environment.getPropertySources().addLast(source);
        }
        configure(environment);
        assertEquals(2, policy().getMaxHistory());
        for (int i = 0; i < 500; i++) context.getLogger("retention-test").info("{}", "z".repeat(1024));
        context.stop();
        assertFalse(archives().isEmpty());
    }

    @Test
    void dateChangeRotatesEvenWhenActiveFileIsSmall() throws Exception {
        configure(new MockEnvironment());
        Logger logger = context.getLogger("retention-test");
        logger.info("before-date-change");
        TimeBasedFileNamingAndTriggeringPolicyBase<?> clock = (TimeBasedFileNamingAndTriggeringPolicyBase<?>)
                policy().getTimeBasedFileNamingAndTriggeringPolicy();
        clock.setCurrentTime(System.currentTimeMillis() + 24 * 60 * 60 * 1000L);
        logger.info("after-date-change");
        context.stop();
        assertEquals(1, archives().size());
        assertTrue(Files.readString(directory.resolve("backend.log")).contains("after-date-change"));
        try (GZIPInputStream input = new GZIPInputStream(Files.newInputStream(archives().get(0)))) {
            assertTrue(new String(input.readAllBytes()).contains("before-date-change"));
        }
    }

    @Test
    void defaultCapacityBoundsRecentArchiveHistory() throws Exception {
        for (int i = 0; i < 3; i++) {
            Path path = archive(LocalDate.now().minusDays(1), i, 512);
            // Sparse fixtures exercise the real 200MB cap without allocating 240MB on disk.
            try (RandomAccessFile file = new RandomAccessFile(path.toFile(), "rw")) {
                file.setLength(80L * 1024 * 1024);
            }
        }
        configure(new MockEnvironment());
        context.stop();
        long size = 0;
        for (Path path : archives()) size += Files.size(path);
        assertTrue(size <= 200L * 1024 * 1024, "Default retention must bound total bytes as well as days");
        assertFalse(archives().isEmpty());
    }

    @Test
    void startupCleanupHasBoundedLookbackAndPreservesLegacyFiles() throws Exception {
        Path expired = archive(LocalDate.now().minusDays(10), 0, 512);
        Path recent = archive(LocalDate.now().minusDays(1), 0, 512);
        Path longOutage = archive(LocalDate.now().minusDays(60), 0, 512);
        Path legacy = directory.resolve(LocalDate.now().minusDays(40) + ".log");
        Files.writeString(legacy, "legacy logs need operator review");
        configure(new MockEnvironment());
        context.stop();
        assertFalse(Files.exists(expired), "Expired matching archives must be cleaned at startup");
        assertTrue(Files.exists(recent));
        assertTrue(Files.exists(longOutage), "Logback's bounded startup lookback requires separate long-outage cleanup");
        assertTrue(Files.exists(legacy), "Changing the archive pattern must not delete unrelated files");
    }

    @Test
    void archiveCapacityOverrideRemovesExcessRecentArchivesAtStartup() throws Exception {
        for (int i = 0; i < 3; i++) archive(LocalDate.now().minusDays(1), i, 1500);
        configure(new MockEnvironment()
                .withProperty("log-file-max-size", "1KB")
                .withProperty("log-file-total-size-cap", "2KB"));
        context.stop();
        long size = 0;
        for (Path path : archives()) size += Files.size(path);
        assertTrue(size <= 2048, "Recent archives must also respect the capacity limit, not just age");
        assertFalse(archives().isEmpty(), "Keep the newest archive that fits");
    }

    @Test
    void historyOverrideControlsStartupCleanup() throws Exception {
        Path expired = archive(LocalDate.now().minusDays(4), 0, 512);
        configure(new MockEnvironment().withProperty("log-file-max-history", "2"));
        context.stop();
        assertFalse(Files.exists(expired));
    }

    private void configure(MockEnvironment environment) throws Exception {
        context = new LoggerContext();
        environment.setProperty("log-dir", directory.toString());
        // Use Boot's real Spring-aware parser without resetting the shared test logger context.
        var constructor = Class.forName("org.springframework.boot.logging.logback.SpringBootJoranConfigurator")
                .getDeclaredConstructor(LoggingInitializationContext.class);
        constructor.setAccessible(true);
        GenericConfigurator configurator = (GenericConfigurator) constructor.newInstance(
                new LoggingInitializationContext(environment));
        configurator.setContext(context);
        configurator.doConfigure(getClass().getResource("/logback-spring.xml"));
        List<Status> errors = context.getStatusManager().getCopyOfStatusList().stream()
                .filter(status -> status.getLevel() == Status.ERROR).collect(Collectors.toList());
        assertTrue(errors.isEmpty(), errors.toString());
        context.getLogger(Logger.ROOT_LOGGER_NAME).detachAppender("console");
    }

    @SuppressWarnings("unchecked")
    private TimeBasedRollingPolicy<ILoggingEvent> policy() {
        RollingFileAppender<ILoggingEvent> appender = (RollingFileAppender<ILoggingEvent>)
                context.getLogger(Logger.ROOT_LOGGER_NAME).getAppender("logFile");
        return (TimeBasedRollingPolicy<ILoggingEvent>) appender.getRollingPolicy();
    }

    private Path archive(LocalDate date, int index, int size) throws Exception {
        Path path = directory.resolve("backend." + date + "." + index + ".log.gz");
        byte[] bytes = new byte[size];
        new Random(index).nextBytes(bytes);
        try (GZIPOutputStream output = new GZIPOutputStream(Files.newOutputStream(path))) {
            output.write(bytes);
        }
        return path;
    }

    private List<Path> archives() throws Exception {
        try (var paths = Files.list(directory)) {
            return paths.filter(path -> path.getFileName().toString().endsWith(".log.gz"))
                    .sorted().collect(Collectors.toList());
        }
    }
}
