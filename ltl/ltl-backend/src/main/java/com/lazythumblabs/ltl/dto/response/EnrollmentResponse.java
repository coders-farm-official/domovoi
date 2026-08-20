package com.lazythumblabs.ltl.dto.response;

/**
 * What a Domovoi server gets back when it opens a pairing window.
 *
 * <p>Note what is absent: the pairing code. The server minted it and
 * kept it; LTL only ever saw a hash. There is nothing here to leak.
 */
public class EnrollmentResponse {

    private String enrollmentId;
    private String pollToken;
    private String expiresAt;

    public EnrollmentResponse(String enrollmentId, String pollToken, String expiresAt) {
        this.enrollmentId = enrollmentId;
        this.pollToken = pollToken;
        this.expiresAt = expiresAt;
    }

    public String getEnrollmentId() { return enrollmentId; }
    public String getPollToken() { return pollToken; }
    public String getExpiresAt() { return expiresAt; }
}
