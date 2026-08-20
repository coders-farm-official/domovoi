package com.lazythumblabs.ltl.services;

import com.stytch.java.common.JWTAuthResponse;
import com.stytch.java.common.JWTResponse;
import com.stytch.java.common.JWTSessionResponse;
import com.stytch.java.common.StytchResult;
import com.stytch.java.consumer.StytchClient;
import com.stytch.java.consumer.models.sessions.Session;
import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

/**
 * Session-JWT validation against Stytch, the same identity provider
 * Scooped uses, driven through the same SDK calls.
 *
 * <p>Reusing it is not only consistency: one Lazy Thumb Labs login works
 * across products, and there is a single place where password resets,
 * MFA and OAuth providers are configured.
 */
@Service
public class StytchService {

    public static final String SESSION_JWT_COOKIE_NAME = "stytch_session_jwt";

    private static final Logger logger = LoggerFactory.getLogger(StytchService.class);

    @Value("${stytch.project-id:}")
    private String projectId;

    @Value("${stytch.project-secret:}")
    private String projectSecret;

    private volatile StytchClient client;

    @PostConstruct
    void init() {
        if (projectId == null || projectId.isBlank()
                || projectSecret == null || projectSecret.isBlank()) {
            logger.warn("Stytch is not configured — session validation will reject everything");
            return;
        }
        client = new StytchClient(projectId, projectSecret);
    }

    public boolean isConfigured() {
        return client != null;
    }

    /**
     * Validate a session JWT and return the Stytch user id, or null.
     *
     * <p>Null rather than an exception: the auth filter treats every
     * failure identically — leave the request anonymous and let the
     * security chain decide — and distinguishing "expired" from "forged"
     * here would only leak information to whoever presented it.
     */
    public String authenticateJwt(String jwt) {
        StytchClient active = client;
        if (active == null || jwt == null || jwt.isBlank()) {
            return null;
        }
        try {
            StytchResult<JWTResponse> result =
                    active.sessions.authenticateJwtCompletable(jwt, null).get();
            if (!(result instanceof StytchResult.Success<JWTResponse> success)) {
                return null;
            }
            Session session = sessionOf(success.getValue());
            return session == null ? null : session.getUserId();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return null;
        } catch (Exception e) {
            logger.debug("Stytch JWT validation failed: {}", e.getMessage());
            return null;
        }
    }

    /**
     * Unwrap whichever shape Stytch returned.
     *
     * <p>A locally-verifiable JWT comes back as a {@code JWTSessionResponse};
     * one that needed a round trip to Stytch comes back as a
     * {@code JWTAuthResponse}. Both mean the session is good.
     */
    private static Session sessionOf(JWTResponse response) {
        if (response instanceof JWTSessionResponse sessionResponse) {
            return sessionResponse.getResponse();
        }
        if (response instanceof JWTAuthResponse authResponse) {
            return authResponse.getResponse().getSession();
        }
        return null;
    }
}
