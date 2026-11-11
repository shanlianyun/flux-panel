package com.admin.controller;


import com.admin.common.aop.LogAnnotation;
import com.admin.common.lang.R;
import javax.servlet.http.HttpServletResponse;

import com.admin.common.utils.PasswordUtil;
import com.admin.entity.User;
import com.admin.entity.UserTunnel;
import com.alibaba.fastjson2.JSONObject;
import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import org.springframework.web.bind.annotation.*;
import java.util.concurrent.ConcurrentHashMap;


@RestController
@CrossOrigin
@RequestMapping("/api/v1/open_api")
public class OpenApiController extends BaseController {

    private static final int MAX_ATTEMPTS_PER_WINDOW = 10;
    private static final long ATTEMPT_WINDOW_MS = 60_000L;
    private final ConcurrentHashMap<String, AttemptWindow> authAttempts = new ConcurrentHashMap<>();

    @LogAnnotation
    @GetMapping("/sub_store")
    public Object create(
            @RequestParam("user") String user,
            @RequestParam("pwd") String pwd,
            @RequestParam(value = "tunnel", required = false, defaultValue = "-1") String tunnel,
            HttpServletResponse response,
            javax.servlet.http.HttpServletRequest request) {
        JSONObject result = new JSONObject();
        result.put("upload", 0);
        result.put("download", 0);
        result.put("total", 0);
        result.put("expire", 0);
        // 校验 user 是否为空
        if (user == null || user.isEmpty()) {
            return R.err("用户不能为空");
        }
        if (pwd == null || pwd.isEmpty()) {
            return R.err("密码不能为空");
        }
        String attemptKey = user + "\u0000" + request.getRemoteAddr();
        AttemptWindow attemptWindow = authAttempts.computeIfAbsent(attemptKey, k -> new AttemptWindow());
        if (!attemptWindow.allow()) return R.err("请求过于频繁，请稍后再试");

        User userInfo = userService.getOne(new QueryWrapper<User>().eq("user", user));
        if (userInfo == null) {
            return R.err("鉴权失败");
        }

        if (!PasswordUtil.matches(pwd, userInfo.getPwd())) {
            return R.err("鉴权失败");
        }
        authAttempts.remove(attemptKey);
        response.setHeader("Cache-Control", "no-store");
        response.setHeader("Referrer-Policy", "no-referrer");

        final long GIGA = 1024L * 1024L * 1024L;
        String headerValue;

        if ("-1".equals(tunnel)) {
            headerValue = buildSubscriptionHeader(
                    userInfo.getOutFlow(),
                    userInfo.getInFlow(),
                    userInfo.getFlow() * GIGA,
                    userInfo.getExpTime() / 1000
            );
        } else {
            UserTunnel tunnelInfo = userTunnelService.getById(tunnel);
            if (tunnelInfo == null) return R.err("隧道不存在");
            if (!tunnelInfo.getUserId().toString().equals(userInfo.getId().toString())) return R.err("隧道不存在");
            headerValue = buildSubscriptionHeader(
                    tunnelInfo.getOutFlow(),
                    tunnelInfo.getInFlow(),
                    tunnelInfo.getFlow() * GIGA,
                    tunnelInfo.getExpTime() / 1000
            );
        }

        response.setHeader("subscription-userinfo", headerValue);
        return headerValue;
    }



    private String buildSubscriptionHeader(long upload, long download, long total, long expire) {
        return String.format("upload=%d; download=%d; total=%d; expire=%d", download, upload, total, expire);
    }

    private static final class AttemptWindow {
        private long startedAt = System.currentTimeMillis();
        private int count;

        synchronized boolean allow() {
            long now = System.currentTimeMillis();
            if (now - startedAt >= ATTEMPT_WINDOW_MS) { startedAt = now; count = 0; }
            return ++count <= MAX_ATTEMPTS_PER_WINDOW;
        }
    }


}
