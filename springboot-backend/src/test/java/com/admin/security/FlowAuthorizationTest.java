package com.admin.security;

import com.admin.controller.FlowController;
import com.admin.entity.*;
import com.admin.service.*;
import com.baomidou.mybatisplus.core.conditions.update.UpdateWrapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class FlowAuthorizationTest {
    private final NodeService nodes = mock(NodeService.class);
    private final ForwardService forwards = mock(ForwardService.class);
    private final TunnelService tunnels = mock(TunnelService.class);
    private final UserService users = mock(UserService.class);
    private final UserTunnelService grants = mock(UserTunnelService.class);
    private final FlowController controller = new FlowController();
    private Node node;
    private User user;
    private UserTunnel grant;
    private Tunnel tunnel;

    @BeforeEach
    void setUp() {
        ReflectionTestUtils.setField(controller, "nodeService", nodes);
        ReflectionTestUtils.setField(controller, "forwardService", forwards);
        ReflectionTestUtils.setField(controller, "tunnelService", tunnels);
        ReflectionTestUtils.setField(controller, "userService", users);
        ReflectionTestUtils.setField(controller, "userTunnelService", grants);
        node = new Node(); node.setId(1L);
        when(nodes.count(any())).thenReturn(1);
        when(nodes.getOne(any())).thenReturn(node);
        Forward forward = new Forward(); forward.setId(100L); forward.setUserId(20); forward.setTunnelId(10);
        when(forwards.getById("100")).thenReturn(forward);
        tunnel = new Tunnel(); tunnel.setId(10L); tunnel.setInNodeId(1L); tunnel.setOutNodeId(2L);
        tunnel.setType(2); tunnel.setFlow(1); tunnel.setTrafficRatio(new BigDecimal("1.5"));
        when(tunnels.getById(10)).thenReturn(tunnel);
        user = new User(); user.setId(20L); user.setRoleId(1); user.setStatus(1);
        user.setInFlow(0L); user.setOutFlow(0L); user.setFlow(100L);
        when(users.getById("20")).thenReturn(user);
        grant = new UserTunnel(); grant.setId(30); grant.setUserId(20); grant.setTunnelId(10);
        grant.setFlow(100L); grant.setInFlow(0L); grant.setOutFlow(0L); grant.setStatus(1);
        when(grants.getById("30")).thenReturn(grant);
    }

    private void report(String name, String d, String u) {
        assertEquals("ok", controller.uploadFlowData(
                "{\"n\":\"" + name + "\",\"d\":" + d + ",\"u\":" + u + "}", "valid-secret"));
    }

    private void assertNoWrites() {
        verify(forwards, never()).update(isNull(), any());
        verify(users, never()).update(isNull(), any());
        verify(grants, never()).update(isNull(), any());
    }

    @Test
    void unrelatedNodeCannotChargeAnotherNodesRule() {
        node.setId(99L);
        report("100_20_30_tcp", "100", "200");
        assertNoWrites();
    }

    @ParameterizedTest
    @ValueSource(strings = {"100_999_30_tcp", "100_20_999_tcp", "100_20_0_tcp", "100_20_30_tls", "bad", "100_20_30_tcp_extra"})
    void forgedOrMalformedServiceNamesCannotMutateAccounting(String name) {
        report(name, "100", "200");
        assertNoWrites();
    }

    @Test
    void grantMustBelongToBothForwardOwnerAndTunnel() {
        grant.setUserId(999);
        report("100_20_30_tcp", "100", "200");
        assertNoWrites();
        grant.setUserId(20); grant.setTunnelId(999);
        report("100_20_30_tcp", "100", "200");
        assertNoWrites();
    }

    @ParameterizedTest
    @ValueSource(strings = {"-1", "null", "9223372036854775807"})
    void invalidOrScaledOverflowingTrafficCannotReachAnyWrite(String d) {
        report("100_20_30_tcp", d, "200");
        assertNoWrites();
    }

    @Test
    void legitimateIngressReportUsesValidatedIdsAndBillingRatio() {
        report("100_20_30_udp", "100", "200");
        ArgumentCaptor<UpdateWrapper> capture = ArgumentCaptor.forClass(UpdateWrapper.class);
        verify(users).update(isNull(), capture.capture());
        UpdateWrapper update = capture.getValue(); update.getSqlSegment();
        assertTrue(update.getParamNameValuePairs().containsValue("20"));
        assertTrue(update.getSqlSet().contains("in_flow = in_flow + 150"));
        assertTrue(update.getSqlSet().contains("out_flow = out_flow + 300"));
        verify(forwards).update(isNull(), any());
        verify(grants).update(isNull(), any());
    }

    @Test
    void administratorForwardMayUseZeroGrantButOrdinaryUsersCannot() {
        user.setRoleId(0);
        report("100_20_0_tcp", "100", "200");
        verify(users).update(isNull(), any());
        verify(grants, never()).update(isNull(), any());
    }

    @Test
    void exitTlsServiceDoesNotChargeTrafficTwice() {
        node.setId(2L);
        report("100_20_30_tls", "100", "200");
        assertNoWrites();
    }
}
