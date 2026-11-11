package com.admin.service;

import com.admin.common.lang.R;
import com.admin.entity.Node;
import com.admin.entity.ViteConfig;
import com.admin.service.impl.NodeServiceImpl;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.test.util.ReflectionTestUtils;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class NodeInstallCommandTest {
    @TempDir
    Path temporary;

    private String command(String address, String secret) {
        NodeServiceImpl service = spy(new NodeServiceImpl());
        Node node = new Node();
        node.setId(1L);
        node.setSecret(secret);
        doReturn(node).when(service).getById(1L);
        ViteConfig config = new ViteConfig();
        config.setValue(address);
        ViteConfigService configs = mock(ViteConfigService.class);
        when(configs.getOne(any())).thenReturn(config);
        ReflectionTestUtils.setField(service, "viteConfigService", configs);
        R result = service.getInstallCommand(1L);
        assertEquals(0, result.getCode());
        return (String) result.getData();
    }

    private int execute(String command, String downloader) throws Exception {
        return execute(command, downloader, 0);
    }

    private int execute(String command, String downloader, int installerStatus) throws Exception {
        Path bin = Files.createDirectories(temporary.resolve("bin"));
        Path curl = bin.resolve("curl");
        Files.writeString(curl, "#!/bin/bash\n" + downloader);
        assertTrue(curl.toFile().setExecutable(true));
        Path fixture = temporary.resolve("downloaded.sh");
        Files.writeString(fixture, "#!/bin/bash\nprintf '%s\\0' \"$@\" > \"$CAPTURE\"\n"
                + "printf '%s' \"${GOST_PANEL_SCHEME:-http}\" > \"$SCHEME_CAPTURE\"\n"
                + "exit \"${INSTALL_STATUS:-0}\"\n");
        Path scratch = Files.createDirectories(temporary.resolve("scratch"));
        ProcessBuilder builder = new ProcessBuilder("/bin/sh", "-c", command)
                .directory(temporary.toFile()).redirectErrorStream(true)
                .redirectOutput(temporary.resolve("output.log").toFile());
        builder.environment().put("PATH", bin + ":" + System.getenv("PATH"));
        builder.environment().put("FIXTURE", fixture.toString());
        builder.environment().put("CAPTURE", temporary.resolve("args").toString());
        builder.environment().put("SCHEME_CAPTURE", temporary.resolve("scheme").toString());
        builder.environment().put("GOST_PANEL_SCHEME", "https");
        builder.environment().put("INSTALL_STATUS", Integer.toString(installerStatus));
        builder.environment().put("TMPDIR", scratch.toString());
        builder.environment().put("BOOTSTRAP_FIXTURE", Path.of("..", "node.sh").toAbsolutePath().normalize().toString());
        Process process = builder.start();
        if (!process.waitFor(10, TimeUnit.SECONDS)) {
            process.destroyForcibly();
            fail("Install command timed out");
        }
        try (var files = Files.list(scratch)) {
            assertEquals(0, files.count(), "Temporary installer was not cleaned up");
        }
        return process.exitValue();
    }

    private static final String STREAM_FIXTURE = "if [[ \"$*\" == *'/node.sh'* ]]; then cat \"$BOOTSTRAP_FIXTURE\"; exit; fi\n"
            + "output=''\n"
            + "while [[ $# -gt 0 ]]; do\n"
            + "case \"$1\" in -o) output=\"$2\"; shift 2 ;; *) shift ;; esac\ndone\n"
            + "if [[ -n \"$output\" ]]; then cp \"$FIXTURE\" \"$output\"; else cat \"$FIXTURE\"; fi\n";

    @Test
    void copiedCommandFitsOneShortLine() {
        String command = command("panel.example.com:443", "0123456789abcdef0123456789abcdef");
        assertFalse(command.contains("\n"));
        assertTrue(command.length() <= 240, "Install command still has " + command.length() + " characters");
    }

    @Test
    void passesAddressAndSecretAsLiteralArgumentsAndKeepsHttps() throws Exception {
        String secret = "test'$(touch injected); secret";
        assertEquals(0, execute(command("panel.example.com:443", secret), STREAM_FIXTURE));
        String captured = Files.readString(temporary.resolve("args"), StandardCharsets.UTF_8);
        List<String> args = Arrays.asList(captured.split("\u0000"));
        assertEquals(List.of("install", "-a", "panel.example.com:443", "-s", secret), args);
        assertEquals("https", Files.readString(temporary.resolve("scheme")));
        assertFalse(Files.exists(temporary.resolve("injected")));
    }

    @Test
    void bracketsBareIpv6Address() throws Exception {
        assertEquals(0, execute(command("2001:db8::1:6365", "test-only"), STREAM_FIXTURE));
        String captured = Files.readString(temporary.resolve("args"), StandardCharsets.UTF_8);
        assertEquals("[2001:db8::1]:6365", captured.split("\u0000")[2]);
    }

    @Test
    void emptyDownloadFailureCannotLookLikeSuccess() throws Exception {
        assertNotEquals(0, execute(command("panel.example.com:443", "test-only"), "exit 22\n"));
        assertFalse(Files.exists(temporary.resolve("args")));
    }

    @Test
    void installerFailureIsPreserved() throws Exception {
        assertEquals(23, execute(command("panel.example.com:443", "test-only"), STREAM_FIXTURE, 23));
    }

    @Test
    void truncatedBootstrapCannotStartInstaller() throws Exception {
        assertNotEquals(0, execute(command("panel.example.com:443", "test-only"),
                "sed '$d' \"$BOOTSTRAP_FIXTURE\"\nexit 18\n"));
        assertFalse(Files.exists(temporary.resolve("args")));
    }

    @Test
    void completeBootstrapFollowedByDownloadFailureCannotStartInstaller() throws Exception {
        String downloader = STREAM_FIXTURE.replace("cat \"$BOOTSTRAP_FIXTURE\"; exit;",
                "cat \"$BOOTSTRAP_FIXTURE\"; exit 18;");
        assertNotEquals(0, execute(command("panel.example.com:443", "test-only"), downloader));
        assertFalse(Files.exists(temporary.resolve("args")), "Failed download started installation");
    }

    @Test
    void missingFinalNewlineAndDownloadFailureCannotStartInstaller() throws Exception {
        String downloader = STREAM_FIXTURE.replace("cat \"$BOOTSTRAP_FIXTURE\"; exit;",
                "printf %s \"$(cat \"$BOOTSTRAP_FIXTURE\")\"; exit 18;");
        assertNotEquals(0, execute(command("panel.example.com:443", "test-only"), downloader));
        assertFalse(Files.exists(temporary.resolve("args")), "Failed download started installation");
    }
}
