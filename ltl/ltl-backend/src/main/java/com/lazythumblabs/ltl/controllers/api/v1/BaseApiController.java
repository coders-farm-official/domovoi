package com.lazythumblabs.ltl.controllers.api.v1;

import com.lazythumblabs.ltl.entities.Household;
import com.lazythumblabs.ltl.entities.User;
import com.lazythumblabs.ltl.services.HouseholdService;
import com.lazythumblabs.ltl.services.UserService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;

public abstract class BaseApiController {

    @Autowired
    protected UserService userService;

    @Autowired
    protected HouseholdService householdService;

    protected User getAuthenticatedUser() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication == null || !authentication.isAuthenticated()
                || "anonymousUser".equals(authentication.getName())) {
            throw new ApiExceptionHandler.NotAuthenticatedException("Not authenticated");
        }
        return userService.findByStytchUserId(authentication.getName())
                .orElseThrow(() -> new ApiExceptionHandler.NotAuthenticatedException(
                        "No account for this session"));
    }

    /**
     * A household the caller owns, or a 404.
     *
     * <p>Ownership is part of the lookup rather than a check a controller
     * could forget: another account's household id is indistinguishable
     * from one that does not exist, which is also the right thing to
     * leak — nothing.
     */
    protected Household requireOwnedHousehold(String householdUid) {
        return householdService.findOwned(getAuthenticatedUser(), householdUid)
                .orElseThrow(() -> new ApiExceptionHandler.ResourceNotFoundException(
                        "No such household"));
    }
}
