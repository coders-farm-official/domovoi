package com.lazythumblabs.ltl.services;

import java.time.LocalDateTime;

/**
 * The answer to one question — <em>may this household do this right
 * now</em> — computed in one place so nothing else has to reason about
 * subscription state.
 *
 * @param active      whether connections are permitted at all
 * @param reason      a relay close-reason code when {@code active} is false
 * @param planCode    the plan in force
 * @param bytesUsed   bytes relayed so far this period
 * @param bytesLimit  the period allowance; 0 means unmetered
 * @param deviceLimit devices the plan permits
 * @param periodEnd   when the current period rolls over
 */
public record Entitlement(
        boolean active,
        String reason,
        String planCode,
        long bytesUsed,
        long bytesLimit,
        int deviceLimit,
        LocalDateTime periodEnd) {

    /**
     * Whether a NEW stream may start.
     *
     * <p>Separate from {@link #active} on purpose: a household at its
     * cap keeps its existing link and its control traffic, so the
     * dashboard can still load the page that explains why streaming
     * stopped. Cutting everything off at 100% would mean the customer's
     * first symptom is a blank screen.
     */
    public boolean withinQuota() {
        return bytesLimit <= 0 || bytesUsed < bytesLimit;
    }

    public int percentUsed() {
        if (bytesLimit <= 0) {
            return 0;
        }
        return (int) Math.min(100, (bytesUsed * 100) / bytesLimit);
    }
}
