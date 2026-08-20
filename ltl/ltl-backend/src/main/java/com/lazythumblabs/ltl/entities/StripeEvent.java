package com.lazythumblabs.ltl.entities;

import jakarta.persistence.*;
import java.time.LocalDateTime;

/**
 * A processed Stripe webhook event, recorded for idempotency.
 *
 * <p>Stripe retries. A retried {@code customer.subscription.deleted}
 * must not cancel a subscription the customer has since restarted, so
 * every event id is recorded and a repeat is acknowledged without being
 * re-applied.
 */
@Entity
@Table(name = "stripe_events")
public class StripeEvent {

    public static final String PROCESSED = "processed";
    public static final String FAILED = "failed";

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "event_id", nullable = false, unique = true)
    private String eventId;

    @Column(name = "event_type", nullable = false, length = 100)
    private String eventType;

    @Column(nullable = false, length = 20)
    private String status = PROCESSED;

    @Column(name = "error_message", columnDefinition = "TEXT")
    private String errorMessage;

    @Column(name = "received_at", nullable = false)
    private LocalDateTime receivedAt;

    @PrePersist
    protected void onCreate() {
        if (receivedAt == null) {
            receivedAt = LocalDateTime.now();
        }
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public String getEventId() { return eventId; }
    public void setEventId(String eventId) { this.eventId = eventId; }

    public String getEventType() { return eventType; }
    public void setEventType(String eventType) { this.eventType = eventType; }

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }

    public String getErrorMessage() { return errorMessage; }
    public void setErrorMessage(String errorMessage) { this.errorMessage = errorMessage; }

    public LocalDateTime getReceivedAt() { return receivedAt; }
    public void setReceivedAt(LocalDateTime receivedAt) { this.receivedAt = receivedAt; }
}
