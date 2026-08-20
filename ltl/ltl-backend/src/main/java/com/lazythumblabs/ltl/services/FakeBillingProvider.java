package com.lazythumblabs.ltl.services;

import com.lazythumblabs.ltl.entities.Plan;
import com.lazythumblabs.ltl.entities.User;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

/**
 * The no-Stripe implementation, used automatically when no secret key is
 * configured.
 *
 * <p>It returns URLs that point back at the app's own
 * {@code /billing/simulate} page, so the whole flow — press subscribe,
 * land somewhere, come back with an active subscription — works in local
 * development without a Stripe account or a webhook tunnel.
 *
 * <p>It is deliberately obvious about what it is. A fake payment
 * provider that looked real in production would be a very expensive
 * mistake, so its URLs say {@code simulate} and it logs loudly.
 */
@Service
public class FakeBillingProvider implements BillingProvider {

    private static final Logger logger = LoggerFactory.getLogger(FakeBillingProvider.class);

    @Value("${app.base-url:http://localhost:8080}")
    private String baseUrl;

    @Override
    public boolean isConfigured() {
        return false;
    }

    @Override
    public String ensureCustomer(User user) {
        return "fake_cus_" + user.getId();
    }

    @Override
    public String createCheckoutUrl(User user, Plan plan, String successUrl, String cancelUrl) {
        logger.warn("NO STRIPE KEY CONFIGURED — issuing a simulated checkout for plan {}",
                plan.getCode());
        return baseUrl + "/billing/simulate?plan=" + plan.getCode()
                + "&next=" + java.net.URLEncoder.encode(
                        successUrl, java.nio.charset.StandardCharsets.UTF_8);
    }

    @Override
    public String createPortalUrl(User user, String returnUrl) {
        logger.warn("NO STRIPE KEY CONFIGURED — issuing a simulated billing portal");
        return baseUrl + "/billing/simulate?portal=1&next="
                + java.net.URLEncoder.encode(returnUrl, java.nio.charset.StandardCharsets.UTF_8);
    }

    @Override
    public void cancelAtPeriodEnd(String providerSubscriptionId) {
        logger.warn("NO STRIPE KEY CONFIGURED — pretending to cancel {}", providerSubscriptionId);
    }
}
