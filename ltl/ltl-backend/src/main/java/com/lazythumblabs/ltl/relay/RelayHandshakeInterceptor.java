package com.lazythumblabs.ltl.relay;

import org.springframework.http.server.ServerHttpRequest;
import org.springframework.http.server.ServerHttpResponse;
import org.springframework.http.server.ServletServerHttpRequest;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.WebSocketHandler;
import org.springframework.web.socket.server.HandshakeInterceptor;

import java.util.Map;

/**
 * Carries the HTTP handshake's headers and query into the WebSocket
 * session, where the handlers can reach them.
 *
 * <p>Spring hands a {@code WebSocketSession} only its attributes, so
 * anything from the upgrade request — the bearer token, the household
 * and device ids, the caller's country — has to be stashed here or it is
 * gone by the time a handler runs.
 */
@Component
public class RelayHandshakeInterceptor implements HandshakeInterceptor {

    public static final String ATTR_AUTHORIZATION = "ltl.authorization";
    public static final String ATTR_HOUSEHOLD = "ltl.household";
    public static final String ATTR_DEVICE = "ltl.device";
    public static final String ATTR_COUNTRY = "ltl.country";
    public static final String ATTR_REMOTE_IP = "ltl.remoteIp";

    @Override
    public boolean beforeHandshake(ServerHttpRequest request, ServerHttpResponse response,
                                   WebSocketHandler handler, Map<String, Object> attributes) {
        String authorization = request.getHeaders().getFirst("Authorization");
        if (authorization != null && authorization.startsWith("Bearer ")) {
            attributes.put(ATTR_AUTHORIZATION, authorization.substring(7).trim());
        }

        // Country comes from the reverse proxy (Caddy or Cloudflare), and
        // is coarse on purpose: routing needs a rough origin for the
        // audit trail, and nothing here needs a precise location.
        String country = request.getHeaders().getFirst("CF-IPCountry");
        if (country == null) {
            country = request.getHeaders().getFirst("X-Country-Code");
        }
        if (country != null && country.length() == 2) {
            attributes.put(ATTR_COUNTRY, country.toUpperCase());
        }

        for (String pair : queryOf(request).split("&")) {
            int equals = pair.indexOf('=');
            if (equals <= 0) {
                continue;
            }
            String key = pair.substring(0, equals);
            String value = java.net.URLDecoder.decode(
                    pair.substring(equals + 1), java.nio.charset.StandardCharsets.UTF_8);
            if ("household".equals(key)) {
                attributes.put(ATTR_HOUSEHOLD, value);
            } else if ("device".equals(key)) {
                attributes.put(ATTR_DEVICE, value);
            }
        }

        // The agent may also send its household id as a header, which is
        // what the plugin does — a query string ends up in more access
        // logs than a header does.
        String householdHeader = request.getHeaders().getFirst("X-Household-Id");
        if (householdHeader != null && !householdHeader.isBlank()) {
            attributes.put(ATTR_HOUSEHOLD, householdHeader);
        }

        if (request instanceof ServletServerHttpRequest servletRequest) {
            attributes.put(ATTR_REMOTE_IP,
                    servletRequest.getServletRequest().getRemoteAddr());
        }
        return true;
    }

    @Override
    public void afterHandshake(ServerHttpRequest request, ServerHttpResponse response,
                               WebSocketHandler handler, Exception exception) {
        // Nothing to do.
    }

    private static String queryOf(ServerHttpRequest request) {
        String query = request.getURI().getRawQuery();
        return query == null ? "" : query;
    }
}
