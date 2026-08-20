package com.lazythumblabs.ltl.controllers.api.v1;

import com.lazythumblabs.ltl.dto.request.CheckoutRequest;
import com.lazythumblabs.ltl.dto.response.ApiResponse;
import com.lazythumblabs.ltl.dto.response.CheckoutResponse;
import com.lazythumblabs.ltl.dto.response.PlanResponse;
import com.lazythumblabs.ltl.dto.response.SubscriptionResponse;
import com.lazythumblabs.ltl.entities.User;
import com.lazythumblabs.ltl.repositories.PlanRepository;
import com.lazythumblabs.ltl.repositories.SubscriptionRepository;
import com.lazythumblabs.ltl.services.BillingService;
import jakarta.validation.Valid;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/v1")
public class BillingApiController extends BaseApiController {

    @Autowired
    private BillingService billingService;

    @Autowired
    private PlanRepository planRepository;

    @Autowired
    private SubscriptionRepository subscriptionRepository;

    /** Public: the pricing page reads this without a session. */
    @GetMapping("/plans")
    public ResponseEntity<ApiResponse<List<PlanResponse>>> plans() {
        List<PlanResponse> plans = planRepository.findByActiveTrueOrderBySortOrderAsc()
                .stream().map(PlanResponse::from).toList();
        return ResponseEntity.ok(ApiResponse.success(plans));
    }

    @GetMapping("/billing/subscription")
    public ResponseEntity<ApiResponse<SubscriptionResponse>> subscription() {
        User user = getAuthenticatedUser();
        return ResponseEntity.ok(ApiResponse.success(
                subscriptionRepository.findByUserId(user.getId())
                        .map(SubscriptionResponse::from)
                        .orElse(null)));
    }

    @PostMapping("/billing/checkout")
    public ResponseEntity<ApiResponse<CheckoutResponse>> checkout(
            @Valid @RequestBody CheckoutRequest request) {
        String url = billingService.startCheckout(getAuthenticatedUser(), request.getPlanCode());
        return ResponseEntity.ok(ApiResponse.success(new CheckoutResponse(url)));
    }

    /**
     * The Stripe Customer Portal — where cancelling, changing plan and
     * updating a card all live.
     *
     * <p>Building those screens here would mean reimplementing proration
     * and dunning, and would put card handling in scope. The portal is
     * strictly better.
     */
    @PostMapping("/billing/portal")
    public ResponseEntity<ApiResponse<CheckoutResponse>> portal() {
        String url = billingService.openPortal(getAuthenticatedUser());
        return ResponseEntity.ok(ApiResponse.success(new CheckoutResponse(url)));
    }
}
