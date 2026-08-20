package com.lazythumblabs.ltl.repositories;

import com.lazythumblabs.ltl.entities.Household;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface HouseholdRepository extends JpaRepository<Household, Long> {

    Optional<Household> findByHouseholdUid(String householdUid);

    /**
     * The agent-authentication lookup. Keyed on the token HASH, never the
     * token, so a database dump yields nothing an attacker can present.
     */
    Optional<Household> findByRelayTokenHash(String relayTokenHash);

    List<Household> findByUserIdOrderByCreatedAtAsc(Long userId);

    long countByUserId(Long userId);
}
