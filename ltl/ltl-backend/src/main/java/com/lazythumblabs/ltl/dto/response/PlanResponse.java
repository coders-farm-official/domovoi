package com.lazythumblabs.ltl.dto.response;

import com.lazythumblabs.ltl.entities.Plan;

/** A plan as the pricing page sees it. Stripe price ids stay server-side. */
public class PlanResponse {

    private String code;
    private String name;
    private String description;
    private long monthlyBytes;
    private int deviceLimit;
    private int householdLimit;
    private int priceCents;
    private String currency;

    public static PlanResponse from(Plan plan) {
        PlanResponse response = new PlanResponse();
        response.code = plan.getCode();
        response.name = plan.getName();
        response.description = plan.getDescription();
        response.monthlyBytes = plan.getMonthlyBytes();
        response.deviceLimit = plan.getDeviceLimit();
        response.householdLimit = plan.getHouseholdLimit();
        response.priceCents = plan.getPriceCents();
        response.currency = plan.getCurrency();
        return response;
    }

    public String getCode() { return code; }
    public String getName() { return name; }
    public String getDescription() { return description; }
    public long getMonthlyBytes() { return monthlyBytes; }
    public int getDeviceLimit() { return deviceLimit; }
    public int getHouseholdLimit() { return householdLimit; }
    public int getPriceCents() { return priceCents; }
    public String getCurrency() { return currency; }
}
