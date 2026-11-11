package com.admin.security;

import com.admin.common.utils.JwtUtil;
import com.admin.common.utils.PasswordUtil;
import com.admin.entity.User;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.BeforeEach;
import org.springframework.test.util.ReflectionTestUtils;

import static org.junit.jupiter.api.Assertions.*;

class PasswordSecurityTest {
    @BeforeEach
    void setUp() {
        ReflectionTestUtils.setField(JwtUtil.class, "SECRET_KEY", "password-test-only-secret");
    }

    @Test
    void bcryptPasswordHashesAreSaltedAndVerifiable() {
        String first = PasswordUtil.encode("correct horse battery staple");
        String second = PasswordUtil.encode("correct horse battery staple");
        assertNotEquals(first, second);
        assertTrue(PasswordUtil.matches("correct horse battery staple", first));
        assertFalse(PasswordUtil.matches("wrong", first));
    }

    @Test
    void legacyMd5PasswordsRemainReadableForOneLoginMigration() {
        assertTrue(PasswordUtil.isLegacy("5ebe2294ecd0e0f08eab7690d2a6ee69"));
        assertTrue(PasswordUtil.matches("secret", "5ebe2294ecd0e0f08eab7690d2a6ee69"));
    }

    @Test
    void bootstrapTokenCarriesServerEnforcedPasswordChangeFlag() {
        User user = new User(); user.setId(7L); user.setUser("admin_user"); user.setRoleId(0);
        user.setStatus(1); user.setPwd("legacy-hash");
        String token = JwtUtil.generateToken(user, true);
        assertTrue(JwtUtil.requiresPasswordChange(token));
        assertFalse(JwtUtil.requiresPasswordChange(JwtUtil.generateToken(user)));
    }
}
