package com.admin.security;

import com.admin.common.exception.UnauthorizedException;
import com.admin.common.interceptor.JwtInterceptor;
import com.admin.common.utils.JwtUtil;
import com.admin.common.utils.WebSocketServer;
import com.admin.config.WebSocketInterceptor;
import com.admin.entity.User;
import com.admin.service.NodeService;
import com.admin.service.UserService;
import org.junit.jupiter.api.*;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.http.server.ServletServerHttpRequest;
import org.springframework.http.server.ServletServerHttpResponse;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.socket.*;

import java.util.HashMap;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class AccountAuthorizationTest {
    private AnnotationConfigApplicationContext context;
    private UserService users;
    private User user;
    private JwtInterceptor http;
    private WebSocketInterceptor socket;

    @BeforeEach
    void setUp() {
        ReflectionTestUtils.setField(JwtUtil.class, "SECRET_KEY", "security-test-only-secret-key");
        users = mock(UserService.class);
        user = new User(); user.setId(20L); user.setUser("operator"); user.setPwd("current-password-hash");
        user.setStatus(1); user.setRoleId(0);
        when(users.getById(any())).thenAnswer(invocation -> user);
        context = new AnnotationConfigApplicationContext();
        context.registerBean(UserService.class, () -> users);
        context.registerBean(NodeService.class, () -> mock(NodeService.class));
        context.scan("com.admin.security");
        context.refresh();
        http = wired(new JwtInterceptor());
        socket = wired(new WebSocketInterceptor());
    }

    private <T> T wired(T bean) {
        context.getAutowireCapableBeanFactory().autowireBean(bean);
        return bean;
    }

    @AfterEach
    void tearDown() {
        ((Set<?>) ReflectionTestUtils.getField(WebSocketServer.class, "activeSessions")).clear();
        context.close();
        ReflectionTestUtils.setField(JwtUtil.class, "SECRET_KEY", null);
    }

    private boolean authorize(String token) {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setRequestURI("/api/v1/node/list");
        request.addHeader("Authorization", token);
        return http.preHandle(request, new MockHttpServletResponse(), new Object());
    }

    private Map<String, Object> handshake(String token, boolean expected) throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setParameter("type", "0"); request.setParameter("secret", token);
        Map<String, Object> attributes = new HashMap<>();
        assertEquals(expected, socket.beforeHandshake(new ServletServerHttpRequest(request),
                new ServletServerHttpResponse(new MockHttpServletResponse()), null, attributes));
        return attributes;
    }

    @Test
    void activeAccountCanUseItsCurrentToken() throws Exception {
        String token = JwtUtil.generateToken(user);
        assertTrue(authorize(token));
        assertEquals(20L, handshake(token, true).get("id"));
    }

    @Test
    void passwordChangeImmediatelyRevokesOldToken() {
        String token = JwtUtil.generateToken(user);
        user.setPwd("new-password-hash");
        assertThrows(UnauthorizedException.class, () -> authorize(token));
        assertTrue(authorize(JwtUtil.generateToken(user)));
    }

    @Test
    void deletedAccountCannotUseAnOtherwiseValidToken() {
        String token = JwtUtil.generateToken(user);
        when(users.getById(any())).thenReturn(null);
        assertThrows(UnauthorizedException.class, () -> authorize(token));
    }

    @Test
    void disabledAccountCannotKeepAccessingThePanel() throws Exception {
        String token = JwtUtil.generateToken(user);
        user.setStatus(0);
        assertThrows(UnauthorizedException.class, () -> authorize(token));
        handshake(token, false);
    }

    @Test
    void demotedAdministratorCannotReuseOldAdminClaims() throws Exception {
        String token = JwtUtil.generateToken(user);
        user.setRoleId(1);
        assertThrows(UnauthorizedException.class, () -> authorize(token));
        handshake(token, false);
    }

    @Test
    void ordinaryUserCannotEnterAdministratorMonitoring() throws Exception {
        user.setRoleId(1);
        String token = JwtUtil.generateToken(user);
        assertTrue(authorize(token));
        handshake(token, false);
    }

    @Test
    void openMonitoringSessionStopsReceivingDataAfterAccountRevocation() throws Exception {
        Map<String, Object> attributes = handshake(JwtUtil.generateToken(user), true);
        WebSocketSession subscriber = mock(WebSocketSession.class);
        when(subscriber.getId()).thenReturn("audit-subscriber");
        when(subscriber.isOpen()).thenReturn(true);
        when(subscriber.getAttributes()).thenReturn(attributes);
        WebSocketServer server = wired(new WebSocketServer());
        server.afterConnectionEstablished(subscriber);
        WebSocketSession node = mock(WebSocketSession.class);
        when(node.getAttributes()).thenReturn(Map.of("id", 1L, "type", "1"));
        server.handleTextMessage(node, new TextMessage("{\"cpu_usage\":1}"));
        verify(subscriber).sendMessage(any(TextMessage.class));
        clearInvocations(subscriber);
        user.setStatus(0);
        server.handleTextMessage(node, new TextMessage("{\"cpu_usage\":2}"));
        verify(subscriber, never()).sendMessage(any());
        verify(subscriber).close(CloseStatus.POLICY_VIOLATION);
    }
}
