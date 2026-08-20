package com.lazythumblabs.ltl.dto.response;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.util.List;

/** Everything the account dashboard needs in one call. */
@JsonInclude(JsonInclude.Include.NON_NULL)
public class AccountResponse {

    private String email;
    private String displayName;
    private SubscriptionResponse subscription;
    private List<HouseholdResponse> households;

    public AccountResponse(String email, String displayName,
                           SubscriptionResponse subscription,
                           List<HouseholdResponse> households) {
        this.email = email;
        this.displayName = displayName;
        this.subscription = subscription;
        this.households = households;
    }

    public String getEmail() { return email; }
    public String getDisplayName() { return displayName; }
    public SubscriptionResponse getSubscription() { return subscription; }
    public List<HouseholdResponse> getHouseholds() { return households; }
}
