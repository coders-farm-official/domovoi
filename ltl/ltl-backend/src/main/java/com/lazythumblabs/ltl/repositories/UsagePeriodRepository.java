package com.lazythumblabs.ltl.repositories;

import com.lazythumblabs.ltl.entities.UsagePeriod;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;
import java.util.Optional;

@Repository
public interface UsagePeriodRepository extends JpaRepository<UsagePeriod, Long> {

    Optional<UsagePeriod> findByHouseholdIdAndPeriodStart(Long householdId, LocalDateTime periodStart);

    /**
     * The household's most recent period.
     *
     * <p>Used by the metering flush, which holds a household id and a
     * byte delta but no notion of billing boundaries — asking the
     * database which period is current is cheaper and more correct than
     * recomputing one from a subscription on every flush.
     */
    Optional<UsagePeriod> findTopByHouseholdIdOrderByPeriodStartDesc(Long householdId);

    /**
     * Add a delta atomically in the database rather than read-modify-write
     * in Java. Several relay connections for one household flush
     * concurrently, and a lost update here is a customer billed for less
     * (or more) than they used.
     */
    @Modifying
    @Query("UPDATE UsagePeriod u SET u.bytesUsed = u.bytesUsed + :delta, u.updatedAt = :now "
            + "WHERE u.id = :id")
    int addBytes(@Param("id") Long id, @Param("delta") long delta, @Param("now") LocalDateTime now);
}
