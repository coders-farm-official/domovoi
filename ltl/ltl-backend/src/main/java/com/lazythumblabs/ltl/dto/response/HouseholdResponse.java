package com.lazythumblabs.ltl.dto.response;

import com.lazythumblabs.ltl.entities.Household;

import java.time.LocalDateTime;

/**
 * A household as the web app sees it.
 *
 * <p>{@code dhPublicKey} is here because the client genuinely needs it:
 * the end-to-end handshake agrees against the household's static key,
 * and a device cannot start one without holding it first. It is a
 * <em>public</em> key, so shipping it to the account that owns the
 * household costs nothing — and the value a human is asked to compare
 * stays the fingerprint, which is short enough to actually read.
 *
 * <p>The signing key is not included. Only the relay verifies against
 * it, and a browser has no use for it.
 */
public class HouseholdResponse {

    private String householdId;
    private String name;
    private String hostname;
    private String fingerprint;
    private String dhPublicKey;
    private boolean online;
    private LocalDateTime lastSeenAt;
    private String agentVersion;
    private LocalDateTime createdAt;

    public static HouseholdResponse from(Household household) {
        HouseholdResponse response = new HouseholdResponse();
        response.householdId = household.getHouseholdUid();
        response.name = household.getName();
        response.hostname = household.getHostname();
        response.fingerprint = household.getFingerprint();
        response.dhPublicKey = household.getDhPublicKey();
        response.online = Boolean.TRUE.equals(household.getOnline());
        response.lastSeenAt = household.getLastSeenAt();
        response.agentVersion = household.getAgentVersion();
        response.createdAt = household.getCreatedAt();
        return response;
    }

    public String getHouseholdId() { return householdId; }
    public String getName() { return name; }
    public String getHostname() { return hostname; }
    public String getFingerprint() { return fingerprint; }
    public String getDhPublicKey() { return dhPublicKey; }
    public boolean isOnline() { return online; }
    public LocalDateTime getLastSeenAt() { return lastSeenAt; }
    public String getAgentVersion() { return agentVersion; }
    public LocalDateTime getCreatedAt() { return createdAt; }
}
