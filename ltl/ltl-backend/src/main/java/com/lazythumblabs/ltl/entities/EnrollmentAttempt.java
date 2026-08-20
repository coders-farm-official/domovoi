package com.lazythumblabs.ltl.entities;

import jakarta.persistence.*;
import java.time.LocalDateTime;

/**
 * One call to the unauthenticated enroll endpoint, recorded for rate
 * limiting.
 *
 * <p>Persisted rather than counted in memory so that restarting the
 * service does not hand an attacker a fresh budget.
 */
@Entity
@Table(name = "enrollment_attempts")
public class EnrollmentAttempt {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "source_ip", nullable = false, length = 64)
    private String sourceIp;

    @Column(name = "attempted_at", nullable = false)
    private LocalDateTime attemptedAt;

    @PrePersist
    protected void onCreate() {
        if (attemptedAt == null) {
            attemptedAt = LocalDateTime.now();
        }
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public String getSourceIp() { return sourceIp; }
    public void setSourceIp(String sourceIp) { this.sourceIp = sourceIp; }

    public LocalDateTime getAttemptedAt() { return attemptedAt; }
    public void setAttemptedAt(LocalDateTime attemptedAt) { this.attemptedAt = attemptedAt; }
}
