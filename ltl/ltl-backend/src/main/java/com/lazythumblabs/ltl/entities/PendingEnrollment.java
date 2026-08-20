package com.lazythumblabs.ltl.entities;

import jakarta.persistence.*;
import java.time.LocalDateTime;

/**
 * An open pairing window.
 *
 * <p>The home server mints an eight-word code and sends only its
 * <em>hash</em>. That ordering is the load-bearing property: nothing in
 * this table reverses to the words a user types, so a dump of it cannot
 * be replayed into a claim.
 */
@Entity
@Table(name = "pending_enrollments")
public class PendingEnrollment {

    public static final String PENDING = "pending";
    public static final String CLAIMED = "claimed";
    public static final String EXPIRED = "expired";

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "enrollment_uid", nullable = false, unique = true, length = 64)
    private String enrollmentUid;

    @Column(name = "code_hash", nullable = false, unique = true, length = 64)
    private String codeHash;

    @Column(name = "poll_token_hash", nullable = false, length = 64)
    private String pollTokenHash;

    @Column(name = "dh_public_key", nullable = false)
    private String dhPublicKey;

    @Column(name = "sig_public_key", nullable = false)
    private String sigPublicKey;

    @Column(nullable = false, length = 64)
    private String fingerprint;

    private String hostname;

    @Column(length = 20, nullable = false)
    private String status = PENDING;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "household_id")
    private Household household;

    /**
     * The relay token, parked here only between the moment a user claims
     * the code and the agent's next poll, then cleared.
     *
     * <p>It exists because the two halves of pairing are asynchronous:
     * the person typing the code and the server waiting for it are not
     * in the same request, so the token has to live somewhere for a few
     * seconds. Cleared on handoff and on expiry.
     */
    @Column(name = "claimed_secret")
    private String claimedSecret;

    @Column(name = "source_ip", length = 64)
    private String sourceIp;

    @Column(name = "expires_at", nullable = false)
    private LocalDateTime expiresAt;

    @Column(name = "claimed_at")
    private LocalDateTime claimedAt;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @PrePersist
    protected void onCreate() {
        createdAt = LocalDateTime.now();
    }

    @Transient
    public boolean isExpired() {
        return expiresAt != null && expiresAt.isBefore(LocalDateTime.now());
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public String getEnrollmentUid() { return enrollmentUid; }
    public void setEnrollmentUid(String enrollmentUid) { this.enrollmentUid = enrollmentUid; }

    public String getCodeHash() { return codeHash; }
    public void setCodeHash(String codeHash) { this.codeHash = codeHash; }

    public String getPollTokenHash() { return pollTokenHash; }
    public void setPollTokenHash(String pollTokenHash) { this.pollTokenHash = pollTokenHash; }

    public String getDhPublicKey() { return dhPublicKey; }
    public void setDhPublicKey(String dhPublicKey) { this.dhPublicKey = dhPublicKey; }

    public String getSigPublicKey() { return sigPublicKey; }
    public void setSigPublicKey(String sigPublicKey) { this.sigPublicKey = sigPublicKey; }

    public String getFingerprint() { return fingerprint; }
    public void setFingerprint(String fingerprint) { this.fingerprint = fingerprint; }

    public String getHostname() { return hostname; }
    public void setHostname(String hostname) { this.hostname = hostname; }

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }

    public Household getHousehold() { return household; }
    public void setHousehold(Household household) { this.household = household; }

    public String getClaimedSecret() { return claimedSecret; }
    public void setClaimedSecret(String claimedSecret) { this.claimedSecret = claimedSecret; }

    public String getSourceIp() { return sourceIp; }
    public void setSourceIp(String sourceIp) { this.sourceIp = sourceIp; }

    public LocalDateTime getExpiresAt() { return expiresAt; }
    public void setExpiresAt(LocalDateTime expiresAt) { this.expiresAt = expiresAt; }

    public LocalDateTime getClaimedAt() { return claimedAt; }
    public void setClaimedAt(LocalDateTime claimedAt) { this.claimedAt = claimedAt; }

    public LocalDateTime getCreatedAt() { return createdAt; }
}
