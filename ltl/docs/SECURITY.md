# LTL Remote — security and privacy

Written the same way Domovoi's own
[SECURITY_PRIVACY.md](../../docs/SECURITY_PRIVACY.md) is written: against
the shipped code, with the accepted risks stated out loud instead of
implied.

---

## The threat model in one paragraph

LTL Remote extends Domovoi's LAN-trust model across the internet **for
devices the household explicitly approved, and for no one else**. LTL
operates the relay and can therefore see that your house is online, how
many devices connect, and how many bytes move — but not what they say.
The people who can get into your house are exactly: whoever holds an
approved device's private key, plus whoever can approve a new device
(which requires the Domovoi admin password on your own dashboard). LTL
cannot approve a device. LTL can, however, deny you service, and if LTL
is fully compromised at the moment you first pair a device, it can
attempt a key substitution that a user who compares fingerprints will
catch and a user who does not will not.

## What each party can do

| Party | Can | Cannot |
|---|---|---|
| **LTL (the relay)** | Route frames; count bytes; see household online/offline, device ids, connection times, source IP and coarse geography; refuse service | Read any request or response; approve a device; forge a handshake; recover past sessions from a database dump |
| **An approved device** | Everything a LAN device can do, including Domovoi's admin tier if it has the admin password | Nothing beyond what the plugin's allowlist forwards |
| **A signed-in LTL account holder** | Manage billing; see household metadata; register a device *for approval* | Enter the house until the household admin approves that device locally |
| **A network attacker** | See TLS-shaped traffic to LTL | Read the tunnel; TLS wraps a second, independent encryption layer |
| **A compromised relay** | Deny service; observe metadata; attempt first-pairing key substitution | Decrypt an established session, retroactively or otherwise |

## What is actually encrypted

Everything above the outer frame. The relay handles
`version | opcode | link_id | opaque`, and the opaque part is
AES-256-GCM under a key derived from a three-term Diffie-Hellman the
relay does not take part in ([PROTOCOL.md §3](PROTOCOL.md#3-handshake)).
Session keys are held in memory only and are discarded when a link
closes, so forward secrecy is real: recording today's ciphertext and
stealing the household's private key next year does not open it.

## The two places a human decision matters

Automated security ends at these two points, and the product is designed
so they are short, rare, and legible.

**1. Comparing the fingerprint.** The Domovoi dashboard shows the
household key fingerprint; each client shows the fingerprint it
negotiated. They must match. This is the only defense against a
malicious LTL substituting its own key during a first pairing. After the
first successful handshake the client pins the key and a change is a hard
failure requiring an explicit re-pin.

**2. Approving a device.** A device registered through the LTL web app
arrives on the household's own Remote Access page as *pending*, with its
fingerprint and the name the registrant gave it. It cannot reach anything
until an admin approves it there. This is the seam that keeps LTL out of
the trust decision.

## Accepted risks, stated plainly

**The tunnel reaches the admin tier.** The allowlist forwards `/v1/**`,
which includes Domovoi's admin endpoints — plugin installation among them,
and plugin installation is code execution. An approved remote device with
the admin password can do that from anywhere. This is the feature, not an
oversight. If you do not want it, the plugin has a `read_only` setting
that drops every non-GET request at the home server; it is off by default
because a dashboard you cannot press buttons on is not much of a
dashboard.

**Trust-on-first-use has a first use.** See above. We are not going to
pretend a fingerprint nobody reads is a cryptographic guarantee.

**A hand-written protocol is a risk.** [PROTOCOL.md](PROTOCOL.md) is a
Noise `KK` pattern written out longhand rather than a Noise library,
because the browser client runs with no build step and WebCrypto gives us
P-256, HKDF and AES-GCM natively. The construction is conservative and
the primitives are standard, but a custom assembly of standard primitives
is still a custom assembly. Two things follow: `crypto.py` and `e2e.js`
are deliberately small and test-vector-driven, and if this ships to real
customers it should get an outside review before it gets a marketing
page.

**Metadata is not private.** LTL knows when you are home-adjacent, how
often you connect, and roughly from where. A relay cannot route without
knowing that much.

**Device private keys live where the browser puts them.** In the browser
they are non-extractable WebCrypto keys in IndexedDB — safe from page
script, not safe from someone with your unlocked laptop. Treat an
approved device as equivalent to a house key, and revoke it from the
Domovoi dashboard when you lose the device.

**The relay token is a bearer token.** It is stored as a SHA-256 hash at
LTL and is useless without the household's signing key
([PROTOCOL.md §8](PROTOCOL.md#8-agent-authentication-to-the-relay)), but
it is still a secret in a config file on the home server, with the same
exposure as anything else in `~/.domovoi/`.

## What the household stores

| Item | Where | Notes |
|---|---|---|
| Household DH + signing private keys | `~/.domovoi/plugins/data/ltl_remote/keys/`, mode `0600` | Never transmitted. Regenerating them un-pairs every client. |
| Relay token | the plugin's `.env` via the core config bridge | Rotatable from the dashboard |
| Approved devices | `plugin_ltl_remote.remote_devices` | Public keys only |
| Access log | `plugin_ltl_remote.remote_access_log` | Method, path, status, byte counts, device — **no bodies, no query strings for paths flagged sensitive**; retention is a setting, default 30 days |

The plugin logs *that* a request happened, never what it contained. The
access log exists so you can answer "what did that device do while I was
away", which is a reasonable question to be able to answer about your own
house.

## What LTL stores

Accounts, households (id, name, public keys, fingerprint, online state),
devices (id, label, public key, approval state), subscription and
invoice references, and per-period byte counters. No request paths, no
bodies, no headers — the relay never parses them, so there is nothing to
retain even accidentally.

## Deliberately deferred

Same posture as Domovoi v1: name it rather than imply it is handled.

* **No post-quantum layer.** A recorded session is safe against classical
  attack; a future CRQC changes that. A hybrid X25519+ML-KEM handshake is
  the obvious v2 and is why §9 of the protocol is versioned.
* **No per-device capability scoping.** A device is approved or it is
  not; it cannot be approved for "media only". The allowlist is
  household-wide, not per-device.
* **No relay-side abuse detection.** The relay meters bytes and enforces
  entitlements; it does not attempt to detect a household being used as
  someone else's CDN, because it cannot see what is moving.
* **No independent transparency log** for household public keys, which is
  what would turn TOFU into something stronger.
