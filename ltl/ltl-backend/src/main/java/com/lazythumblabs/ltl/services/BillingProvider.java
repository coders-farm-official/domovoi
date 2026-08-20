package com.lazythumblabs.ltl.services;

import com.lazythumblabs.ltl.entities.Plan;
import com.lazythumblabs.ltl.entities.User;

/**
 * The payments seam.
 *
 * <p>Two implementations: {@link StripeBillingProvider} and
 * {@link FakeBillingProvider}. The fake is selected automatically when
 * no Stripe key is configured, which means checkout, webhook handling,
 * entitlement transitions, grace periods and quota enforcement all run
 * end to end in local development and in tests with no Stripe account at
 * all.
 *
 * <p>That is the reason this interface exists. A second real provider is
 * a nice option to have; being able to exercise the whole billing state
 * machine without keys is the thing that pays for itself immediately.
 */
public interface BillingProvider {

    /** True when this provider can actually take money. */
    boolean isConfigured();

    /** Create or reuse the provider-side customer for an account. */
    String ensureCustomer(User user);

    /** A hosted checkout URL for a plan. */
    String createCheckoutUrl(User user, Plan plan, String successUrl, String cancelUrl);

    /** A hosted customer-portal URL for managing an existing subscription. */
    String createPortalUrl(User user, String returnUrl);

    /** Cancel at period end, leaving the customer their remaining time. */
    void cancelAtPeriodEnd(String providerSubscriptionId);
}
