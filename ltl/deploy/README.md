# deploy

Docker Compose and Caddy for a single VPS. Three containers: Caddy for
TLS and static hosting, the Spring backend for both the control plane and
the relay, and Postgres.

```bash
cp .env.example .env        # fill it in — POSTGRES_PASSWORD and LTL_DOMAIN are required
docker compose up -d
docker compose logs -f backend
```

Point an A record at the box first; Caddy provisions the certificate on
first boot and will fail loudly if DNS is not there yet.

---

## Sizing

The relay's cost is memory per connected household, not CPU per request.
A household's agent socket is mostly idle, and Java 21 virtual threads
mean each one costs a socket buffer rather than a thread stack. A 2 vCPU
/ 4 GB box comfortably holds low thousands of households; the first thing
to watch is not CPU but egress bandwidth, which is also the thing
customers are billed for.

## Two settings that are not cosmetic

**`X-Forwarded-For` must be set by the proxy.** `POST /api/v1/enroll` is
unauthenticated by design — a Domovoi server opening a pairing window has
no account yet — and is rate limited per source address. Behind a proxy
that does not set this header, every request looks like it came from the
proxy and the limit protects nothing. Caddy sets it by default on
`reverse_proxy`; the `Caddyfile` says so, so nobody removes it.

**No timeouts on `/relay/*`.** Agent sockets are held open for days and
are idle most of that time. A read timeout there produces a reconnect
storm, not a saving.

## Stripe webhooks

Point a Stripe webhook at `https://<your domain>/api/webhooks/stripe`
for these events, and put the signing secret in `STRIPE_WEBHOOK_SECRET`:

```
checkout.session.completed
customer.subscription.updated
customer.subscription.deleted
invoice.payment_succeeded
invoice.payment_failed
```

With no secret configured the backend **refuses** webhooks rather than
trusting them, which is deliberate — processing unverified events would
let anyone who can reach the URL grant themselves a subscription.

## Backups

The only stateful volume is `pgdata`. Everything else — the frontend, the
jar, Caddy's certificates — is rebuilt or re-fetched.

```bash
docker compose exec postgres pg_dump -U ltluser ltldb | gzip > ltl-$(date +%F).sql.gz
```

Worth being clear about what a database loss costs a customer: their
account, their pairing, and their usage history. It does **not** cost
them their Domovoi server, their data, or their household keys — those
live at home, and this database holds only public keys. Re-pairing after
a restore is eight words, not a migration.

## Scaling past one box

`RelayRegistry` is in-process: it maps a household to the socket this JVM
holds. A second backend instance therefore needs either a shared registry
(Redis) or a routing layer that pins a household to a node. The second is
simpler and matches the traffic shape — a household's agent and its
clients must land on the same instance anyway.

Doing that before there is a second instance would be pure cost, which is
why it is not done here. The seam is one class.
