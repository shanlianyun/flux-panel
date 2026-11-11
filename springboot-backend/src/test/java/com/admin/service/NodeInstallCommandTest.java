package com.admin.service;

import com.admin.common.lang.R;
import com.admin.entity.Node;
import com.admin.entity.ViteConfig;
import com.admin.service.impl.NodeServiceImpl;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.test.util.ReflectionTestUtils;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class NodeInstallCommandTest {
    @TempDir Path temporary;

    static R response(String address, String secret) {
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
        return service.getInstallCommand(1L);
    }

    private String command(String endpoint, String secret) {
        R result = response(endpoint, secret);
        assertEquals(0, result.getCode(), result.getMsg());
        return (String) result.getData();
    }

    private int execute(String command, int downloadStatus, int installStatus) throws Exception {
        Path bin = Files.createDirectories(temporary.resolve("bin"));
        Path installer = temporary.resolve("downloaded.sh");
        Path payload = temporary.resolve("payload");
        Files.writeString(payload, "#!/bin/bash\nprintf '%s\\0' \"$@\" > \"$CAPTURE\"\nexit \"$INSTALL_STATUS\"\n");
        Path curl = bin.resolve("curl");
        Files.writeString(curl, "#!/bin/bash\ncp \"$PAYLOAD\" \"$DOWNLOADED\"\nexit \"$DOWNLOAD_STATUS\"\n");
        assertTrue(curl.toFile().setExecutable(true));
        // Redirect only the managed /root script location; execute the copied shell unchanged.
        Path bash = bin.resolve("bash");
        Files.writeString(bash, "#!/bin/bash\nif [[ $1 == /root/flux-node.sh ]]; then shift; exec /bin/bash \"$DOWNLOADED\" \"$@\"; fi\nexit 99\n");
        assertTrue(bash.toFile().setExecutable(true));
        ProcessBuilder builder = new ProcessBuilder("/bin/sh", "-c", command)
                .directory(temporary.toFile()).redirectErrorStream(true)
                .redirectOutput(temporary.resolve("output.log").toFile());
        builder.environment().put("PATH", bin + ":" + System.getenv("PATH"));
        builder.environment().put("PAYLOAD", payload.toString());
        builder.environment().put("DOWNLOADED", installer.toString());
        builder.environment().put("CAPTURE", temporary.resolve("args").toString());
        builder.environment().put("DOWNLOAD_STATUS", Integer.toString(downloadStatus));
        builder.environment().put("INSTALL_STATUS", Integer.toString(installStatus));
        builder.environment().put("GOST_PANEL_SCHEME", "http");
        Process process = builder.start();
        if (!process.waitFor(10, TimeUnit.SECONDS)) {
            process.destroyForcibly();
            fail("Copied command timed out");
        }
        return process.exitValue();
    }

    @Test
    void copiedCommandIsOrdinaryDownloadAndDirectExecution() {
        String command = command("https://panel.example.com:443", "test-only");
        assertTrue(command.startsWith("curl -fsSL --retry 3 "));
        assertTrue(command.contains(" -o /root/flux-node.sh && bash /root/flux-node.sh install "));
        assertFalse(command.contains("printf"));
        assertFalse(command.contains("bash -c"));
        assertFalse(command.contains("|"));
    }

    @Test
    void httpsAndSecretAreLiteralArgumentsWithoutExternalEnvironmentSetup() throws Exception {
        String secret = "test'$(touch injected); secret";
        assertEquals(0, execute(command("https://panel.example.com/", secret), 0, 0));
        List<String> args = Arrays.asList(Files.readString(temporary.resolve("args")).split("\u0000"));
        assertEquals(List.of("install", "-a", "https://panel.example.com:443", "-s", secret), args);
        assertFalse(Files.exists(temporary.resolve("injected")));
    }

    @Test
    void explicitHttpAndBracketedIpv6ArePreserved() throws Exception {
        assertEquals(0, execute(command("http://[2001:db8::1]:6365", "test-only"), 0, 0));
        assertEquals("http://[2001:db8::1]:6365", Files.readString(temporary.resolve("args")).split("\u0000")[2]);
    }

    @Test
    void completeFileWithTransferFailureCannotStartInstallation() throws Exception {
        assertEquals(18, execute(command("https://panel.example.com", "test-only"), 18, 0));
        assertFalse(Files.exists(temporary.resolve("args")));
    }

    @Test
    void installerFailureIsPreserved() throws Exception {
        assertEquals(23, execute(command("https://panel.example.com", "test-only"), 0, 23));
    }

    @Test
    void ambiguousOrInvalidEndpointsCannotGenerateSilentlyWrongCommands() {
        for (String endpoint : List.of("panel.example.com:443", "127.0.0.1:6365", "",
                "ftp://panel.example.com", "https://user:password@panel.example.com",
                "https://panel.example.com/api", "https://panel.example.com?token=secret",
                "https://panel.example.com/#fragment", "https://panel.example.com:65536")) {
            R result = response(endpoint, "test-only");
            assertNotEquals(0, result.getCode(), endpoint);
            assertNull(result.getData(), endpoint);
        }
    }
}
