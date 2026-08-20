package com.lazythumblabs.ltl;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * LTL Remote — the control plane (accounts, billing, entitlements) and
 * the relay data plane, in one deployable.
 *
 * <p>They live together on purpose. At this scale a second service buys
 * operational cost and nothing else; the package split ({@code relay}
 * versus {@code controllers}) is drawn so that pulling the relay into
 * its own process later is a build-file change rather than a rewrite.
 */
@SpringBootApplication
@EnableScheduling
public class LtlApplication {

	public static void main(String[] args) {
		SpringApplication.run(LtlApplication.class, args);
	}

}
