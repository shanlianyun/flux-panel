package com.admin.common.utils;

import java.util.Collection;
import java.util.function.Supplier;

/** Bounded locks shared by replay and all rule-stop/edit paths in this panel process. */
public final class ForwardRuleLock {
    private static final Object[] LOCKS = new Object[256];
    static {
        for (int i = 0; i < LOCKS.length; i++) LOCKS[i] = new Object();
    }

    private ForwardRuleLock() {}

    public static Object forRule(Long id) {
        return LOCKS[index(id)];
    }

    // Call only from top-level edits, before taking any individual rule lock.
    public static <T> T withRules(Collection<Long> ruleIds, Supplier<T> edit) {
        int[] stripes = ruleIds.stream().mapToInt(ForwardRuleLock::index).distinct().sorted().toArray();
        return withLocks(stripes, 0, edit);
    }

    private static int index(Long id) {
        return id == null ? 0 : Math.floorMod(id.hashCode(), LOCKS.length);
    }

    private static <T> T withLocks(int[] stripes, int offset, Supplier<T> edit) {
        if (offset == stripes.length) return edit.get();
        synchronized (LOCKS[stripes[offset]]) {
            return withLocks(stripes, offset + 1, edit);
        }
    }
}
