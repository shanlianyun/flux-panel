package com.admin.common.interceptor;


import com.admin.common.exception.UnauthorizedException;
import com.admin.common.utils.JwtUtil;
import com.admin.entity.User;
import com.admin.service.UserService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.util.StringUtils;
import org.springframework.web.servlet.HandlerInterceptor;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;


/**
 * JWT拦截器，验证用户是否登录
 */
public class JwtInterceptor implements HandlerInterceptor {

    @Autowired
    private UserService userService;

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        String token = request.getHeader("Authorization");

        if (!StringUtils.hasText(token)) {
            throw new UnauthorizedException("未登录或token已过期");
        }


        if (!JwtUtil.validateToken(token)) {
            throw new UnauthorizedException("无效的token或token已过期");
        }

        User user;
        try {
            user = userService == null ? null : userService.getById(JwtUtil.getUserIdFromToken(token));
        } catch (RuntimeException e) {
            throw new UnauthorizedException("无效的token或token已过期", e);
        }
        if (!JwtUtil.validateTokenForUser(token, user)) {
            throw new UnauthorizedException("账号状态已变化，请重新登录");
        }
        if (JwtUtil.requiresPasswordChange(token)
                && !request.getRequestURI().endsWith("/api/v1/user/updatePassword")) {
            throw new UnauthorizedException("首次登录必须先修改密码");
        }


        return true;
    }
}
