package com.lazythumblabs.ltl.entities;

import jakarta.persistence.*;
import java.time.LocalDateTime;

/**
 * A subscription tier.
 *
 * <p>Plans are rows rather than enum constants so pricing and limits can
 * change without a deploy. {@code monthlyBytes} is the axis that matters:
 * relaying bytes is the only real marginal cost of the service.
 */
@Entity
@Table(name = "plans")
public class Plan {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, unique = true, length = 50)
    private String code;

    @Column(nullable = false)
    private String name;

    @Column(columnDefinition = "TEXT")
    private String description;

    /** Null for the free plan, which has no Stripe price at all. */
    @Column(name = "stripe_price_id")
    private String stripePriceId;

    @Column(name = "monthly_bytes", nullable = false)
    private Long monthlyBytes;

    @Column(name = "device_limit", nullable = false)
    private Integer deviceLimit = 2;

    @Column(name = "household_limit", nullable = false)
    private Integer householdLimit = 1;

    @Column(name = "price_cents", nullable = false)
    private Integer priceCents = 0;

    @Column(nullable = false, length = 3)
    private String currency = "usd";

    @Column(nullable = false)
    private Boolean active = true;

    @Column(name = "sort_order", nullable = false)
    private Integer sortOrder = 0;

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

    public String getCode() { return code; }
    public void setCode(String code) { this.code = code; }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }

    public String getStripePriceId() { return stripePriceId; }
    public void setStripePriceId(String stripePriceId) { this.stripePriceId = stripePriceId; }

    public Long getMonthlyBytes() { return monthlyBytes; }
    public void setMonthlyBytes(Long monthlyBytes) { this.monthlyBytes = monthlyBytes; }

    public Integer getDeviceLimit() { return deviceLimit; }
    public void setDeviceLimit(Integer deviceLimit) { this.deviceLimit = deviceLimit; }

    public Integer getHouseholdLimit() { return householdLimit; }
    public void setHouseholdLimit(Integer householdLimit) { this.householdLimit = householdLimit; }

    public Integer getPriceCents() { return priceCents; }
    public void setPriceCents(Integer priceCents) { this.priceCents = priceCents; }

    public String getCurrency() { return currency; }
    public void setCurrency(String currency) { this.currency = currency; }

    public Boolean getActive() { return active; }
    public void setActive(Boolean active) { this.active = active; }

    public Integer getSortOrder() { return sortOrder; }
    public void setSortOrder(Integer sortOrder) { this.sortOrder = sortOrder; }

    public LocalDateTime getCreatedAt() { return createdAt; }
    public LocalDateTime getUpdatedAt() { return updatedAt; }
}
