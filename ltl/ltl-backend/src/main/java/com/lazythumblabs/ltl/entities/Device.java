package com.lazythumblabs.ltl.entities;

import jakarta.persistence.*;
import java.time.LocalDateTime;

/**
 * A client device registered against a household.
 *
 * <p>{@code approved} here is a <strong>mirror for display, never an
 * authority</strong>. The home server decides, against its own database,
 * which public key it will complete a handshake with. Flipping this
 * column grants nothing — which is precisely the property that keeps LTL
 * out of the trust decision.
 */
@Entity
@Table(name = "devices")
public class Device {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "household_id", nullable = false)
    private Household household;

    @Column(name = "device_uid", nullable = false, unique = true, length = 64)
    private String deviceUid;

    @Column(nullable = false)
    private String label;

    /** Public key only, base64url uncompressed SEC1. */
    @Column(name = "public_key", nullable = false)
    private String publicKey;

    @Column(nullable = false, length = 64)
    private String fingerprint;

    @Column(length = 50)
    private String platform;

    @Column(nullable = false)
    private Boolean approved = false;

    @Column(name = "registered_at", nullable = false)
    private LocalDateTime registeredAt;

    @Column(name = "last_seen_at")
    private LocalDateTime lastSeenAt;

    @Column(name = "last_seen_country", length = 2)
    private String lastSeenCountry;

    @Column(name = "revoked_at")
    private LocalDateTime revokedAt;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;

    @PrePersist
    protected void onCreate() {
        createdAt = LocalDateTime.now();
        updatedAt = LocalDateTime.now();
        if (registeredAt == null) {
            registeredAt = LocalDateTime.now();
        }
    }

    @PreUpdate
    protected void onUpdate() {
        updatedAt = LocalDateTime.now();
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public Household getHousehold() { return household; }
    public void setHousehold(Household household) { this.household = household; }

    public String getDeviceUid() { return deviceUid; }
    public void setDeviceUid(String deviceUid) { this.deviceUid = deviceUid; }

    public String getLabel() { return label; }
    public void setLabel(String label) { this.label = label; }

    public String getPublicKey() { return publicKey; }
    public void setPublicKey(String publicKey) { this.publicKey = publicKey; }

    public String getFingerprint() { return fingerprint; }
    public void setFingerprint(String fingerprint) { this.fingerprint = fingerprint; }

    public String getPlatform() { return platform; }
    public void setPlatform(String platform) { this.platform = platform; }

    public Boolean getApproved() { return approved; }
    public void setApproved(Boolean approved) { this.approved = approved; }

    public LocalDateTime getRegisteredAt() { return registeredAt; }
    public void setRegisteredAt(LocalDateTime registeredAt) { this.registeredAt = registeredAt; }

    public LocalDateTime getLastSeenAt() { return lastSeenAt; }
    public void setLastSeenAt(LocalDateTime lastSeenAt) { this.lastSeenAt = lastSeenAt; }

    public String getLastSeenCountry() { return lastSeenCountry; }
    public void setLastSeenCountry(String lastSeenCountry) { this.lastSeenCountry = lastSeenCountry; }

    public LocalDateTime getRevokedAt() { return revokedAt; }
    public void setRevokedAt(LocalDateTime revokedAt) { this.revokedAt = revokedAt; }

    public LocalDateTime getCreatedAt() { return createdAt; }
    public LocalDateTime getUpdatedAt() { return updatedAt; }
}
