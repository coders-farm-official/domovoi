package com.lazythumblabs.ltl.services;

import com.lazythumblabs.ltl.entities.Household;
import com.lazythumblabs.ltl.entities.Plan;
import com.lazythumblabs.ltl.entities.Subscription;
import com.lazythumblabs.ltl.repositories.PlanRepository;
import com.lazythumblabs.ltl.repositories.SubscriptionRepository;
import com.lazythumblabs.ltl.relay.RelayFrames;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.Optional;

/**
 * The single place where "is this customer paid up" is decided.
 *
 * <p>The relay consults this at three moments — when an agent connects,
 * when a client link opens, and when a period's byte counter crosses its
 * limit. Nothing else is allowed to reason about subscription state, so
 * there is exactly one implementation of the rules and exactly one place
 * to change them.
 */
@Service
public class EntitlementService {

    private static final Logger logger = LoggerFactory.getLogger(EntitlementService.class);

    private static final String FREE_PLAN_CODE = "free";

    @Autowired
    private SubscriptionRepository subscriptionRepository;

    @Autowired
    private PlanRepository planRepository;

    @Autowired
    private UsageService usageService;

    /**
     * The plan in force for an account, free tier included.
     *
     * <p>Exposed so callers enforcing a plan limit read it from the
     * {@code plans} row rather than hardcoding a number that would then
     * disagree with what the pricing page shows.
     */
    @Transactional(readOnly = true)
    public Plan planFor(Long userId) {
        return subscriptionRepository.findByUserId(userId)
                .map(Subscription::getPlan)
                .orElseGet(this::freePlan);
    }

    @Transactional(readOnly = true)
    public Entitlement resolve(Household household) {
        Long userId = household.getUser().getId();
        Optional<Subscription> maybeSubscription = subscriptionRepository.findByUserId(userId);

        Plan plan = maybeSubscription
                .map(Subscription::getPlan)
                .orElseGet(this::freePlan);

        long used = usageService.bytesUsedThisPeriod(household);
        LocalDateTime periodEnd = maybeSubscription
                .map(Subscription::getCurrentPeriodEnd)
                .orElse(null);

        if (maybeSubscription.isEmpty()) {
            // No subscription row at all: the free tier. Not an error —
            // signing up and pairing a house before choosing a plan is
            // the intended first-run path.
            return new Entitlement(true, null, plan.getCode(), used,
                    plan.getMonthlyBytes(), plan.getDeviceLimit(), periodEnd);
        }

        Subscription subscription = maybeSubscription.get();
        String status = subscription.getStatus();

        if (Subscription.ACTIVE.equals(status) || Subscription.TRIALING.equals(status)) {
            return new Entitlement(true, null, plan.getCode(), used,
                    plan.getMonthlyBytes(), plan.getDeviceLimit(), periodEnd);
        }

        if (Subscription.PAST_DUE.equals(status)) {
            LocalDateTime graceUntil = subscription.getGraceUntil();
            boolean inGrace = graceUntil != null && graceUntil.isAfter(LocalDateTime.now());
            if (inGrace) {
                // Still working, with a banner. A card that expires while
                // someone is away must not lock them out of their house.
                return new Entitlement(true, null, plan.getCode(), used,
                        plan.getMonthlyBytes(), plan.getDeviceLimit(), periodEnd);
            }
            return inactive(plan, used, periodEnd);
        }

        return inactive(plan, used, periodEnd);
    }

    private Entitlement inactive(Plan plan, long used, LocalDateTime periodEnd) {
        return new Entitlement(false, RelayFrames.ERR_SUBSCRIPTION_INACTIVE,
                plan.getCode(), used, plan.getMonthlyBytes(), plan.getDeviceLimit(), periodEnd);
    }

    /**
     * The free plan, which every account falls back to.
     *
     * <p>If it is somehow missing from the database, fall back to a
     * permissive in-memory plan rather than locking every customer out.
     * A seeding mistake should page an operator, not sever a household's
     * link to their own house.
     */
    private Plan freePlan() {
        return planRepository.findByCode(FREE_PLAN_CODE).orElseGet(() -> {
            logger.error("plan '{}' is missing from the database; using a fallback",
                    FREE_PLAN_CODE);
            Plan fallback = new Plan();
            fallback.setCode(FREE_PLAN_CODE);
            fallback.setName("Free");
            fallback.setMonthlyBytes(2L * 1024 * 1024 * 1024);
            fallback.setDeviceLimit(2);
            fallback.setHouseholdLimit(1);
            return fallback;
        });
    }
}
