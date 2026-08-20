package com.lazythumblabs.ltl.repositories;

import com.lazythumblabs.ltl.entities.PendingEnrollment;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;
import java.util.Optional;

@Repository
public interface PendingEnrollmentRepository extends JpaRepository<PendingEnrollment, Long> {

    Optional<PendingEnrollment> findByEnrollmentUid(String enrollmentUid);

    Optional<PendingEnrollment> findByCodeHash(String codeHash);

    /**
     * Expire stale windows. Clears {@code claimedSecret} in the same
     * statement — a relay token must not outlive the pairing it belongs
     * to just because nobody came back for it.
     */
    @Modifying
    @Query("UPDATE PendingEnrollment e SET e.status = 'expired', e.claimedSecret = NULL "
            + "WHERE e.status = 'pending' AND e.expiresAt < :now")
    int expireOlderThan(@Param("now") LocalDateTime now);

    @Modifying
    @Query("DELETE FROM PendingEnrollment e WHERE e.expiresAt < :cutoff")
    int deleteOlderThan(@Param("cutoff") LocalDateTime cutoff);
}
