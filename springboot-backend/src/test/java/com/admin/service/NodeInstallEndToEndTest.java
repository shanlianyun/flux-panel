package com.admin.service;

import com.admin.common.lang.R;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.io.TempDir;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.*;

/** Real generated command, installer and native GOST; only OS/download boundaries are sandboxed. */
@EnabledIfEnvironmentVariable(named = "NODE_INSTALL_E2E_GOST", matches = ".+")
class NodeInstallEndToEndTest {
    @TempDir Path temporary;

    private void reinstall(String mode) throws Exception {
        Path repository = Path.of(System.getProperty("user.dir")).toAbsolutePath();
        if (!Files.exists(repository.resolve("install.sh"))) repository = repository.getParent();
        Path output = temporary.resolve("runner.log");
        String image = System.getenv("NODE_INSTALL_E2E_CONTAINER_IMAGE");
        List<String> arguments = new ArrayList<>();
        if (image != null && !image.isBlank()) {
            // macOS uses its system keychain, not SSL_CERT_FILE. Run Linux GOST in a container there.
            arguments.addAll(List.of("docker", "run", "--rm", "-i", "--mount",
                    "type=bind,source=" + repository + ",target=/repo,readonly", "--mount",
                    "type=bind,source=" + Path.of(System.getenv("NODE_INSTALL_E2E_GOST")).toRealPath()
                            + ",target=/fixture/gost,readonly",
                    "--entrypoint", "python3", image, "/repo/tests/node_install_e2e.py", "/fixture/gost", mode));
        } else {
            arguments.addAll(List.of("python3", repository.resolve("tests/node_install_e2e.py").toString(),
                    System.getenv("NODE_INSTALL_E2E_GOST"), mode));
        }
        Process runner = new ProcessBuilder(arguments)
                .directory(repository.toFile()).redirectError(output.toFile()).start();
        try {
            // The runner announces its real HTTP/TLS listener, then consumes the unmodified copied command.
            BufferedReader reader = new BufferedReader(new InputStreamReader(runner.getInputStream(), StandardCharsets.UTF_8));
            String endpoint = reader.readLine();
            assertNotNull(endpoint, Files.readString(output));
            R result = NodeInstallCommandTest.response(endpoint, "e2e-node-secret");
            assertEquals(0, result.getCode(), result.getMsg());
            runner.getOutputStream().write(((String) result.getData() + "\n").getBytes(StandardCharsets.UTF_8));
            runner.getOutputStream().close();
            assertTrue(runner.waitFor(60, TimeUnit.SECONDS), "Installer E2E timed out");
            assertEquals(0, runner.exitValue(), Files.readString(output));
            assertTrue(reader.lines().anyMatch(line -> line.equals("PASS " + mode)), Files.readString(output));
        } finally {
            runner.destroyForcibly();
        }
    }

    @Test void httpUninstallAndReinstallConnects() throws Exception { reinstall("http"); }
    @Test void trustedHttpsUninstallAndReinstallConnects() throws Exception { reinstall("https"); }
    @Test void rejectedHandshakeDoesNotStopNodeOrClaimOnline() throws Exception { reinstall("forbidden"); }
    @Test void untrustedCertificateNeverFallsBackToPlaintext() throws Exception { reinstall("untrusted"); }
}
