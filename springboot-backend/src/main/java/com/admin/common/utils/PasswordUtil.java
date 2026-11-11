package com.admin.common.utils;

import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;

/** Password verification with a slow KDF and a legacy-MD5 migration path. */
public final class PasswordUtil {
    private static final BCryptPasswordEncoder ENCODER = new BCryptPasswordEncoder(12);

    private PasswordUtil() { }

    public static String encode(String rawPassword) { return ENCODER.encode(rawPassword); }

    public static boolean matches(String rawPassword, String stored) {
        if (rawPassword == null || stored == null) return false;
        if (stored.startsWith("$2a$") || stored.startsWith("$2b$") || stored.startsWith("$2y$")) {
            return ENCODER.matches(rawPassword, stored);
        }
        return stored.equals(Md5Util.md5(rawPassword));
    }

    public static boolean isLegacy(String stored) {
        return stored != null && stored.matches("[0-9a-fA-F]{32}");
    }
}
