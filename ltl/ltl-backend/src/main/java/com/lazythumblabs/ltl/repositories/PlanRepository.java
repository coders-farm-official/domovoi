package com.lazythumblabs.ltl.repositories;

import com.lazythumblabs.ltl.entities.Plan;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface PlanRepository extends JpaRepository<Plan, Long> {

    Optional<Plan> findByCode(String code);

    Optional<Plan> findByStripePriceId(String stripePriceId);

    List<Plan> findByActiveTrueOrderBySortOrderAsc();
}
