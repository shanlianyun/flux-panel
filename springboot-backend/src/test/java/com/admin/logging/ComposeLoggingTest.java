package com.admin.logging;

import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.yaml.snakeyaml.Yaml;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class ComposeLoggingTest {
    @ParameterizedTest
    @ValueSource(strings = {"docker-compose-v4.yml", "docker-compose-v6.yml",
            "docker-compose-fork-v4.yml", "docker-compose-fork-v6.yml"})
    @SuppressWarnings("unchecked")
    void everyServiceHasBoundedDockerLogging(String filename) throws Exception {
        Map<String, Object> compose;
        try (var input = Files.newInputStream(Path.of("..", filename))) {
            compose = new Yaml().load(input);
        }
        Map<String, Map<String, Object>> services = (Map<String, Map<String, Object>>) compose.get("services");
        assertFalse(services.isEmpty());
        for (var service : services.entrySet()) {
            Map<String, Object> logging = (Map<String, Object>) service.getValue().get("logging");
            assertNotNull(logging, filename + ": " + service.getKey() + " has no log limits");
            assertEquals("json-file", logging.get("driver"));
            Map<String, String> options = (Map<String, String>) logging.get("options");
            assertEquals("10m", options.get("max-size"));
            assertEquals("3", options.get("max-file"));
        }
    }
}
