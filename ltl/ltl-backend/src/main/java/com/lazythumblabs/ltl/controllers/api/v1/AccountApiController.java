package com.lazythumblabs.ltl.controllers.api.v1;

import com.lazythumblabs.ltl.dto.response.AccountResponse;
import com.lazythumblabs.ltl.dto.response.ApiResponse;
import com.lazythumblabs.ltl.dto.response.HouseholdResponse;
import com.lazythumblabs.ltl.dto.response.SubscriptionResponse;
import com.lazythumblabs.ltl.entities.User;
import com.lazythumblabs.ltl.repositories.SubscriptionRepository;
import com.lazythumblabs.ltl.services.UserService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/v1/account")
public class AccountApiController extends BaseApiController {

    @Autowired
    private SubscriptionRepository subscriptionRepository;

    @Autowired
    private UserService users;

    /** Everything the account dashboard needs, in one call. */
    @GetMapping
    public ResponseEntity<ApiResponse<AccountResponse>> account() {
        User user = getAuthenticatedUser();
        List<HouseholdResponse> households = householdService.listFor(user)
                .stream().map(HouseholdResponse::from).toList();
        SubscriptionResponse subscription = subscriptionRepository.findByUserId(user.getId())
                .map(SubscriptionResponse::from)
                .orElse(null);
        return ResponseEntity.ok(ApiResponse.success(new AccountResponse(
                user.getEmail(), user.getDisplayName(), subscription, households)));
    }

    /**
     * Provision the local account for a freshly authenticated Stytch
     * user.
     *
     * <p>Called by the frontend right after sign-in. Doing it here rather
     * than in a Stytch webhook keeps the two systems from being able to
     * disagree about whether an account exists: if you can authenticate,
     * you have a row.
     */
    @PostMapping("/bootstrap")
    public ResponseEntity<ApiResponse<AccountResponse>> bootstrap(
            @RequestParam(required = false) String email,
            @RequestParam(required = false) String name) {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication == null || "anonymousUser".equals(authentication.getName())) {
            throw new ApiExceptionHandler.NotAuthenticatedException("Not authenticated");
        }
        users.ensureUser(authentication.getName(), email, name);
        return account();
    }
}
