# LTL Remote

Remote access for [Domovoi](../README.md), by **Lazy Thumb Labs (LTL)**.

Domovoi is deliberately not internet-exposed: its
[threat model](../docs/SECURITY_PRIVACY.md) assumes the LAN *is* the
household, and the docs tell you in plain words not to port-forward
6369 or 6370. LTL Remote is the sanctioned way to get at your house from
outside it without punching that hole — the home server dials **out** to
a relay, and the relay carries bytes it cannot read.

This directory holds three deliverables that ship and version
independently:

| Directory | What it is | Stack |
|---|---|---|
| [`plugin-ltl-remote/`](plugin-ltl-remote/) | The Domovoi plugin (slug `ltl_remote`). Runs in the household's core process, holds the outbound link, proxies allowlisted loopback traffic. | Python 3.11, Domovoi plugin SDK |
| [`ltl-backend/`](ltl-backend/) | The LTL service: accounts, billing, entitlements, and the relay data plane. | Java 21, Spring Boot 3.3, Postgres |
| [`ltl-frontend/`](ltl-frontend/) | The LTL web app: marketing, sign-up, billing management, household/device management, and the browser remote client. | Static HTML + CSS + vanilla ES modules |
| [`ltl-db/`](ltl-db/) | Schema for the LTL Postgres database. | Plain SQL |
| [`deploy/`](deploy/) | Compose + reverse proxy for a single VPS. | Docker Compose, Caddy |
| [`interop/`](interop/) | Runs a real handshake between the JavaScript and Python implementations, so the two halves of the protocol can't drift apart unnoticed. | Node + Python |

Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) first. The wire format
lives in [`docs/PROTOCOL.md`](docs/PROTOCOL.md), the honest threat model in
[`docs/SECURITY.md`](docs/SECURITY.md), and the plan/entitlement model in
[`docs/BILLING.md`](docs/BILLING.md).

---

## The one-paragraph version

The household installs the `ltl_remote` plugin. On enable it generates a
P-256 keypair whose private half never leaves the house, then shows an
eight-word pairing code in the Domovoi dashboard. The owner types that
code into the LTL web app while signed in, which binds the household to
their account. From then on the plugin holds a WebSocket open to the LTL
relay. When you open your dashboard from a phone on cellular, your
browser performs an authenticated ECDH handshake **with the home server**
— not with LTL — and every request and response after that is AES-GCM
sealed. LTL routes frames between two sockets, meters the bytes for
billing, and enforces your subscription. It cannot read your dashboard,
your library, or your voice.

## Why it is built this way

**Outbound-only.** The home server dials the relay. No port forwarding,
no dynamic DNS, no UPnP, and it works behind CGNAT and double NAT — which
is most residential internet. The household's firewall keeps its default
inbound-deny posture.

**End-to-end encrypted.** Domovoi's whole pitch is that your house stays
yours. A relay that terminates TLS and reads plaintext would quietly
undo that, so LTL does not: it forwards sealed frames between two
endpoints that authenticated each other. This costs real complexity —
[`docs/PROTOCOL.md`](docs/PROTOCOL.md) is longer than it would otherwise
be, and browsers have to do key management — and it is worth it.

**The trust anchor stays local.** A device is authorized to reach your
house by *your Domovoi dashboard*, not by the LTL account system. Signing
into LTL gets you a route; it does not get you into the house. If LTL is
compromised tomorrow, an attacker can deny you service and see traffic
volumes. It cannot approve a device.

**Voice belongs to core, not here.** Talking to Domovoi from your phone
is a Domovoi feature — mic capture, codecs, barge-in, and the answer
coming back are core plus Android work. This plugin does not implement
any of it. What it does is carry `WS /v1/stream/{room_id}` frames
faithfully, so when core ships phone voice, remote phone voice works with
no change here.

## Repository conventions

`plugin-ltl-remote/` follows Domovoi's rules exactly — the manifest
contract, the band table, the core/web process split, migration-only
schema changes, and the reserved-token branding gate in
[`CLAUDE.md`](../CLAUDE.md).

`ltl-backend/` and `ltl-db/` follow the conventions already established
in Lazy Thumb Labs' Scooped codebase: Spring Boot with a
controllers/services/repositories/entities layering, a `JwtAuthFilter`
that accepts a bearer token or a session cookie, Stytch for identity,
Stripe for payments, an `ApiResponse<T>` envelope on every `/api/v1`
response, configuration from the environment with no checked-in
`application.properties`, and a multi-stage Temurin build.

`ltl-frontend/` follows the same front-end idiom: server-agnostic
static pages, hand-written CSS, and vanilla ES modules with no build
step or npm dependency tree.
