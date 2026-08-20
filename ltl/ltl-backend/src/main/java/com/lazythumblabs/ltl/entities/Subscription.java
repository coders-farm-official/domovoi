package com.lazythumblabs.ltl.entities;

import jakarta.persistence.*;
import java.time.LocalDateTime;

/**
 * One account's subscription.
 *
 * <p>{@code status} deliberately mirrors Stripe's vocabulary
 * (trialing / active / past_due / canceled) so a webhook maps straight
 * across without a translation table that could drift.
 */
@Entity
@Table(name = "subscriptions")
public class Subscription {

    public static final String TRIALING = "trialing";
    public static final String ACTIVE = "active";
    public static final String PAST_DUE = "past_due";
    public static final String CANCELED = "canceled";

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "user_id", nullable = false, unique = true)
    private User user;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "plan_id", nullable = false)
    private Plan plan;

    @Column(name = "stripe_subscription_id", unique = true)
    private String stripeSubscriptionId;

    @Column(length = 20, nullable = false)
    private String status = ACTIVE;

    @Column(name = "cancel_at_period_end", nullable = false)
    private Boolean cancelAtPeriodEnd = false;

    @Column(name = "current_period_start")
    private LocalDateTime currentPeriodStart;

    @Column(name = "current_period_end")
    private LocalDateTime currentPeriodEnd;

    /**
     * Set when an invoice fails. Until it passes, the tunnel keeps
     * working and the dashboard shows a banner — a card that expires
     * while someone is away must not lock them out of their own house.
     */
    @Column(name = "grace_until")
    private LocalDateTime graceUntil;

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

    public Plan getPlan() { return plan; }
    public void setPlan(Plan plan) { this.plan = plan; }

    public String getStripeSubscriptionId() { return stripeSubscriptionId; }
    public void setStripeSubscriptionId(String stripeSubscriptionId) { this.stripeSubscriptionId = stripeSubscriptionId; }

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }

    public Boolean getCancelAtPeriodEnd() { return cancelAtPeriodEnd; }
    public void setCancelAtPeriodEnd(Boolean cancelAtPeriodEnd) { this.cancelAtPeriodEnd = cancelAtPeriodEnd; }

    public LocalDateTime getCurrentPeriodStart() { return currentPeriodStart; }
    public void setCurrentPeriodStart(LocalDateTime currentPeriodStart) { this.currentPeriodStart = currentPeriodStart; }

    public LocalDateTime getCurrentPeriodEnd() { return currentPeriodEnd; }
    public void setCurrentPeriodEnd(LocalDateTime currentPeriodEnd) { this.currentPeriodEnd = currentPeriodEnd; }

    public LocalDateTime getGraceUntil() { return graceUntil; }
    public void setGraceUntil(LocalDateTime graceUntil) { this.graceUntil = graceUntil; }

    public LocalDateTime getCreatedAt() { return createdAt; }
    public LocalDateTime getUpdatedAt() { return updatedAt; }
}
