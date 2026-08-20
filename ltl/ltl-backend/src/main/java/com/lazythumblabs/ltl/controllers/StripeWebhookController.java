package com.lazythumblabs.ltl.controllers;

import com.lazythumblabs.ltl.services.BillingService;
import com.stripe.exception.SignatureVerificationException;
import com.stripe.model.Event;
import com.stripe.model.EventDataObjectDeserializer;
import com.stripe.model.Invoice;
import com.stripe.model.StripeObject;
import com.stripe.model.Subscription;
import com.stripe.model.checkout.Session;
import com.stripe.net.Webhook;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Optional;

/**
 * Stripe's view of the subscription lifecycle, applied to ours.
 *
 * <p>Two rules run through this class. First, the signature is verified
 * before the body is looked at — an unverified webhook is an
 * unauthenticated request to change billing state. Second, every event
 * id is recorded and repeats are acknowledged without being re-applied:
 * Stripe retries, and a retried {@code subscription.deleted} must not
 * cancel a subscription the customer has since restarted.
 */
@RestController
@RequestMapping("/api/webhooks")
public class StripeWebhookController {

    private static final Logger logger = LoggerFactory.getLogger(StripeWebhookController.class);

    @Value("${stripe.webhook.secret:}")
    private String webhookSecret;

    @Autowired
    private BillingService billingService;

    @PostMapping("/stripe")
    public ResponseEntity<String> handle(
            @RequestBody String payload,
            @RequestHeader(value = "Stripe-Signature", required = false) String signature) {

        if (webhookSecret == null || webhookSecret.isBlank()) {
            // Refuse rather than trust. Processing unverified events
            // would let anyone who can reach this URL grant themselves a
            // subscription.
            logger.warn("Stripe webhook secret is not configured; refusing the event");
            return ResponseEntity.badRequest().body("Webhook secret not configured");
        }

        Event event;
        try {
            event = Webhook.constructEvent(payload, signature, webhookSecret);
        } catch (SignatureVerificationException e) {
            logger.warn("Stripe webhook signature verification failed: {}", e.getMessage());
            return ResponseEntity.badRequest().body("Invalid signature");
        }

        if (billingService.alreadyProcessed(event.getId())) {
            return ResponseEntity.ok("Duplicate");
        }

        String error = null;
        try {
            switch (event.getType()) {
                case "checkout.session.completed" -> handleCheckoutCompleted(event);
                case "customer.subscription.updated" -> handleSubscriptionUpdated(event);
                case "customer.subscription.deleted" -> handleSubscriptionDeleted(event);
                case "invoice.payment_succeeded" -> handleInvoice(event, true);
                case "invoice.payment_failed" -> handleInvoice(event, false);
                default -> logger.debug("Unhandled Stripe event type: {}", event.getType());
            }
        } catch (RuntimeException e) {
            // Record the failure and still return 200. Stripe retries on
            // a non-2xx, and an event that fails deterministically would
            // be retried forever; the recorded row is what an operator
            // looks at instead.
            error = e.getMessage();
            logger.error("failed to apply Stripe event {} ({})", event.getId(), event.getType(), e);
        }

        billingService.recordProcessed(event.getId(), event.getType(), error);
        return ResponseEntity.ok("OK");
    }

    private void handleCheckoutCompleted(Event event) {
        deserialize(event, Session.class).ifPresent(session ->
                billingService.applyCheckoutCompleted(
                        session.getMetadata() == null ? null
                                : session.getMetadata().get("ltl_user_id"),
                        session.getMetadata() == null ? null
                                : session.getMetadata().get("ltl_plan_code"),
                        session.getCustomer(),
                        session.getSubscription(),
                        null, null));
    }

    private void handleSubscriptionUpdated(Event event) {
        deserialize(event, Subscription.class).ifPresent(subscription ->
                billingService.applySubscriptionUpdated(
                        subscription.getId(),
                        subscription.getStatus(),
                        priceIdOf(subscription),
                        Boolean.TRUE.equals(subscription.getCancelAtPeriodEnd()),
                        null, null));
    }

    private void handleSubscriptionDeleted(Event event) {
        deserialize(event, Subscription.class).ifPresent(subscription ->
                billingService.applySubscriptionDeleted(subscription.getId()));
    }

    private void handleInvoice(Event event, boolean succeeded) {
        deserialize(event, Invoice.class).ifPresent(invoice -> {
            String subscriptionId = invoice.getSubscription();
            if (subscriptionId == null) {
                return;
            }
            if (succeeded) {
                billingService.applyPaymentSucceeded(subscriptionId);
            } else {
                billingService.applyPaymentFailed(subscriptionId);
            }
        });
    }

    private static String priceIdOf(Subscription subscription) {
        if (subscription.getItems() == null || subscription.getItems().getData().isEmpty()) {
            return null;
        }
        return subscription.getItems().getData().get(0).getPrice() == null
                ? null
                : subscription.getItems().getData().get(0).getPrice().getId();
    }

    /**
     * Pull the typed object out of an event.
     *
     * <p>Returns empty when Stripe's API version does not match the SDK's
     * — better to skip an event and have it show up in the recorded log
     * than to act on a half-deserialized object.
     */
    private <T extends StripeObject> Optional<T> deserialize(Event event, Class<T> type) {
        EventDataObjectDeserializer deserializer = event.getDataObjectDeserializer();
        Optional<StripeObject> object = deserializer.getObject();
        if (object.isEmpty()) {
            logger.warn("could not deserialize Stripe event {} ({}) — API version mismatch?",
                    event.getId(), event.getType());
            return Optional.empty();
        }
        StripeObject value = object.get();
        if (!type.isInstance(value)) {
            return Optional.empty();
        }
        return Optional.of(type.cast(value));
    }
}
