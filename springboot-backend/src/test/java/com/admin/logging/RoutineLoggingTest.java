package com.admin.logging;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.admin.common.aop.LogAnnotation;
import com.admin.common.aop.LogAspect;
import com.admin.common.dto.GostConfigDto;
import com.admin.common.task.CheckGostConfigAsync;
import com.admin.common.utils.WebSocketServer;
import com.admin.controller.FlowController;
import com.admin.entity.Node;
import com.admin.service.ForwardService;
import com.admin.service.NodeService;
import com.admin.service.UserService;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.slf4j.LoggerFactory;
import org.springframework.aop.aspectj.annotation.AspectJProxyFactory;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.util.LinkedHashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class RoutineLoggingTest {
    private final ListAppender<ILoggingEvent> events = new ListAppender<>();
    private final Map<Logger, Level> originalLevels = new LinkedHashMap<>();
    private NodeService nodes;
    private FlowController controller;

    @BeforeEach
    void setUp() {
        events.start();
        for (Class<?> type : new Class<?>[]{LogAspect.class, FlowController.class,
                WebSocketServer.class, CheckGostConfigAsync.class}) {
            Logger logger = (Logger) LoggerFactory.getLogger(type);
            originalLevels.put(logger, logger.getLevel());
            logger.setLevel(Level.INFO);
            logger.addAppender(events);
        }
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(new MockHttpServletRequest()));
        nodes = mock(NodeService.class);
        when(nodes.count(any())).thenReturn(1);
        Node node = new Node();
        node.setId(1L);
        when(nodes.getOne(any())).thenReturn(node);
        FlowController target = new FlowController();
        ReflectionTestUtils.setField(target, "nodeService", nodes);
        ReflectionTestUtils.setField(target, "forwardService", mock(ForwardService.class));
        ReflectionTestUtils.setField(target, "userService", mock(UserService.class));
        ReflectionTestUtils.setField(target, "checkGostConfigAsync", mock(CheckGostConfigAsync.class));
        controller = proxy(target);
    }

    @AfterEach
    void tearDown() {
        RequestContextHolder.resetRequestAttributes();
        originalLevels.forEach((logger, level) -> {
            logger.detachAppender(events);
            logger.setLevel(level);
        });
        events.stop();
    }

    @Test
    void healthChecksAreSilentAtInfo() {
        assertEquals("test", controller.test());
        assertTrue(events.list.isEmpty(), "Health checks must not generate an INFO request log every 30 seconds");
    }

    @Test
    void trafficReportsAreSilentAtInfoIncludingRequestAspect() {
        assertEquals("ok", controller.uploadFlowData("{\"n\":\"100_20_0_tcp\",\"d\":100,\"u\":200}", "node-secret"));
        assertTrue(events.list.isEmpty(), "Neither controller nor aspect should log every successful traffic report at INFO");
    }

    @Test
    void configReportsAreSilentAtInfoIncludingRequestAspect() {
        assertEquals("ok", controller.config("{}", "node-secret"));
        assertTrue(events.list.isEmpty());
    }

    @Test
    void routineSuccessDiagnosticsRemainAvailableAtDebug() {
        originalLevels.keySet().forEach(logger -> logger.setLevel(Level.DEBUG));
        assertEquals("ok", controller.uploadFlowData("{\"n\":\"100_20_0_tcp\",\"d\":100,\"u\":200}", "node-secret"));
        assertTrue(events.list.stream().anyMatch(event -> event.getLevel() == Level.DEBUG));
        assertTrue(events.list.stream().noneMatch(event -> event.getLevel().isGreaterOrEqual(Level.INFO)));
    }

    @Test
    void administrativeRequestsStillLogAtInfo() {
        assertEquals("saved", proxy(new AdministrativeController()).save());
        assertTrue(events.list.stream().anyMatch(event -> event.getLevel() == Level.INFO));
    }

    @Test
    void interfaceBasedAdministrativeProxyStillLogsWithoutBreakingTheRequest() {
        AspectJProxyFactory factory = new AspectJProxyFactory(new AdministrativeImplementation());
        factory.addAspect(new LogAspect());
        AdministrativeEndpoint endpoint = factory.getProxy();
        assertEquals("saved", endpoint.save());
        assertTrue(events.list.stream().anyMatch(event -> event.getLevel() == Level.INFO));
    }

    @Test
    void routineFailuresRemainVisibleWithoutDebug() {
        assertThrows(RuntimeException.class, () -> controller.uploadFlowData("invalid-json", "node-secret"));
        assertTrue(events.list.stream().anyMatch(event -> event.getLevel().isGreaterOrEqual(Level.WARN)),
                "Suppressing routine successes must not hide failure diagnostics");
    }

    @ParameterizedTest
    @ValueSource(strings = {"{\"message\":\"routine-node-payload\"}",
            "{\"requestId\":\"completed-command\",\"message\":\"ok\"}"})
    void routineWebSocketPayloadIsNotLoggedAtInfo(String payload) throws Exception {
        WebSocketSession session = mock(WebSocketSession.class);
        when(session.getAttributes()).thenReturn(Map.of("id", "1", "type", "0"));
        new WebSocketServer().handleTextMessage(session, new TextMessage(payload));
        assertTrue(events.list.isEmpty());
    }

    @Test
    void nodeConfigurationDoesNotBypassLogLevelsThroughStdout() {
        CheckGostConfigAsync cleanup = new CheckGostConfigAsync();
        ReflectionTestUtils.setField(cleanup, "nodeService", nodes);
        PrintStream previous = System.out;
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try (PrintStream captured = new PrintStream(output)) {
            System.setOut(captured);
            cleanup.cleanNodeConfigs("1", new GostConfigDto());
        } finally {
            System.setOut(previous);
        }
        assertEquals("", output.toString(), "Configuration dumps must not bypass logging levels");
        assertTrue(events.list.isEmpty());
    }

    @SuppressWarnings("unchecked")
    private <T> T proxy(T target) {
        AspectJProxyFactory factory = new AspectJProxyFactory(target);
        factory.addAspect(new LogAspect());
        return (T) factory.getProxy();
    }

    public static class AdministrativeController {
        @LogAnnotation
        public String save() {
            return "saved";
        }
    }

    public interface AdministrativeEndpoint {
        String save();
    }

    public static class AdministrativeImplementation implements AdministrativeEndpoint {
        @Override
        @LogAnnotation
        public String save() {
            return "saved";
        }
    }
}
