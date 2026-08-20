package com.lazythumblabs.ltl.entities;

import jakarta.persistence.*;
import java.time.LocalDateTime;

/**
 * Bytes relayed for one household in one billing period.
 *
 * <p>Counting happens at the relay because the relay is the party that
 * pays for the bytes — and because it is the only counter both endpoints
 * can check against their own totals if they ever disagree.
 */
@Entity
@Table(name = "usage_periods")
public class UsagePeriod {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "household_id", nullable = false)
    private Household household;

    @Column(name = "period_start", nullable = false)
    private LocalDateTime periodStart;

    @Column(name = "period_end", nullable = false)
    private LocalDateTime periodEnd;

    @Column(name = "bytes_used", nullable = false)
    private Long bytesUsed = 0L;

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

    public Household getHousehold() { return household; }
    public void setHousehold(Household household) { this.household = household; }

    public LocalDateTime getPeriodStart() { return periodStart; }
    public void setPeriodStart(LocalDateTime periodStart) { this.periodStart = periodStart; }

    public LocalDateTime getPeriodEnd() { return periodEnd; }
    public void setPeriodEnd(LocalDateTime periodEnd) { this.periodEnd = periodEnd; }

    public Long getBytesUsed() { return bytesUsed; }
    public void setBytesUsed(Long bytesUsed) { this.bytesUsed = bytesUsed; }

    public LocalDateTime getCreatedAt() { return createdAt; }
    public LocalDateTime getUpdatedAt() { return updatedAt; }
}
