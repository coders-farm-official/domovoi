# LTL Remote — Domovoi plugin

Slug `ltl_remote`. Holds an outbound, end-to-end encrypted link so
devices you approve can reach this Domovoi server from outside the
house — with no port forwarding and nothing readable by the relay.

Design docs live one level up: [architecture](../docs/ARCHITECTURE.md),
[wire protocol](../docs/PROTOCOL.md), [security](../docs/SECURITY.md).

---

## What it adds

| Plane | What |
|---|---|
| Voice | One handler at band 265 — "is remote access on", "who can access domovoi remotely", "turn off remote access" (asks first). |
| Background | `ltl_relay_link` (long-run) holds the relay socket; `ltl_remote_reaper` (poll) enforces retention. |
| Data | Schema `plugin_ltl_remote`: link state, approved devices, a metadata-only access log. |
| HTTP (core) | `/v1/plugins/ltl_remote/…` — pairing, device approval, token rotation, unlink. Admin-gated. |
| Dashboard | A **Remote Access** page: pairing code, fingerprint, devices, usage, activity. |
| Config | `LTL_*` settings on the Settings page. |

## Install and pair

1. Install and enable the plugin. On enable it generates the household
   keypairs in `~/.domovoi/plugins/data/ltl_remote/keys/` (mode `0600`)
   and prints the fingerprint to the log.
2. Open **Remote Access** in the dashboard and press *Get a pairing
   code*. You get eight words; only their hash goes to LTL.
3. Sign in at lazythumblabs.com, choose *Add a household*, enter the
   code. The link comes up on its own within a few seconds.
4. Add a device in the LTL app. It appears here as **pending**.
   Compare its fingerprint with the one on this page, then approve it.

Step 4 is the security-relevant one. Approving a device grants it what a
device on your LAN has; the fingerprint comparison is what stops a
substituted key. See [SECURITY.md](../docs/SECURITY.md) for the honest
version, including what trust-on-first-use does and does not cover.

## Settings worth knowing

| Setting | Default | Why you'd change it |
|---|---|---|
| `LTL_READ_ONLY` | off | Drops every remote request that would change something. Look, don't touch. |
| `LTL_ALLOW_CORE_ADMIN` | on | Off restricts remote access to the dashboard, removing remote plugin installation (i.e. remote code execution) from the picture. |
| `LTL_ALLOW_MEDIA_STREAMING` | on | Off stops remote library playback — the thing that actually consumes your data allowance. |
| `LTL_ACCESS_LOG_RETENTION_DAYS` | 30 | 0 keeps the access log forever. |
| `LTL_RELAY_URL` / `LTL_API_BASE` | LTL production | Point at a staging or self-hosted relay. |

## What it will and will not forward

Allowlisted, checked before any local socket opens:
`/api/**`, `/ws/state`, `/plugins/**`, `/static/**`, `/assets/**`,
`/media/**` (dashboard) and `/v1/**`, `/v1/stream/**` (core). Everything
else is refused. The two local origins must be loopback or private
addresses — the plugin refuses to load otherwise, so a settings typo
cannot turn your server into an open proxy.

The plugin holds **no Domovoi credential**. A remote user logs into the
dashboard through the tunnel exactly as they would on the LAN.

## Phone voice

Not implemented here, and deliberately so. Talking to Domovoi from your
phone is core plus Android work — capture, codecs, barge-in. What this
plugin does is carry `WS /v1/stream/{room_id}` faithfully, so when core
ships it, it works remotely with no change to this plugin.

## Development

```bash
domovoi plugin dev ltl/plugin-ltl-remote     # register in place
pytest ltl/plugin-ltl-remote/tests           # unit tiers need no DB
```

The protocol modules — `crypto.py` and `framing.py` — are pure functions
over bytes with no I/O, and `local_proxy.resolve_route` is a pure
allowlist decision. That is what lets the interesting parts be tested
exhaustively without a network, a relay, or a database.

Regenerate the hashed lockfile after touching `requirements.in`:

```bash
pip-compile --generate-hashes --output-file=requirements.lock requirements.in
```
