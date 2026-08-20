package com.lazythumblabs.ltl.repositories;

import com.lazythumblabs.ltl.entities.EnrollmentAttempt;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;

@Repository
public interface EnrollmentAttemptRepository extends JpaRepository<EnrollmentAttempt, Long> {

    long countBySourceIpAndAttemptedAtAfter(String sourceIp, LocalDateTime since);

    @Modifying
    @Query("DELETE FROM EnrollmentAttempt a WHERE a.attemptedAt < :cutoff")
    int deleteOlderThan(@Param("cutoff") LocalDateTime cutoff);
}
