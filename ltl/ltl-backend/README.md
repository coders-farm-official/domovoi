# ltl-backend

The LTL Remote service: accounts, billing and entitlements (the control
plane) plus the relay that carries sealed traffic between a household's
Domovoi server and its approved devices (the data plane).

Java 21 · Spring Boot 3.3 · Postgres · Stytch · Stripe — the same stack
and the same layering as `scooped-web`, so the two read as one codebase.

Design docs are one level up: [architecture](../docs/ARCHITECTURE.md),
[protocol](../docs/PROTOCOL.md), [security](../docs/SECURITY.md),
[billing](../docs/BILLING.md).

---

## Running it

```bash
cp .env.example .env        # fill in the database; everything else is optional
./gradlew bootRun
```

With no Stripe key, billing runs against `FakeBillingProvider`: checkout,
webhooks, entitlements, grace periods and quota enforcement all work end
to end without a Stripe account. With no Stytch keys, session validation
rejects everything — which is correct, but means you will want Stytch
configured to use the web app.

The schema is owned by [`../ltl-db`](../ltl-db), never by Hibernate:
`spring.jpa.hibernate.ddl-auto=validate` makes drift between the entities
and the database fail at startup rather than silently at the first query.

```bash
./gradlew test        # 33 tests, no database or network needed
./gradlew bootJar
```

## Layout

```
com.lazythumblabs.ltl
├── config/         security chain, WebSocket registration, OpenAPI, startup
├── filters/        JwtAuthFilter — bearer header or Stytch session cookie
├── controllers/    api/v1/** REST + webhooks/stripe
├── dto/            request/ and response/, with the ApiResponse<T> envelope
├── entities/       JPA, snake_case columns, no Lombok
├── repositories/   Spring Data JPA
├── services/       billing, entitlements, metering, pairing, devices
├── relay/          the WebSocket data plane
└── util/           CryptoUtil — the shared crypto vocabulary
```

## The relay, in one paragraph

`AgentSocketHandler` (`/relay/v1/agent`) holds one socket per household.
`ClientSocketHandler` (`/relay/v1/client`) holds one per connected
device. `RelayRegistry` maps between them. A client frame is wrapped in
an 18-byte header and handed to the household's agent; an agent frame is
unwrapped and handed to the right client. The payload is never parsed —
`RelayFrames` deliberately has no inner-frame decoder, because writing
one would mean the relay had acquired the ability to read household
traffic.

Java 21 virtual threads are what make one JVM a reasonable home for both
planes: a household's socket is mostly idle, and platform threads would
cap households per instance on stack allocation alone.

## Things worth knowing before changing something

**Entitlements have one implementation.** `EntitlementService.resolve` is
the only place that answers "may this household do this right now".
Adding a second check somewhere else is how a customer ends up locked out
for a reason nobody can find.

**`devices.approved` grants nothing.** It is a mirror of what a household
reported. Access is decided by the home server, against its own database,
inside a handshake this service cannot read. Flipping the column in a
psql session would achieve exactly nothing, which is the design working.

**Webhooks are idempotent by event id.** Stripe retries; a retried
`subscription.deleted` must not cancel a subscription the customer has
since restarted.

**`CryptoUtil` has no ECDH, no HKDF, no AES.** Not an oversight — the
relay has no business possessing them. If a change appears to need one,
that is a design discussion rather than an import.

**The cross-implementation vectors are not decorative.**
`CrossImplementationVectors` holds output from the Python plugin. Two
implementations of one protocol in two languages stay honest only by
being run against the same bytes; regenerate them from Python, never from
whatever Java currently produces.

## Deviations from scooped-web, and why

| Change | Reason |
|---|---|
| `spring-boot-starter-test` added | Scooped ships no test dependencies. The frame codec must agree byte for byte with a Python implementation, and the entitlement state machine decides whether customers can reach their own houses. Neither is checkable by hand. |
| An `application.properties` exists | Scooped reads everything through `Environment`, which works but leaves the knobs undiscoverable. This file holds the genuinely constant settings and documents the rest; every secret still comes from the environment, and none is checked in. |
| `spring-boot-starter-websocket` | The relay. |
| G1 rather than SerialGC, larger heap | Memory here scales with connected households, not with request concurrency, and thousands of long-lived sockets make pause time matter. |
| Explicit on-curve validation in `CryptoUtil` | The JDK's `KeyFactory` accepts P-256 coordinates that do not satisfy the curve equation. Python's `from_encoded_point` rejects them. Without the check the two sides disagree about which keys are valid, and the relay would store attacker-chosen points as household identities. |
