package com.lazythumblabs.ltl.entities;

import jakarta.persistence.*;
import java.time.LocalDateTime;

/**
 * One Domovoi server, claimed by one account.
 *
 * <p>Both key columns hold <em>public</em> keys, base64url of the
 * uncompressed SEC1 point. There is no private-key column and there will
 * not be one: the relay derives no session key and cannot open a frame.
 *
 * <p>{@code relayTokenHash} is a SHA-256 digest. The token itself is
 * shown to the household exactly once, when it claims a pairing code,
 * and is never stored — so a database dump does not yield a credential.
 */
@Entity
@Table(name = "households")
public class Household {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    /** Opaque public identifier the agent and clients address it by. */
    @Column(name = "household_uid", nullable = false, unique = true, length = 64)
    private String householdUid;

    @Column(nullable = false)
    private String name;

    /** Reported at enrollment; cosmetic, so two houses are tellable apart. */
    private String hostname;

    @Column(name = "dh_public_key", nullable = false)
    private String dhPublicKey;

    @Column(name = "sig_public_key", nullable = false)
    private String sigPublicKey;

    /**
     * The string a human compares between their dashboard and their
     * phone. Recomputed from the two keys on enrollment — never trusted
     * from the client that sent it.
     */
    @Column(nullable = false, length = 64)
    private String fingerprint;

    @Column(name = "relay_token_hash", nullable = false, unique = true, length = 64)
    private String relayTokenHash;

    @Column(nullable = false)
    private Boolean online = false;

    @Column(name = "last_seen_at")
    private LocalDateTime lastSeenAt;

    @Column(name = "agent_version", length = 50)
    private String agentVersion;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;

    @PrePersist
    protected void onCreate() {
        createdAt = LocalDateTime.now();
        updatedAt = LocalDateTime.now();
    }

    @PreUpdate
    protected void onUpdate() {
        updatedAt = LocalDateTime.now();
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public User getUser() { return user; }
    public void setUser(User user) { this.user = user; }

    public String getHouseholdUid() { return householdUid; }
    public void setHouseholdUid(String householdUid) { this.householdUid = householdUid; }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    public String getHostname() { return hostname; }
    public void setHostname(String hostname) { this.hostname = hostname; }

    public String getDhPublicKey() { return dhPublicKey; }
    public void setDhPublicKey(String dhPublicKey) { this.dhPublicKey = dhPublicKey; }

    public String getSigPublicKey() { return sigPublicKey; }
    public void setSigPublicKey(String sigPublicKey) { this.sigPublicKey = sigPublicKey; }

    public String getFingerprint() { return fingerprint; }
    public void setFingerprint(String fingerprint) { this.fingerprint = fingerprint; }

    public String getRelayTokenHash() { return relayTokenHash; }
    public void setRelayTokenHash(String relayTokenHash) { this.relayTokenHash = relayTokenHash; }

    public Boolean getOnline() { return online; }
    public void setOnline(Boolean online) { this.online = online; }

    public LocalDateTime getLastSeenAt() { return lastSeenAt; }
    public void setLastSeenAt(LocalDateTime lastSeenAt) { this.lastSeenAt = lastSeenAt; }

    public String getAgentVersion() { return agentVersion; }
    public void setAgentVersion(String agentVersion) { this.agentVersion = agentVersion; }

    public LocalDateTime getCreatedAt() { return createdAt; }
    public LocalDateTime getUpdatedAt() { return updatedAt; }
}
