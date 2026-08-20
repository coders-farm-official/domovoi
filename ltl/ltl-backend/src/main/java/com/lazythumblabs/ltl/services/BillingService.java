package com.lazythumblabs.ltl.services;

import com.lazythumblabs.ltl.entities.Plan;
import com.lazythumblabs.ltl.entities.StripeEvent;
import com.lazythumblabs.ltl.entities.Subscription;
import com.lazythumblabs.ltl.entities.User;
import com.lazythumblabs.ltl.repositories.HouseholdRepository;
import com.lazythumblabs.ltl.repositories.PlanRepository;
import com.lazythumblabs.ltl.repositories.StripeEventRepository;
import com.lazythumblabs.ltl.repositories.SubscriptionRepository;
import com.lazythumblabs.ltl.repositories.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.Optional;

/**
 * Subscription lifecycle: checkout, portal, and applying webhook events.
 *
 * <p>Nothing injects {@link BillingProvider} directly — every caller
 * goes through this class, which picks the real provider when a Stripe
 * key is present and the fake one when it is not. That keeps the "which
 * provider is live" decision in exactly one place instead of scattering
 * {@code @Primary} annotations and conditional beans across the app.
 */
@Service
public class BillingService {

    private static final Logger logger = LoggerFactory.getLogger(BillingService.class);

    @Autowired
    private StripeBillingProvider stripeProvider;

    @Autowired
    private FakeBillingProvider fakeProvider;

    @Autowired
    private UserRepository userRepository;

    @Autowired
    private HouseholdRepository householdRepository;

    @Autowired
    private PlanRepository planRepository;

    @Autowired
    private SubscriptionRepository subscriptionRepository;

    @Autowired
    private StripeEventRepository stripeEventRepository;

    @Autowired
    private UsageService usageService;

    @Value("${ltl.billing.grace-days:7}")
    private int graceDays;

    @Value("${app.base-url:http://localhost:8080}")
    private String baseUrl;

    public BillingProvider provider() {
        return stripeProvider.isConfigured() ? stripeProvider : fakeProvider;
    }

    // ── customer-facing flows ───────────────────────────────────────────

    @Transactional
    public String startCheckout(User user, String planCode) {
        Plan plan = planRepository.findByCode(planCode)
                .orElseThrow(() -> new IllegalArgumentException("no such plan: " + planCode));
        if (!Boolean.TRUE.equals(plan.getActive())) {
            throw new IllegalArgumentException("that plan is no longer available");
        }
        BillingProvider active = provider();
        String customerId = active.ensureCustomer(user);
        if (!customerId.equals(user.getStripeCustomerId())) {
            user.setStripeCustomerId(customerId);
            userRepository.save(user);
        }
        return active.createCheckoutUrl(user, plan,
                baseUrl + "/app.html?checkout=success",
                baseUrl + "/pricing.html?checkout=cancelled");
    }

    public String openPortal(User user) {
        return provider().createPortalUrl(user, baseUrl + "/app.html");
    }

    // ── webhook application ─────────────────────────────────────────────

    /**
     * Whether this event has already been applied.
     *
     * <p>Stripe retries. A retried {@code customer.subscription.deleted}
     * must not cancel a subscription the customer has since restarted,
     * so every id is recorded and a repeat is acknowledged without being
     * re-applied.
     */
    @Transactional(readOnly = true)
    public boolean alreadyProcessed(String eventId) {
        return stripeEventRepository.existsByEventId(eventId);
    }

    @Transactional
    public void recordProcessed(String eventId, String eventType, String error) {
        StripeEvent event = new StripeEvent();
        event.setEventId(eventId);
        event.setEventType(eventType);
        event.setStatus(error == null ? StripeEvent.PROCESSED : StripeEvent.FAILED);
        event.setErrorMessage(error);
        stripeEventRepository.save(event);
    }

    /**
     * Bind a completed checkout to an account.
     *
     * <p>Looks the user up by the metadata written when the session was
     * created, falling back to the Stripe customer id. Both paths exist
     * because metadata is the reliable one and the customer id is the
     * one that still works if a session was created out of band.
     */
    @Transactional
    public void applyCheckoutCompleted(
            String userIdMetadata, String planCodeMetadata,
            String stripeCustomerId, String stripeSubscriptionId,
            Long periodStartEpoch, Long periodEndEpoch) {

        Optional<User> maybeUser = Optional.empty();
        if (userIdMetadata != null && !userIdMetadata.isBlank()) {
            try {
                maybeUser = userRepository.findById(Long.parseLong(userIdMetadata));
            } catch (NumberFormatException ignored) {
                // Fall through to the customer-id lookup below.
            }
        }
        if (maybeUser.isEmpty() && stripeCustomerId != null) {
            maybeUser = userRepository.findByStripeCustomerId(stripeCustomerId);
        }
        if (maybeUser.isEmpty()) {
            logger.warn("checkout completed for an unknown account (customer {})",
                    stripeCustomerId);
            return;
        }

        User user = maybeUser.get();
        if (stripeCustomerId != null && user.getStripeCustomerId() == null) {
            user.setStripeCustomerId(stripeCustomerId);
            userRepository.save(user);
        }

        Plan plan = planRepository.findByCode(
                        planCodeMetadata == null ? "free" : planCodeMetadata)
                .orElseThrow(() -> new IllegalStateException(
                        "checkout referenced unknown plan " + planCodeMetadata));

        Subscription subscription = subscriptionRepository.findByUserId(user.getId())
                .orElseGet(Subscription::new);
        subscription.setUser(user);
        subscription.setPlan(plan);
        subscription.setStripeSubscriptionId(stripeSubscriptionId);
        subscription.setStatus(Subscription.ACTIVE);
        subscription.setCancelAtPeriodEnd(false);
        subscription.setGraceUntil(null);
        subscription.setCurrentPeriodStart(toLocal(periodStartEpoch));
        subscription.setCurrentPeriodEnd(toLocal(periodEndEpoch));
        subscriptionRepository.save(subscription);

        logger.info("account {} subscribed to plan {}", user.getId(), plan.getCode());
    }

