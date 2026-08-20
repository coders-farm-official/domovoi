package com.lazythumblabs.ltl.dto.response;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.lazythumblabs.ltl.entities.Subscription;

import java.time.LocalDateTime;

@JsonInclude(JsonInclude.Include.NON_NULL)
public class SubscriptionResponse {

    private String status;
    private PlanResponse plan;
    private boolean cancelAtPeriodEnd;
    private LocalDateTime currentPeriodEnd;
    private LocalDateTime graceUntil;

    public static SubscriptionResponse from(Subscription subscription) {
        SubscriptionResponse response = new SubscriptionResponse();
        response.status = subscription.getStatus();
        response.plan = PlanResponse.from(subscription.getPlan());
        response.cancelAtPeriodEnd = Boolean.TRUE.equals(subscription.getCancelAtPeriodEnd());
        response.currentPeriodEnd = subscription.getCurrentPeriodEnd();
        response.graceUntil = subscription.getGraceUntil();
        return response;
    }

    public String getStatus() { return status; }
    public PlanResponse getPlan() { return plan; }
    public boolean isCancelAtPeriodEnd() { return cancelAtPeriodEnd; }
    public LocalDateTime getCurrentPeriodEnd() { return currentPeriodEnd; }
    public LocalDateTime getGraceUntil() { return graceUntil; }
}
