package com.lazythumblabs.ltl.repositories;

import com.lazythumblabs.ltl.entities.RelaySession;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface RelaySessionRepository extends JpaRepository<RelaySession, Long> {

    List<RelaySession> findTop50ByHouseholdIdOrderByStartedAtDesc(Long householdId);
}
