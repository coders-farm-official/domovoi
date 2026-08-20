package com.lazythumblabs.ltl.config;

import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class SwaggerConfig {

    @Bean
    public OpenAPI ltlOpenAPI() {
        return new OpenAPI().info(new Info()
                .title("LTL Remote API")
                .version("1.0.0")
                .description("""
                        Accounts, billing and pairing for LTL Remote.

                        The relay data plane is not described here: it is a \
                        binary WebSocket protocol whose payloads are encrypted \
                        end to end between a household's Domovoi server and \
                        the customer's device. See ltl/docs/PROTOCOL.md."""));
    }
}
