package com.lazythumblabs.ltl.dto.response;

import java.time.LocalDateTime;

/** Byte usage for one household in the current billing period. */
public class UsageResponse {

    private final String householdId;
    private final long bytesUsed;
    private final long bytesLimit;
    private final LocalDateTime periodEnd;

    public UsageResponse(String householdId, long bytesUsed, long bytesLimit,
                         LocalDateTime periodEnd) {
        this.householdId = householdId;
        this.bytesUsed = bytesUsed;
        this.bytesLimit = bytesLimit;
        this.periodEnd = periodEnd;
    }

    public String getHouseholdId() { return householdId; }
    public long getBytesUsed() { return bytesUsed; }
    public long getBytesLimit() { return bytesLimit; }
    public LocalDateTime getPeriodEnd() { return periodEnd; }

    /** Percent of the allowance consumed, clamped to 100. */
    public int getPercentUsed() {
        if (bytesLimit <= 0) {
            return 0;
        }
        return (int) Math.min(100, (bytesUsed * 100) / bytesLimit);
    }
}
