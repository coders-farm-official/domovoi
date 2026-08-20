package com.lazythumblabs.ltl.dto.response;

/**
 * A redirect target for Stripe Checkout or the Customer Portal.
 *
 * <p>Card details never reach LTL — both flows are hosted by Stripe,
 * which keeps PCI scope where it belongs and removes an entire class of
 * form from the frontend.
 */
public class CheckoutResponse {

    private final String url;

    public CheckoutResponse(String url) {
        this.url = url;
    }

    public String getUrl() { return url; }
}
