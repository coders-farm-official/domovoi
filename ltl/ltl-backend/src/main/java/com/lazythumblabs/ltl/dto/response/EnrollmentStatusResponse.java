package com.lazythumblabs.ltl.dto.response;

import com.fasterxml.jackson.annotation.JsonInclude;

/**
 * The agent's poll result: still {@code pending}, or {@code claimed}
 * with the credentials it needs.
 *
 * <p>The relay token appears in exactly one response, ever — this one,
 * on the first poll after a claim. It is cleared from the database in
 * the same transaction that serves it.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public class EnrollmentStatusResponse {

    private String status;
    private String householdId;
    private String relayToken;
    private String accountLabel;

    public static EnrollmentStatusResponse pending() {
        EnrollmentStatusResponse response = new EnrollmentStatusResponse();
        response.status = "pending";
        return response;
    }

    public static EnrollmentStatusResponse claimed(
            String householdId, String relayToken, String accountLabel) {
        EnrollmentStatusResponse response = new EnrollmentStatusResponse();
        response.status = "claimed";
        response.householdId = householdId;
        response.relayToken = relayToken;
        response.accountLabel = accountLabel;
        return response;
    }

    public String getStatus() { return status; }
    public String getHouseholdId() { return householdId; }
    public String getRelayToken() { return relayToken; }
    public String getAccountLabel() { return accountLabel; }
}
