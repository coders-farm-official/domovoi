package com.lazythumblabs.ltl.services;

import com.lazythumblabs.ltl.entities.Plan;
import com.lazythumblabs.ltl.entities.User;
import com.stripe.Stripe;
import com.stripe.exception.StripeException;
import com.stripe.model.Customer;
import com.stripe.model.Subscription;
import com.stripe.param.CustomerCreateParams;
import com.stripe.param.SubscriptionUpdateParams;
import com.stripe.param.checkout.SessionCreateParams;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

/**
 * Stripe Checkout and the Customer Portal.
 *
 * <p>Both flows are hosted by Stripe, so card details never reach LTL.
 * That keeps PCI scope where it belongs and removes an entire class of
 * form from the frontend.
 */
@Service
public class StripeBillingProvider implements BillingProvider {

    private static final Logger logger = LoggerFactory.getLogger(StripeBillingProvider.class);

    @Value("${stripe.secret.key:}")
    private String secretKey;

    private void init() {
        Stripe.apiKey = secretKey;
    }

    @Override
    public boolean isConfigured() {
        return secretKey != null && !secretKey.isBlank()
                && !secretKey.equals("sk_test_YOUR_KEY_HERE");
    }

    @Override
    public String ensureCustomer(User user) {
        if (user.getStripeCustomerId() != null && !user.getStripeCustomerId().isBlank()) {
            return user.getStripeCustomerId();
        }
        init();
        try {
            CustomerCreateParams params = CustomerCreateParams.builder()
                    .setEmail(user.getEmail())
                    .setName(user.getDisplayName())
                    .putMetadata("ltl_user_id", String.valueOf(user.getId()))
                    .build();
            Customer customer = Customer.create(params);
            logger.info("created Stripe customer {} for {}", customer.getId(), user.getEmail());
            return customer.getId();
        } catch (StripeException e) {
            throw new BillingException("could not create a billing customer", e);
        }
    }

    @Override
    public String createCheckoutUrl(User user, Plan plan, String successUrl, String cancelUrl) {
        if (plan.getStripePriceId() == null || plan.getStripePriceId().isBlank()) {
            throw new BillingException("plan " + plan.getCode() + " has no Stripe price configured");
        }
        init();
        try {
            SessionCreateParams params = SessionCreateParams.builder()
                    .setMode(SessionCreateParams.Mode.SUBSCRIPTION)
                    .setCustomer(ensureCustomer(user))
                    .setSuccessUrl(successUrl)
                    .setCancelUrl(cancelUrl)
                    .addLineItem(SessionCreateParams.LineItem.builder()
                            .setPrice(plan.getStripePriceId())
                            .setQuantity(1L)
                            .build())
                    // The webhook needs to know which account and plan
                    // this was, and the Checkout session is the only place
                    // that association is guaranteed to survive.
                    .putMetadata("ltl_user_id", String.valueOf(user.getId()))
                    .putMetadata("ltl_plan_code", plan.getCode())
                    .build();
            return com.stripe.model.checkout.Session.create(params).getUrl();
        } catch (StripeException e) {
            throw new BillingException("could not start checkout", e);
        }
    }

    @Override
    public String createPortalUrl(User user, String returnUrl) {
        String customerId = user.getStripeCustomerId();
        if (customerId == null || customerId.isBlank()) {
            throw new BillingException("this account has no billing customer yet");
        }
        init();
        try {
            // Fully qualified on both sides: `Session` and
            // `SessionCreateParams` each exist under `checkout` and
            // `billingportal`, and importing one of each pair would make
            // the other read like a typo.
            com.stripe.param.billingportal.SessionCreateParams params =
                    com.stripe.param.billingportal.SessionCreateParams.builder()
                            .setCustomer(customerId)
                            .setReturnUrl(returnUrl)
                            .build();
            return com.stripe.model.billingportal.Session.create(params).getUrl();
        } catch (StripeException e) {
            throw new BillingException("could not open the billing portal", e);
        }
    }

    @Override
    public void cancelAtPeriodEnd(String providerSubscriptionId) {
        init();
        try {
            Subscription subscription = Subscription.retrieve(providerSubscriptionId);
            subscription.update(SubscriptionUpdateParams.builder()
                    .setCancelAtPeriodEnd(true)
                    .build());
        } catch (StripeException e) {
            throw new BillingException("could not schedule cancellation", e);
        }
    }

    /** Any provider-side failure, surfaced to the API as a 502. */
    public static class BillingException extends RuntimeException {
        public BillingException(String message) {
            super(message);
        }

        public BillingException(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
