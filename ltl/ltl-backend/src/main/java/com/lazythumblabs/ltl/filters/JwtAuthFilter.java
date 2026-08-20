package com.lazythumblabs.ltl.filters;

import com.lazythumblabs.ltl.entities.User;
import com.lazythumblabs.ltl.services.StytchService;
import com.lazythumblabs.ltl.services.UserService;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.AuthorityUtils;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.authentication.WebAuthenticationDetailsSource;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.Arrays;
import java.util.Optional;

/**
 * Session authentication, matching the Scooped pattern: a bearer token
 * from the {@code Authorization} header for app clients, falling back to
 * the Stytch session cookie for browsers.
 *
 * <p>The filter never rejects. It either establishes an authentication
 * or leaves the request anonymous and lets the security chain decide —
 * which keeps "who may reach this path" in one place
 * ({@code AppConfiguration}) instead of split across a filter and a
 * config.
 */
@Component
public class JwtAuthFilter extends OncePerRequestFilter {

    @Autowired
    private StytchService stytchService;

    @Autowired
    private UserService userService;

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        // The relay endpoints authenticate with a household relay token
        // and a challenge signature, not a user session. Running this
        // filter over them would only waste a Stytch round trip.
        return path.startsWith("/relay/")
                || path.startsWith("/api/webhooks/")
                || path.startsWith("/css/")
                || path.startsWith("/js/")
                || path.startsWith("/images/");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        String token = extractBearerToken(request);
        if (token == null) {
            token = extractCookie(request);
        }
        if (token == null) {
            filterChain.doFilter(request, response);
            return;
        }

        String stytchUserId = stytchService.authenticateJwt(token);
        if (stytchUserId != null) {
            Optional<User> user = userService.findByStytchUserId(stytchUserId);
            // A disabled account authenticates as nobody. Returning 403
            // from here would leak that the account exists.
            if (user.isPresent() && Boolean.TRUE.equals(user.get().getDisabled())) {
                filterChain.doFilter(request, response);
                return;
            }
            UsernamePasswordAuthenticationToken authentication =
                    new UsernamePasswordAuthenticationToken(
                            stytchUserId, null, AuthorityUtils.NO_AUTHORITIES);
            authentication.setDetails(
                    new WebAuthenticationDetailsSource().buildDetails(request));
            SecurityContextHolder.getContext().setAuthentication(authentication);
        }

        filterChain.doFilter(request, response);
    }

    private String extractBearerToken(HttpServletRequest request) {
        String header = request.getHeader("Authorization");
        if (header != null && header.startsWith("Bearer ")) {
            String token = header.substring(7).trim();
            if (!token.isEmpty()) {
                return token;
            }
        }
        return null;
    }

    private String extractCookie(HttpServletRequest request) {
        Cookie[] cookies = request.getCookies();
        if (cookies == null) {
            return null;
        }
        return Arrays.stream(cookies)
                .filter(cookie -> StytchService.SESSION_JWT_COOKIE_NAME.equals(cookie.getName()))
                .map(Cookie::getValue)
                .findFirst()
                .orElse(null);
    }
}
