package com.lazythumblabs.ltl.entities;

import jakarta.persistence.*;
import java.time.LocalDateTime;

/**
 * One connection's audit record: who connected, from roughly where, for
 * how long, and how many bytes moved.
 *
 * <p>Metadata only — no paths, no headers, no bodies. The relay never
 * parses them, so there is nothing to retain even by accident.
 */
@Entity
@Table(name = "relay_sessions")
public class RelaySession {

    public static final String KIND_AGENT = "agent";
    public static final String KIND_CLIENT = "client";

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "household_id", nullable = false)
    private Household household;

    /** Null for an agent session; set for a client link. */
    @Column(name = "device_uid", length = 64)
    private String deviceUid;

    @Column(nullable = false, length = 10)
    private String kind;

    @Column(name = "ip_country", length = 2)
    private String ipCountry;

    @Column(name = "bytes_in", nullable = false)
    private Long bytesIn = 0L;

    @Column(name = "bytes_out", nullable = false)
    private Long bytesOut = 0L;

    @Column(name = "close_reason", length = 50)
    private String closeReason;

    @Column(name = "started_at", nullable = false)
    private LocalDateTime startedAt;

    @Column(name = "ended_at")
    private LocalDateTime endedAt;

    @PrePersist
    protected void onCreate() {
        if (startedAt == null) {
            startedAt = LocalDateTime.now();
        }
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public Household getHousehold() { return household; }
    public void setHousehold(Household household) { this.household = household; }

    public String getDeviceUid() { return deviceUid; }
    public void setDeviceUid(String deviceUid) { this.deviceUid = deviceUid; }

    public String getKind() { return kind; }
    public void setKind(String kind) { this.kind = kind; }

    public String getIpCountry() { return ipCountry; }
    public void setIpCountry(String ipCountry) { this.ipCountry = ipCountry; }

    public Long getBytesIn() { return bytesIn; }
    public void setBytesIn(Long bytesIn) { this.bytesIn = bytesIn; }

    public Long getBytesOut() { return bytesOut; }
    public void setBytesOut(Long bytesOut) { this.bytesOut = bytesOut; }

    public String getCloseReason() { return closeReason; }
    public void setCloseReason(String closeReason) { this.closeReason = closeReason; }

    public LocalDateTime getStartedAt() { return startedAt; }
    public void setStartedAt(LocalDateTime startedAt) { this.startedAt = startedAt; }

    public LocalDateTime getEndedAt() { return endedAt; }
    public void setEndedAt(LocalDateTime endedAt) { this.endedAt = endedAt; }
}
