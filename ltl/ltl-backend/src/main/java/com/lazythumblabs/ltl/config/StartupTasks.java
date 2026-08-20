package com.lazythumblabs.ltl.config;

import com.lazythumblabs.ltl.services.HouseholdService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

@Component
public class StartupTasks {

    @Autowired
    private HouseholdService householdService;

    /**
     * Every household is offline until its agent reconnects.
     *
     * <p>Without this, a crash or a deploy would leave rows claiming to
     * be online forever, and the web app would tell customers their
     * house is reachable when it is not — the single most annoying kind
     * of wrong for a status indicator to be.
     */
    @EventListener(ApplicationReadyEvent.class)
    public void resetOnlineFlags() {
        householdService.markAllOffline();
    }
}
