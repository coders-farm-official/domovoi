package com.lazythumblabs.ltl.services;

import com.lazythumblabs.ltl.entities.Household;
import com.lazythumblabs.ltl.entities.Subscription;
import com.lazythumblabs.ltl.entities.UsagePeriod;
import com.lazythumblabs.ltl.repositories.SubscriptionRepository;
import com.lazythumblabs.ltl.repositories.UsagePeriodRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.time.temporal.ChronoUnit;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Byte metering.
 *
 * <p>Counters live in memory and are flushed on a cadence, not per
 * frame: a database write per relayed frame would cost more than the
 * bytes it was counting. The trade is bounded — at most one flush
 * interval of usage is lost if the process dies, which is the right side
 * of the accuracy/cost line for a metered relay.
 */
@Service
public class UsageService {

    private static final Logger logger = LoggerFactory.getLogger(UsageService.class);

    @Autowired
    private UsagePeriodRepository usagePeriodRepository;

    @Autowired
    private SubscriptionRepository subscriptionRepository;

    /** householdId → bytes not yet written to the database. */
    private final Map<Long, AtomicLong> pending = new ConcurrentHashMap<>();

    /** householdId → running total for the current period. */
    private final Map<Long, AtomicLong> cachedTotals = new ConcurrentHashMap<>();

    /** Record relayed bytes. Called on the hot path; stays allocation-light. */
    public void record(Long householdId, long bytes) {
        if (householdId == null || bytes <= 0) {
            return;
        }
        pending.computeIfAbsent(householdId, id -> new AtomicLong()).addAndGet(bytes);
        AtomicLong total = cachedTotals.get(householdId);
        if (total != null) {
            total.addAndGet(bytes);
        }
    }

    /**
     * Bytes used this period, including counts not yet flushed.
     *
     * <p>Reads the database once per household per process lifetime and
     * then tracks increments in memory — an entitlement check runs on
     * every link open, and it should not become a query.
     */
    @Transactional
    public long bytesUsedThisPeriod(Household household) {
        AtomicLong cached = cachedTotals.get(household.getId());
        if (cached != null) {
            return cached.get();
        }
        long stored = currentPeriod(household).getBytesUsed();
        // Fold in anything recorded before the first read, so a burst of
        // traffic during startup is not silently forgiven.
        AtomicLong unflushed = pending.get(household.getId());
        long total = stored + (unflushed == null ? 0 : unflushed.get());
        cachedTotals.put(household.getId(), new AtomicLong(total));
        return total;
    }

    /**
     * Flush pending deltas to the database.
     *
     * <p>Each delta is applied with an atomic SQL {@code UPDATE … SET
     * bytes_used = bytes_used + :delta} rather than a read-modify-write:
     * several relay connections for one household flush concurrently,
     * and a lost update here is a customer billed for the wrong number.
     */
    @Scheduled(fixedDelayString = "${ltl.relay.usage-flush-millis:30000}")
    @Transactional
    public void flush() {
        for (Map.Entry<Long, AtomicLong> entry : pending.entrySet()) {
            Long householdId = entry.getKey();
            long delta = entry.getValue().getAndSet(0);
            if (delta <= 0) {
                continue;
            }
            try {
                Optional<UsagePeriod> period = usagePeriodRepository
                        .findTopByHouseholdIdOrderByPeriodStartDesc(householdId);
                if (period.isEmpty()) {
                    // No period row yet — the household has never had one
                    // created. Hold the bytes; currentPeriod() will make
                    // the row on the next entitlement check.
                    entry.getValue().addAndGet(delta);
                    continue;
                }
                usagePeriodRepository.addBytes(period.get().getId(), delta, LocalDateTime.now());
            } catch (RuntimeException e) {
                // Put the bytes back so a transient database problem does
                // not silently give away a customer's allowance.
                entry.getValue().addAndGet(delta);
                logger.warn("usage flush failed for household {}: {}",
                        householdId, e.getMessage());
            }
        }
    }

    /**
     * The usage row for the current period, created on demand.
     *
     * <p>Period boundaries follow the owner's Stripe subscription when
     * there is one, so an allowance resets when the customer is billed
     * rather than on an unrelated calendar boundary. Accounts without a
     * subscription get a calendar month.
     */
    @Transactional
    public UsagePeriod currentPeriod(Household household) {
        LocalDateTime start = periodStartFor(household);
        return usagePeriodRepository
                .findByHouseholdIdAndPeriodStart(household.getId(), start)
                .orElseGet(() -> {
                    UsagePeriod period = new UsagePeriod();
                    period.setHousehold(household);
                    period.setPeriodStart(start);
                    period.setPeriodEnd(periodEndFor(household, start));
                    period.setBytesUsed(0L);
                    return usagePeriodRepository.save(period);
                });
    }

    /**
     * Drop cached counters for a household.
     *
     * <p>Called when a billing period rolls over: the next read then
     * picks up the new period's row instead of continuing to add to the
     * old period's total.
     */
    public void resetCache(Long householdId) {
        pending.remove(householdId);
        cachedTotals.remove(householdId);
    }

    private LocalDateTime periodStartFor(Household household) {
        return subscriptionOf(household)
                .map(Subscription::getCurrentPeriodStart)
                .filter(java.util.Objects::nonNull)
                .map(start -> start.truncatedTo(ChronoUnit.SECONDS))
                .orElseGet(() -> LocalDateTime.now()
                        .withDayOfMonth(1)
                        .truncatedTo(ChronoUnit.DAYS));
    }

    private LocalDateTime periodEndFor(Household household, LocalDateTime start) {
        return subscriptionOf(household)
                .map(Subscription::getCurrentPeriodEnd)
                .filter(java.util.Objects::nonNull)
                .orElseGet(() -> start.plusMonths(1));
    }

    private Optional<Subscription> subscriptionOf(Household household) {
        if (household.getUser() == null || household.getUser().getId() == null) {
            return Optional.empty();
        }
        return subscriptionRepository.findByUserId(household.getUser().getId());
    }
}