    /**
     * Sync a subscription's status and period.
     *
     * <p>A period roll clears the household's cached byte counters so the
     * new period starts from its own row rather than continuing to add
     * to the previous one.
     */
    @Transactional
    public void applySubscriptionUpdated(
            String stripeSubscriptionId, String status, String stripePriceId,
            boolean cancelAtPeriodEnd, Long periodStartEpoch, Long periodEndEpoch) {

        Optional<Subscription> maybe =
                subscriptionRepository.findByStripeSubscriptionId(stripeSubscriptionId);
        if (maybe.isEmpty()) {
            logger.warn("update for unknown subscription {}", stripeSubscriptionId);
            return;
        }
        Subscription subscription = maybe.get();
        LocalDateTime previousStart = subscription.getCurrentPeriodStart();

        if (status != null) {
            subscription.setStatus(status);
        }
        if (stripePriceId != null) {
            planRepository.findByStripePriceId(stripePriceId).ifPresent(subscription::setPlan);
        }
        subscription.setCancelAtPeriodEnd(cancelAtPeriodEnd);
        subscription.setCurrentPeriodStart(toLocal(periodStartEpoch));
        subscription.setCurrentPeriodEnd(toLocal(periodEndEpoch));
        if (Subscription.ACTIVE.equals(status) || Subscription.TRIALING.equals(status)) {
            subscription.setGraceUntil(null);
        }
        subscriptionRepository.save(subscription);

        LocalDateTime newStart = subscription.getCurrentPeriodStart();
        if (newStart != null && !newStart.equals(previousStart)) {
            // The period moved, so every household on this account needs
            // its cached counters dropped — otherwise the new period
            // would keep accumulating onto the old period's total.
            householdRepository.findByUserIdOrderByCreatedAtAsc(subscription.getUser().getId())
                    .forEach(household -> usageService.resetCache(household.getId()));
            logger.info("subscription {} rolled into a new period", stripeSubscriptionId);
        }
    }

    @Transactional
    public void applySubscriptionDeleted(String stripeSubscriptionId) {
        subscriptionRepository.findByStripeSubscriptionId(stripeSubscriptionId)
                .ifPresent(subscription -> {
                    subscription.setStatus(Subscription.CANCELED);
                    subscription.setGraceUntil(null);
                    subscriptionRepository.save(subscription);
                    logger.info("subscription {} cancelled", stripeSubscriptionId);
                });
    }

    @Transactional
    public void applyPaymentSucceeded(String stripeSubscriptionId) {
        subscriptionRepository.findByStripeSubscriptionId(stripeSubscriptionId)
                .ifPresent(subscription -> {
                    subscription.setStatus(Subscription.ACTIVE);
                    subscription.setGraceUntil(null);
                    subscriptionRepository.save(subscription);
                });
    }

    /**
     * A failed invoice starts a grace period rather than cutting service
     * immediately. Losing remote access must never be the first way a
     * customer learns their card expired.
     */
    @Transactional
    public void applyPaymentFailed(String stripeSubscriptionId) {
        subscriptionRepository.findByStripeSubscriptionId(stripeSubscriptionId)
                .ifPresent(subscription -> {
                    subscription.setStatus(Subscription.PAST_DUE);
                    if (subscription.getGraceUntil() == null) {
                        subscription.setGraceUntil(LocalDateTime.now().plusDays(graceDays));
                    }
                    subscriptionRepository.save(subscription);
                    logger.info("subscription {} is past due; grace until {}",
                            stripeSubscriptionId, subscription.getGraceUntil());
                });
    }

    private static LocalDateTime toLocal(Long epochSeconds) {
        if (epochSeconds == null || epochSeconds <= 0) {
            return null;
        }
        return LocalDateTime.ofInstant(Instant.ofEpochSecond(epochSeconds), ZoneId.of("UTC"));
    }
}
