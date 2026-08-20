package com.lazythumblabs.ltl.config;

import com.lazythumblabs.ltl.filters.JwtAuthFilter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;

import static org.springframework.security.config.http.SessionCreationPolicy.STATELESS;

@Configuration
@EnableWebSecurity
@EnableAsync
public class AppConfiguration {

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http
            .authorizeHttpRequests(authorize -> authorize
                // Pairing: a Domovoi server calling this has no account
                // and no credential yet — that is the whole point of a
                // pairing code. Rate limiting and the code hash are what
                // protect it, not authentication.
                .requestMatchers("/api/v1/enroll", "/api/v1/enroll/**").permitAll()
                // Stripe signs its webhooks; a session would be meaningless.
                .requestMatchers("/api/webhooks/**").permitAll()
                // The relay authenticates with a household relay token
                // plus a challenge signature, inside the socket.
                .requestMatchers("/relay/**").permitAll()
                // Public reads.
                .requestMatchers("/api/v1/plans").permitAll()
                .requestMatchers("/actuator/health").permitAll()
                .requestMatchers("/swagger-ui/**", "/v3/api-docs/**").permitAll()
                // Everything else under /api/v1 needs a signed-in account.
                .requestMatchers("/api/v1/**").authenticated()
                .anyRequest().permitAll()
            )
            // CSRF is off because there is no cookie-authenticated,
            // form-posting surface here: the API is called with a bearer
            // token from a separate static frontend, and the one
            // cookie-authenticated path (Stytch's session cookie) is
            // used only by that same JSON API.
            .csrf(AbstractHttpConfigurer::disable)
            .sessionManagement(manager -> manager.sessionCreationPolicy(STATELESS))
            .addFilterBefore(jwtAuthFilter(), UsernamePasswordAuthenticationFilter.class);
        return http.build();
    }

    @Bean
    JwtAuthFilter jwtAuthFilter() {
        return new JwtAuthFilter();
    }
}
