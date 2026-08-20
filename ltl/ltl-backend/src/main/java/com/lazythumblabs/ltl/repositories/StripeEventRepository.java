package com.lazythumblabs.ltl.repositories;

import com.lazythumblabs.ltl.entities.StripeEvent;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

@Repository
public interface StripeEventRepository extends JpaRepository<StripeEvent, Long> {

    boolean existsByEventId(String eventId);
}
