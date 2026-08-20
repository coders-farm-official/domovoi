package com.lazythumblabs.ltl.entities;

import jakarta.persistence.*;
import java.time.LocalDateTime;

/** One Lazy Thumb Labs account. Identity comes from Stytch; billing identity from Stripe. */
@Entity
@Table(name = "users")
public class User {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "stytch_user_id", unique = true)
    private String stytchUserId;

    @Column(nullable = false, unique = true)
    private String email;

    @Column(name = "display_name")
    private String displayName;

    @Column(name = "stripe_customer_id", unique = true)
    private String stripeCustomerId;

    @Column(nullable = false)
    private Boolean disabled = false;

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

    public String getStytchUserId() { return stytchUserId; }
    public void setStytchUserId(String stytchUserId) { this.stytchUserId = stytchUserId; }

    public String getEmail() { return email; }
    public void setEmail(String email) { this.email = email; }

    public String getDisplayName() { return displayName; }
    public void setDisplayName(String displayName) { this.displayName = displayName; }

    public String getStripeCustomerId() { return stripeCustomerId; }
    public void setStripeCustomerId(String stripeCustomerId) { this.stripeCustomerId = stripeCustomerId; }

    public Boolean getDisabled() { return disabled; }
    public void setDisabled(Boolean disabled) { this.disabled = disabled; }

    public LocalDateTime getCreatedAt() { return createdAt; }
    public LocalDateTime getUpdatedAt() { return updatedAt; }
}
