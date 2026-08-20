package com.lazythumblabs.ltl.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

/**
 * What a Domovoi server sends to open a pairing window.
 *
 * <p>{@code codeHash} is a SHA-256 hex digest of the eight-word code —
 * the code itself never leaves the house. The two public keys are
 * base64url uncompressed SEC1 points; the server verifies they parse
 * before storing them, so a malformed key fails at enrollment rather
 * than at a customer's first connection attempt.
 */
public class EnrollRequest {

    @NotBlank
    @Pattern(regexp = "^[0-9a-f]{64}$", message = "must be a SHA-256 hex digest")
    private String codeHash;

    @NotBlank
    @Size(max = 255)
    private String dhPublicKey;

    @NotBlank
    @Size(max = 255)
    private String sigPublicKey;

    @NotBlank
    @Size(max = 64)
    private String fingerprint;

    @Size(max = 255)
    private String hostname;

    public String getCodeHash() { return codeHash; }
    public void setCodeHash(String codeHash) { this.codeHash = codeHash; }

    public String getDhPublicKey() { return dhPublicKey; }
    public void setDhPublicKey(String dhPublicKey) { this.dhPublicKey = dhPublicKey; }

    public String getSigPublicKey() { return sigPublicKey; }
    public void setSigPublicKey(String sigPublicKey) { this.sigPublicKey = sigPublicKey; }

    public String getFingerprint() { return fingerprint; }
    public void setFingerprint(String fingerprint) { this.fingerprint = fingerprint; }

    public String getHostname() { return hostname; }
    public void setHostname(String hostname) { this.hostname = hostname; }
}
