package com.admin.common.utils;

import com.admin.entity.*;
import com.admin.controller.FlowController;
import com.admin.common.dto.UserTunnelUpdateDto;
import com.admin.mapper.ForwardMapper;
import com.admin.service.*;
import com.admin.service.impl.ForwardServiceImpl;
import com.admin.service.impl.UserTunnelServiceImpl;
import com.alibaba.fastjson2.JSONArray;
import com.alibaba.fastjson2.JSONObject;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.*;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.CountDownLatch;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class NodeReconnectSyncTest {
    private AnnotationConfigApplicationContext context;
    private WebSocketServer server;
    private Tunnel tunnel;
    private Forward forward;
    private User user;
    private UserTunnel userTunnel;
    private final List<WebSocketSession> sessions = new ArrayList<>();
    private final List<JSONObject> commands = new CopyOnWriteArrayList<>();
    private final Set<String> resources = new HashSet<>();
    private final Set<String> pausedResources = new HashSet<>();
    private String rejectedService;
    private java.util.function.Consumer<String> commandHook;
    private final List<String> commandThreads = new CopyOnWriteArrayList<>();
    private final List<String> commandSessions = new CopyOnWriteArrayList<>();

    @BeforeEach
    void setUp() {
        // Only these beans are loaded: no application, database, or real node connections.
        context = new AnnotationConfigApplicationContext(TestConfig.class);
        server = context.getBean(WebSocketServer.class);
        tunnel = new Tunnel();
        tunnel.setId(10L);
        tunnel.setStatus(1);
        tunnel.setType(1);
        tunnel.setInNodeId(1L);
        tunnel.setOutNodeId(2L);
        tunnel.setOutIp("192.0.2.2");
        tunnel.setProtocol("tls");
        tunnel.setTcpListenAddr("0.0.0.0");
        tunnel.setUdpListenAddr("0.0.0.0");
        forward = new Forward();
        forward.setId(100L);
        forward.setStatus(1);
        forward.setUserId(20);
        forward.setTunnelId(10);
        forward.setInPort(12000);
        forward.setOutPort(13000);
        forward.setRemoteAddr("192.0.2.100:443");
        forward.setStrategy("round");
        forward.setInterfaceName("eth0");
        user = new User();
        user.setId(20L);
        user.setRoleId(1);
        user.setStatus(1);
        user.setFlow(100L);
        user.setInFlow(0L);
        user.setOutFlow(0L);
        userTunnel = new UserTunnel();
        userTunnel.setId(30);
        userTunnel.setUserId(20);
        userTunnel.setTunnelId(10);
        userTunnel.setStatus(1);
        userTunnel.setFlow(100L);
        userTunnel.setInFlow(0L);
        userTunnel.setOutFlow(0L);
        when(context.getBean(TunnelService.class).list(any())).thenReturn(Collections.singletonList(tunnel));
        when(context.getBean(TunnelService.class).getById(any())).thenReturn(tunnel);
        when(context.getBean(ForwardMapper.class).selectList(any())).thenReturn(Collections.singletonList(forward));
        when(context.getBean(ForwardMapper.class).selectById(any())).thenReturn(forward);
        when(context.getBean(UserService.class).getById(any())).thenReturn(user);
        when(context.getBean(UserTunnelService.class).getOne(any())).thenReturn(userTunnel);
        Node node = new Node();
        node.setId(1L);
        when(context.getBean(NodeService.class).getById(any())).thenReturn(node);
        when(context.getBean(NodeService.class).updateById(any())).thenReturn(true);
    }

    @AfterEach
    void tearDown() throws Exception {
        drainSync();
        for (WebSocketSession session : sessions) {
            server.afterConnectionClosed(session, CloseStatus.NORMAL);
        }
        context.close();
    }

    @Test
    void freshNodeReceivesStoredTcpAndUdpRulesWithoutEditing() throws Exception {
        connect(1L);
        drainSync();

        List<JSONObject> services = addedServices();
        assertEquals(2, services.size());
        JSONObject tcp = services.get(0);
        assertEquals("100_20_30_tcp", tcp.getString("name"));
        assertEquals("0.0.0.0:12000", tcp.getString("addr"));
        assertEquals("eth0", tcp.getJSONObject("metadata").getString("interface"));
        assertEquals("192.0.2.100:443", tcp.getJSONObject("forwarder")
                .getJSONArray("nodes").getJSONObject(0).getString("addr"));
        assertEquals("100_20_30_udp", services.get(1).getString("name"));
        verify(context.getBean(ForwardMapper.class), never()).updateById(any());
        assertEquals(1, forward.getStatus());
    }

    @Test
    void tunnelEntranceRestoresChainBeforeServicesWithoutContactingExit() throws Exception {
        tunnel.setType(2);
        tunnel.setOutIp("2001:db8::2");
        tunnel.setInterfaceName("eth1");
        connect(1L);
        drainSync();

        JSONObject chain = command("AddChains").getJSONObject("data");
        assertEquals("100_20_30_chains", chain.getString("name"));
        JSONObject hop = chain.getJSONArray("hops").getJSONObject(0).getJSONArray("nodes").getJSONObject(0);
        assertEquals("[2001:db8::2]:13000", hop.getString("addr"));
        assertEquals("eth1", hop.getString("interface"));
        JSONObject service = command("AddService").getJSONArray("data").getJSONObject(0);
        assertEquals("100_20_30_chains", service.getJSONObject("handler").getString("chain"));
        assertFalse(service.containsKey("metadata"));
        assertTrue(commands.indexOf(command("AddChains")) < commands.indexOf(command("AddService")));
        assertEquals(1, forward.getStatus());
    }

    @Test
    void tunnelExitRestoresOnlyRemoteListenerEvenWhenEntranceIsOffline() throws Exception {
        tunnel.setType(2);
        connect(2L);
        drainSync();

        JSONArray services = command("AddService").getJSONArray("data");
        assertEquals(1, services.size());
        assertEquals("100_20_30_tls", services.getJSONObject(0).getString("name"));
        assertEquals(":13000", services.getJSONObject(0).getString("addr"));
        assertTrue(commands.stream().noneMatch(c -> c.getString("type").contains("Chains")));
    }

    @Test
    void limiterIsRestoredBeforeRulesAndRetainsAssignedSpeed() throws Exception {
        userTunnel.setSpeedId(40);
        SpeedLimit speed = new SpeedLimit();
        speed.setId(40L);
        speed.setTunnelId(10L);
        speed.setStatus(1);
        speed.setSpeed(100);
        when(context.getBean(SpeedLimitService.class).getById(any())).thenReturn(speed);
        connect(1L);
        drainSync();

        assertEquals("40", command("AddLimiters").getJSONObject("data").getString("name"));
        assertEquals("$ 12.5MB 12.5MB", command("AddLimiters").getJSONObject("data").getJSONArray("limits").getString(0));
        assertEquals("40", command("AddService").getJSONArray("data").getJSONObject(0).getString("limiter"));
        assertTrue(commands.indexOf(command("AddLimiters")) < commands.indexOf(command("AddService")));
    }

    @ParameterizedTest
    @ValueSource(strings = {"expiredGrant", "paused", "deleted", "moved"})
    void invalidationWhileWaitingForLimiterMustStopReplay(String change) throws Exception {
        userTunnel.setSpeedId(40);
        SpeedLimit speed = new SpeedLimit();
        speed.setId(40L); speed.setTunnelId(10L); speed.setStatus(1); speed.setSpeed(100);
        when(context.getBean(SpeedLimitService.class).getById(any())).thenReturn(speed);
        commandHook = type -> {
            if (!"AddLimiters".equals(type)) return;
            switch (change) {
                case "expiredGrant": userTunnel.setExpTime(1L); userTunnel.setStatus(0); break;
                case "paused": forward.setStatus(0); break;
                case "deleted": when(context.getBean(ForwardMapper.class).selectById(any())).thenReturn(null); break;
                case "moved": forward.setTunnelId(99); break;
            }
        };
        connect(1L);
        drainSync();
        assertTrue(addedServices().stream().allMatch(s -> s.getJSONObject("metadata") != null
                        && s.getJSONObject("metadata").getBooleanValue("paused")),
                "Stale replay created an active service: " + commands);
    }

    @Test
    void invalidationDuringTcpCreationPausesBothProtocolsAndKeepsThemResumable() throws Exception {
        commandHook = type -> { if ("AddService".equals(type)) forward.setStatus(0); };
        connect(1L);
        drainSync();
        assertTrue(pausedResources.contains("1:Service:100_20_30_tcp"), "Invalidated replay left TCP active or missing");
        assertTrue(pausedResources.contains("1:Service:100_20_30_udp"), "Missing UDP needs a paused, resumable configuration");
        assertEquals("OK", GostUtil.ResumeService(1L, "100_20_30").getMsg());
    }

    @Test
    void quotaInvalidationStopsBothExistingProtocolsAndAllowsLaterResume() throws Exception {
        resources.add("1:Service:100_20_30_tcp");
        resources.add("1:Service:100_20_30_udp");
        commandHook = type -> { if ("AddService".equals(type)) user.setInFlow(100L * 1024 * 1024 * 1024); };
        connect(1L);
        drainSync();
        FlowController flowController = new FlowController();
        ReflectionTestUtils.setField(flowController, "forwardService", context.getBean(ForwardService.class));
        ReflectionTestUtils.setField(flowController, "tunnelService", context.getBean(TunnelService.class));
        flowController.pauseService(Collections.singletonList(forward), "100_20_30");
        assertEquals(0, forward.getStatus());
        assertTrue(pausedResources.contains("1:Service:100_20_30_tcp"));
        assertTrue(pausedResources.contains("1:Service:100_20_30_udp"), "Quota invalidation left UDP running");
        user.setInFlow(0L);
        assertEquals("OK", GostUtil.ResumeService(1L, "100_20_30").getMsg());
        assertTrue(pausedResources.isEmpty());
    }

    @Test
    void exitInvalidationKeepsRemoteServicePausedAndResumable() throws Exception {
        tunnel.setType(2);
        commandHook = type -> { if ("AddService".equals(type)) userTunnel.setExpTime(1L); };
        connect(2L);
        drainSync();
        assertTrue(pausedResources.contains("2:Service:100_20_30_tls"));
        assertEquals("OK", GostUtil.ResumeRemoteService(2L, "100_20_30").getMsg());
    }

    @Test
    void cosmeticTunnelChangesDoNotInvalidateHealthyReplay() throws Exception {
        resources.add("1:Service:100_20_30_tcp");
        resources.add("1:Service:100_20_30_udp");
        commandHook = type -> {
            if (!"AddService".equals(type)) return;
            tunnel.setName("renamed during replay");
            tunnel.setFlow(2);
            tunnel.setTrafficRatio(new java.math.BigDecimal("1.5"));
            tunnel.setUpdatedTime(System.currentTimeMillis());
        };
        connect(1L);
        drainSync();
        assertEquals(2, addedServices().size());
        assertTrue(commands.stream().allMatch(c -> "AddService".equals(c.getString("type"))),
                "Harmless metadata edit interrupted healthy services: " + commands);
        assertEquals(2, resources.size());
    }

    @Test
    void earlyInvalidatedTunnelReplayRetainsDependenciesForResume() throws Exception {
        tunnel.setType(2);
        userTunnel.setSpeedId(40);
        SpeedLimit speed = new SpeedLimit();
        speed.setId(40L); speed.setTunnelId(10L); speed.setStatus(1); speed.setSpeed(100);
        when(context.getBean(SpeedLimitService.class).getById(any())).thenReturn(speed);
        commandHook = type -> { if ("AddLimiters".equals(type)) userTunnel.setExpTime(1L); };
        connect(1L);
        drainSync();
        assertTrue(resources.contains("1:Chains:100_20_30_chains"), "Paused tunnel lost its exit chain");
        assertTrue(resources.contains("1:Limiters:40"));
        assertTrue(pausedResources.containsAll(Arrays.asList("1:Service:100_20_30_tcp", "1:Service:100_20_30_udp")));
        userTunnel.setExpTime(null);
        assertEquals("OK", GostUtil.ResumeService(1L, "100_20_30").getMsg());
        assertEquals("100_20_30_chains", addedServices().get(0).getJSONObject("handler").getString("chain"));
    }

    @Test
    void compensationCannotSendToReplacementBetweenSessionCheckAndSend() throws Exception {
        WebSocketSession old = connect(1L);
        drainSync();
        forward.setStatus(0);
        String oldId = old.getId();
        java.util.concurrent.atomic.AtomicBoolean switched = new java.util.concurrent.atomic.AtomicBoolean();
        when(old.getId()).thenAnswer(invocation -> {
            if (switched.compareAndSet(false, true)) connect(1L);
            return oldId;
        });
        commandSessions.clear();
        GostUtil.RetainPausedService(1L, oldId, "100_20_30", 12000, null,
                "192.0.2.100:443", tunnel, "round", "eth0", "tcp");
        drainSync();
        assertTrue(switched.get());
        assertTrue(commandSessions.stream().allMatch(oldId::equals),
                "Old recovery compensation reached replacement session: " + commandSessions);
    }

    @Test
    void speedEditWaitsForReplayBeforeSavingGrantAndUpdatingServices() throws Exception {
        CountDownLatch commandStarted = new CountDownLatch(1);
        CountDownLatch releaseCommand = new CountDownLatch(1);
        commandHook = type -> {
            if (!"AddService".equals(type)) return;
            commandStarted.countDown();
            try { assertTrue(releaseCommand.await(3, TimeUnit.SECONDS)); }
            catch (InterruptedException e) { Thread.currentThread().interrupt(); throw new AssertionError(e); }
        };
        connect(1L);
        assertTrue(commandStarted.await(3, TimeUnit.SECONDS));
        UserTunnelServiceImpl editor = userTunnelEditor();
        java.util.concurrent.ExecutorService edits = java.util.concurrent.Executors.newSingleThreadExecutor();
        try {
            java.util.concurrent.Future<?> edit = edits.submit(() -> assertEquals(0, editor.updateUserTunnel(speedEdit()).getCode()));
            assertThrows(java.util.concurrent.TimeoutException.class, () -> edit.get(100, TimeUnit.MILLISECONDS),
                    "Grant save and service updates must both wait behind replay");
            assertNull(userTunnel.getSpeedId(), "Grant changed before edit acquired rule locks");
            releaseCommand.countDown();
            edit.get(3, TimeUnit.SECONDS);
            drainSync();
            assertEquals(40, userTunnel.getSpeedId());
            assertEquals("UpdateService", commands.get(commands.size() - 1).getString("type"));
            assertTrue(pausedResources.isEmpty(), "Replay compensated after a successful speed edit");
            assertEquals(1, forward.getStatus());
        } finally {
            releaseCommand.countDown();
            edits.shutdownNow();
        }
    }

    @Test
    void speedEditKeepsPausedRulePaused() throws Exception {
        forward.setStatus(0);
        resources.add("1:Service:100_20_30_tcp");
        resources.add("1:Service:100_20_30_udp");
        pausedResources.addAll(resources);
        connect(1L);
        drainSync();
        assertEquals(0, userTunnelEditor().updateUserTunnel(speedEdit()).getCode());
        JSONArray configs = command("UpdateService").getJSONArray("data");
        for (Object item : configs) {
            JSONObject cfg = (JSONObject) item;
            assertEquals("40", cfg.getString("limiter"));
            assertNotNull(cfg.getJSONObject("metadata"));
            assertTrue(cfg.getJSONObject("metadata").getBooleanValue("paused"), "Speed edit reactivated a paused service");
        }
    }

    private UserTunnelServiceImpl userTunnelEditor() {
        UserTunnelServiceImpl editor = spy(new UserTunnelServiceImpl());
        doReturn(userTunnel).when(editor).getById(any());
        doReturn(userTunnel).when(editor).getOne(any());
        doReturn(true).when(editor).updateById(any(UserTunnel.class));
        ReflectionTestUtils.setField(editor, "forwardService", context.getBean(ForwardService.class));
        ReflectionTestUtils.setField(editor, "tunnelService", context.getBean(TunnelService.class));
        ReflectionTestUtils.setField(editor, "nodeService", context.getBean(NodeService.class));
        return editor;
    }

    private UserTunnelUpdateDto speedEdit() {
        UserTunnelUpdateDto dto = new UserTunnelUpdateDto();
        dto.setId(30); dto.setSpeedId(40); dto.setFlow(100L); dto.setNum(10);
        dto.setStatus(1); dto.setExpTime(System.currentTimeMillis() + 60000); dto.setFlowResetTime(1L);
        return dto;
    }

    @Test
    void quotaPauseWaitsForReplayThenStopsTheRestoredRule() throws Exception {
        CountDownLatch commandStarted = new CountDownLatch(1);
        CountDownLatch releaseCommand = new CountDownLatch(1);
        commandHook = type -> {
            if (!"AddService".equals(type)) return;
            commandStarted.countDown();
            try { assertTrue(releaseCommand.await(3, TimeUnit.SECONDS)); }
            catch (InterruptedException e) { Thread.currentThread().interrupt(); throw new AssertionError(e); }
        };
        connect(1L);
        assertTrue(commandStarted.await(3, TimeUnit.SECONDS));
        FlowController flowController = new FlowController();
        ReflectionTestUtils.setField(flowController, "forwardService", context.getBean(ForwardService.class));
        ReflectionTestUtils.setField(flowController, "tunnelService", context.getBean(TunnelService.class));
        java.util.concurrent.ExecutorService stops = java.util.concurrent.Executors.newSingleThreadExecutor();
        try {
            java.util.concurrent.Future<?> pause = stops.submit(() ->
                    flowController.pauseService(Collections.singletonList(forward), "100_20_30"));
            assertThrows(java.util.concurrent.TimeoutException.class, () -> pause.get(100, TimeUnit.MILLISECONDS),
                    "Quota pause must serialize with the in-flight replay");
            releaseCommand.countDown();
            pause.get(3, TimeUnit.SECONDS);
            drainSync();
            assertEquals(0, forward.getStatus());
            assertEquals("PauseService", commands.get(commands.size() - 1).getString("type"));
        } finally {
            releaseCommand.countDown();
            stops.shutdownNow();
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"paused", "error", "disabledTunnel", "unrelatedNode", "portForwardExit",
            "disabledUser", "expiredUser", "userQuota", "missingUser", "missingGrant",
            "disabledGrant", "expiredGrant", "grantQuota", "missingLimiter"})
    void inactiveOrUnauthorizedRulesAreNotEnabled(String reason) throws Exception {
        switch (reason) {
            case "paused": forward.setStatus(0); break;
            case "error": forward.setStatus(-1); break;
            case "disabledTunnel": tunnel.setStatus(0); break;
            case "unrelatedNode": tunnel.setInNodeId(99L); tunnel.setOutNodeId(98L); break;
            case "portForwardExit": tunnel.setInNodeId(99L); tunnel.setOutNodeId(1L); break;
            case "disabledUser": user.setStatus(0); break;
            case "expiredUser": user.setExpTime(1L); break;
            case "userQuota": user.setInFlow(100L * 1024 * 1024 * 1024); break;
            case "missingUser": when(context.getBean(UserService.class).getById(any())).thenReturn(null); break;
            case "missingGrant": when(context.getBean(UserTunnelService.class).getOne(any())).thenReturn(null); break;
            case "disabledGrant": userTunnel.setStatus(0); break;
            case "expiredGrant": userTunnel.setExpTime(1L); break;
            case "grantQuota": userTunnel.setOutFlow(100L * 1024 * 1024 * 1024); break;
            case "missingLimiter": userTunnel.setSpeedId(40); break;
            default: fail("Unknown fixture");
        }
        connect(1L);
        drainSync();
        assertTrue(commands.isEmpty(), reason + " must not start services: " + commands);
        verify(context.getBean(ForwardMapper.class), never()).updateById(any());
    }

    @Test
    void administratorRulesDoNotRequireUserTunnelGrant() throws Exception {
        user.setRoleId(0);
        when(context.getBean(UserTunnelService.class).getOne(any())).thenReturn(null);
        connect(1L);
        drainSync();
        assertEquals("100_20_0_tcp", command("AddService").getJSONArray("data").getJSONObject(0).getString("name"));
    }

    @Test
    void repeatedReconnectDoesNotDuplicateResources() throws Exception {
        connect(1L);
        drainSync();
        command("AddService");
        commands.clear();
        connect(1L);
        drainSync();
        assertEquals(2, resources.size());
        assertEquals(2, commands.size());
        assertTrue(commands.stream().allMatch(c -> "AddService".equals(c.getString("type"))));
    }

    @Test
    void reconnectDoesNotRestartAlreadyRunningServices() throws Exception {
        resources.add("1:Service:100_20_30_tcp");
        resources.add("1:Service:100_20_30_udp");
        connect(1L);
        drainSync();
        assertTrue(commands.stream().noneMatch(c -> c.getString("type").startsWith("Update")),
                "Updating an existing GOST service closes its live connections");
    }

    @Test
    void partiallyPresentRuleStillRestoresMissingUdpService() throws Exception {
        resources.add("1:Service:100_20_30_tcp");
        connect(1L);
        drainSync();
        assertTrue(resources.contains("1:Service:100_20_30_udp"),
                "An existing TCP service must not prevent restoring UDP");
    }

    @Test
    void failedRuleDoesNotBlockOtherRulesOrChangeDatabaseStatus() throws Exception {
        Forward other = new Forward();
        org.springframework.beans.BeanUtils.copyProperties(forward, other);
        other.setId(101L);
        other.setInPort(12001);
        rejectedService = "100_20_30_tcp";
        when(context.getBean(ForwardMapper.class).selectList(any())).thenReturn(Arrays.asList(forward, other));
        when(context.getBean(ForwardMapper.class).selectById(101L)).thenReturn(other);
        connect(1L);
        drainSync();
        assertTrue(addedServices().stream().anyMatch(s -> "101_20_30_tcp".equals(s.getString("name"))));
        assertTrue(resources.contains("1:Service:101_20_30_udp"));
        assertEquals(1, forward.getStatus());
        verify(context.getBean(ForwardMapper.class), never()).updateById(any());
    }

    @ParameterizedTest
    @ValueSource(strings = {"deleted", "paused", "movedTunnel", "disabledTunnel", "movedNode", "deletedTunnel"})
    void staleQueuedSnapshotDoesNotRecreateInvalidRules(String change) throws Exception {
        Forward snapshot = new Forward();
        org.springframework.beans.BeanUtils.copyProperties(forward, snapshot);
        Forward current = new Forward();
        org.springframework.beans.BeanUtils.copyProperties(forward, current);
        Tunnel currentTunnel = new Tunnel();
        org.springframework.beans.BeanUtils.copyProperties(tunnel, currentTunnel);
        when(context.getBean(ForwardMapper.class).selectList(any())).thenReturn(Collections.singletonList(snapshot));
        when(context.getBean(ForwardMapper.class).selectById(any())).thenReturn(current);
        when(context.getBean(TunnelService.class).getById(any())).thenReturn(currentTunnel);
        switch (change) {
            case "deleted": when(context.getBean(ForwardMapper.class).selectById(any())).thenReturn(null); break;
            case "paused": current.setStatus(0); break;
            case "movedTunnel": current.setTunnelId(99); break;
            case "disabledTunnel": currentTunnel.setStatus(0); break;
            case "movedNode": currentTunnel.setInNodeId(99L); break;
            case "deletedTunnel": when(context.getBean(TunnelService.class).getById(any())).thenReturn(null); break;
            default: fail("Unknown fixture");
        }
        connect(1L);
        drainSync();
        assertTrue(commands.isEmpty(), "Must re-read queued rule state: " + change + ": " + commands);
    }

    @Test
    void syncRunsOffWebSocketCallbackThread() throws Exception {
        connect(1L);
        drainSync();
        command("AddService");
        assertTrue(commandThreads.stream().allMatch(t -> t.startsWith("node-sync-test-")), commandThreads.toString());
    }

    @Test
    void deletedNodeDoesNotTriggerReplay() throws Exception {
        when(context.getBean(NodeService.class).getById(any())).thenReturn(null);
        connect(1L);
        drainSync();
        assertTrue(commands.isEmpty());
    }

    @Test
    void administratorWebSocketDoesNotTriggerReplay() throws Exception {
        WebSocketSession session = mock(WebSocketSession.class);
        when(session.getId()).thenReturn("admin-session");
        Map<String, Object> attributes = new HashMap<>();
        attributes.put("id", "20");
        attributes.put("type", "0");
        when(session.getAttributes()).thenReturn(attributes);
        when(session.isOpen()).thenReturn(true);
        sessions.add(session);
        server.afterConnectionEstablished(session);
        drainSync();
        assertTrue(commands.isEmpty());
    }

    @Test
    void supersededQueuedConnectionDoesNotReplayRulesTwice() throws Exception {
        CountDownLatch release = new CountDownLatch(1);
        context.getBean(ThreadPoolTaskExecutor.class).submit(() -> {
            try { release.await(5, TimeUnit.SECONDS); }
            catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        });
        try {
            connect(1L);
            connect(1L);
        } finally {
            release.countDown();
        }
        drainSync();
        assertEquals(2, commands.size());
        command("AddService");
    }

    @Test
    void closedQueuedConnectionDoesNotReplayRules() throws Exception {
        CountDownLatch release = new CountDownLatch(1);
        context.getBean(ThreadPoolTaskExecutor.class).submit(() -> {
            try { release.await(5, TimeUnit.SECONDS); }
            catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        });
        try {
            WebSocketSession session = connect(1L);
            when(session.isOpen()).thenReturn(false);
            server.afterConnectionClosed(session, CloseStatus.NORMAL);
        } finally {
            release.countDown();
        }
        drainSync();
        assertTrue(commands.isEmpty());
    }

    private WebSocketSession connect(long nodeId) throws Exception {
        WebSocketSession session = mock(WebSocketSession.class);
        String sessionId = UUID.randomUUID().toString();
        when(session.getId()).thenReturn(sessionId);
        Map<String, Object> attributes = new HashMap<>();
        attributes.put("id", Long.toString(nodeId));
        attributes.put("type", "1");
        when(session.getAttributes()).thenReturn(attributes);
        when(session.isOpen()).thenReturn(true);
        doAnswer(invocation -> {
            JSONObject request = JSONObject.parseObject(((TextMessage) invocation.getArgument(0)).getPayload());
            commands.add(request);
            commandThreads.add(Thread.currentThread().getName());
            commandSessions.add(sessionId);
            String type = request.getString("type");
            if (commandHook != null) commandHook.accept(type);
            String kind = type.replace("Update", "").replace("Add", "").replace("Delete", "")
                    .replace("Pause", "").replace("Resume", "");
            List<String> names = new ArrayList<>();
            if ("DeleteService".equals(type) || "PauseService".equals(type) || "ResumeService".equals(type)) {
                for (Object name : request.getJSONObject("data").getJSONArray("services")) names.add(name.toString());
            } else if ("Service".equals(kind)) {
                for (Object item : request.getJSONArray("data")) {
                    names.add(((JSONObject) item).getString("name"));
                }
            } else {
                JSONObject data = request.getJSONObject("data");
                names.add(data.containsKey("data") ? data.getJSONObject("data").getString("name") : data.getString("name"));
            }
            String response = "OK";
            for (String name : names) {
                String key = nodeId + ":" + kind + ":" + name;
                String resourceType = "Service".equals(kind) ? "service" : "Chains".equals(kind) ? "chain" : "limiter";
                if (type.startsWith("Update") && !resources.contains(key)) {
                    response = resourceType + " " + name + " not found";
                    break;
                }
                if ((type.startsWith("Pause") || type.startsWith("Resume") || type.startsWith("Delete"))
                        && !resources.contains(key)) {
                    response = resourceType + " " + name + " not found";
                    break;
                }
                if (type.startsWith("Add") && resources.contains(key)) {
                    response = resourceType + " " + name + " already exists";
                    break;
                }
            }
            if ((type.startsWith("Add") || type.startsWith("Update")) && type.endsWith("Service") && rejectedService != null
                    && rejectedService.equals(request.getJSONArray("data").getJSONObject(0).getString("name"))) {
                response = "address already in use";
            }
            if ("OK".equals(response) && type.startsWith("Add")) {
                for (String name : names) {
                    resources.add(nodeId + ":" + kind + ":" + name);
                }
                if ("Service".equals(kind)) {
                    for (Object item : request.getJSONArray("data")) {
                        JSONObject cfg = (JSONObject) item;
                        if (cfg.getJSONObject("metadata") != null && cfg.getJSONObject("metadata").getBooleanValue("paused")) {
                            pausedResources.add(nodeId + ":Service:" + cfg.getString("name"));
                        }
                    }
                }
            }
            if ("OK".equals(response)) {
                for (String name : names) {
                    String key = nodeId + ":" + kind + ":" + name;
                    if (type.startsWith("Delete")) { resources.remove(key); pausedResources.remove(key); }
                    if (type.startsWith("Pause")) pausedResources.add(key);
                    if (type.startsWith("Resume")) pausedResources.remove(key);
                }
            }
            JSONObject reply = new JSONObject();
            reply.put("requestId", request.getString("requestId"));
            reply.put("message", response);
            server.handleTextMessage(session, new TextMessage(reply.toJSONString()));
            return null;
        }).when(session).sendMessage(any());
        sessions.add(session);
        server.afterConnectionEstablished(session);
        return session;
    }

    private JSONObject command(String type) {
        return commands.stream().filter(c -> type.equals(c.getString("type"))).findFirst()
                .orElseThrow(() -> new AssertionError("Node never received " + type + ": " + commands));
    }

    private List<JSONObject> addedServices() {
        List<JSONObject> services = new ArrayList<>();
        for (JSONObject command : commands) {
            if ("AddService".equals(command.getString("type"))) {
                for (Object service : command.getJSONArray("data")) {
                    services.add((JSONObject) service);
                }
            }
        }
        return services;
    }

    private void drainSync() throws Exception {
        context.getBean(ThreadPoolTaskExecutor.class).submit(() -> {}).get(5, TimeUnit.SECONDS);
    }

    @Configuration
    @EnableAsync
    static class TestConfig {
        @Bean ForwardService forwardService() { return new ForwardServiceImpl(); }
        @Bean WebSocketServer webSocketServer() { return new WebSocketServer(); }
        @Bean ForwardMapper forwardMapper() { return mock(ForwardMapper.class); }
        @Bean NodeService nodeService() { return mock(NodeService.class); }
        @Bean TunnelService tunnelService() { return mock(TunnelService.class); }
        @Bean UserService userService() { return mock(UserService.class); }
        @Bean UserTunnelService userTunnelService() { return mock(UserTunnelService.class); }
        @Bean SpeedLimitService speedLimitService() { return mock(SpeedLimitService.class); }
        @Bean ThreadPoolTaskExecutor taskExecutor() {
            ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
            executor.setCorePoolSize(1);
            executor.setMaxPoolSize(1);
            executor.setThreadNamePrefix("node-sync-test-");
            return executor;
        }
    }
}
