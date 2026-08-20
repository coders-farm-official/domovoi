package com.lazythumblabs.ltl.config;

import com.lazythumblabs.ltl.relay.AgentSocketHandler;
import com.lazythumblabs.ltl.relay.ClientSocketHandler;
import com.lazythumblabs.ltl.relay.RelayHandshakeInterceptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.EnableWebSocket;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;
import org.springframework.web.socket.server.standard.ServletServerContainerFactoryBean;

/**
 * The two relay endpoints.
 *
 * <p>Plain WebSocket, not STOMP: the payloads are sealed application
 * frames the server must not interpret, and a messaging layer that
 * wanted to route by destination header would have nothing to read.
 */
@Configuration
@EnableWebSocket
public class RelayWebSocketConfig implements WebSocketConfigurer {

    @Autowired
    private AgentSocketHandler agentSocketHandler;

    @Autowired
    private ClientSocketHandler clientSocketHandler;

    @Autowired
    private RelayHandshakeInterceptor handshakeInterceptor;

    @Value("${ltl.relay.max-frame-bytes:4456448}")
    private int maxFrameBytes;

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(agentSocketHandler, "/relay/v1/agent")
                .addInterceptors(handshakeInterceptor)
                // Households dial out from anywhere; there is no origin
                // to check, and the bearer token plus challenge
                // signature are what actually authenticate them.
                .setAllowedOriginPatterns("*");

        registry.addHandler(clientSocketHandler, "/relay/v1/client")
                .addInterceptors(handshakeInterceptor)
                .setAllowedOriginPatterns("*");
    }

    @Bean
    public ServletServerContainerFactoryBean createWebSocketContainer() {
        ServletServerContainerFactoryBean container = new ServletServerContainerFactoryBean();
        container.setMaxBinaryMessageBufferSize(maxFrameBytes);
        container.setMaxTextMessageBufferSize(8192);
        // Long idle timeouts: a household's agent socket is mostly quiet,
        // and dropping it every few minutes would mean a reconnect storm
        // rather than a saving.
        container.setMaxSessionIdleTimeout(300_000L);
        return container;
    }
}
