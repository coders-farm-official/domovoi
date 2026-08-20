# LTL Remote wire protocol — `ltl-remote/v1`

Three implementations must agree on this document, byte for byte:

| Side | Implementation |
|---|---|
| Home server | `plugin-ltl-remote/domovoi_plugin_ltl_remote/{crypto,framing}.py` |
| Relay | `ltl-backend/.../relay/` — outer framing only; the relay never implements the sealed layer |
| Client | `ltl-frontend/js/e2e.js` (browser), Android app (later) |

All multi-byte integers are **big-endian**. All base64 in JSON is
**unpadded base64url**. Elliptic-curve points are **P-256 uncompressed
SEC1** (65 bytes, `0x04 || X || Y`).

---

## 1. Layers

```
WebSocket binary message
└── outer frame        ← the relay reads this, and only this
    └── sealed frame   ← AES-256-GCM; the relay cannot open it
        └── inner frame ← HTTP request/response or tunneled WebSocket
```

The relay is a router with a byte counter. Everything it is allowed to
know is in the outer frame: a version, an opcode, and a link id. It has
no key that opens the layer below.

## 2. Outer frame

### 2.1 On the agent socket (`WSS /relay/v1/agent`)

One household holds one socket; many client links multiplex over it, so
the frame carries the link id.

```
 offset  size  field
 0       1     version      = 0x01
 1       1     opcode
 2       16    link_id      relay-assigned, 16 random bytes
 18      ...   payload      opaque
```

| Opcode | Name | Direction | Payload |
|---|---|---|---|
| `0x01` | `LINK_OPEN` | relay → agent | JSON: `{"device_id": "...", "ip_country": "US"}` |
| `0x02` | `LINK_DATA` | both | a sealed frame (§4), or a handshake message (§3) while the link is unsealed |
| `0x03` | `LINK_CLOSE` | both | JSON: `{"code": "...", "reason": "..."}` |
| `0x10` | `CONTROL` | both | JSON, `link_id` all-zero — see §6 |

### 2.2 On the client socket (`WSS /relay/v1/client`)

One socket is one link, so there is no link id to carry. Client frames
are the payload alone: a handshake message or a sealed frame. The relay
attaches and strips the outer header on the agent side.

A relay that ever *modifies* a payload byte is a bug, and both endpoints
detect it — the sealed layer is authenticated, so tampering surfaces as a
decryption failure rather than as corrupted data.

## 3. Handshake

Mutual authentication with forward secrecy, in three messages, sent
unsealed as JSON over `LINK_DATA`. It is a Noise `KK` pattern with an
added ephemeral-ephemeral exchange, written out concretely rather than
pulled from a Noise library, because the browser side has to run with no
build step and no dependencies beyond WebCrypto.

### 3.1 Prerequisites

The client must already hold:

* `static_s_dh` — the household's P-256 ECDH public key, fetched from LTL
  at pairing time and **pinned** thereafter (§7);
* its own device keypair `static_c`, whose public half the household
  admin has approved on the Domovoi dashboard.

The home server must already hold the approved `static_c` for
`device_id`. If it does not, it answers `ERROR / DEVICE_NOT_APPROVED` and
closes — it does not complete a handshake with an unknown device.

### 3.2 Messages

**1 — `client_hello` (client → home)**

```json
{
  "t": "client_hello",
  "v": 1,
  "device_id": "d_7f3a…",
  "device_pub": "BASE64URL(65-byte SEC1)",
  "eph_pub": "BASE64URL(65-byte SEC1)",
  "nonce_c": "BASE64URL(16 bytes)"
}
```

**2 — `home_hello` (home → client)**

```json
{
  "t": "home_hello",
  "v": 1,
  "household_fp": "A1B2 C3D4 …",
  "eph_pub": "BASE64URL(65-byte SEC1)",
  "nonce_s": "BASE64URL(16 bytes)",
  "confirm": "BASE64URL(32 bytes)"
}
```

**3 — `client_confirm` (client → home)**

```json
{ "t": "client_confirm", "v": 1, "confirm": "BASE64URL(32 bytes)" }
```

After message 3 verifies, the link is **sealed**: every subsequent
`LINK_DATA` payload is a sealed frame, and a plaintext JSON message on a
sealed link is a protocol violation that closes the link.

### 3.3 Key schedule

```
transcript = SHA-256( b"ltl-remote/v1"
                    || device_pub || eph_c_pub || nonce_c
                    || static_s_dh_pub || eph_s_pub || nonce_s )

dh1 = ECDH(eph_c,    eph_s)        # forward secrecy
dh2 = ECDH(eph_c,    static_s_dh)  # authenticates the home server
dh3 = ECDH(static_c, eph_s)        # authenticates the device

prk = HKDF-Extract(salt = transcript, ikm = dh1 || dh2 || dh3)

k_c2h    = HKDF-Expand(prk, b"ltl-remote/v1 c2h",     32)
k_h2c    = HKDF-Expand(prk, b"ltl-remote/v1 h2c",     32)
k_confirm= HKDF-Expand(prk, b"ltl-remote/v1 confirm", 32)
```

All HKDF is HMAC-SHA-256. Confirmation tags:

```
confirm_home   = HMAC-SHA-256(k_confirm, b"home")
confirm_client = HMAC-SHA-256(k_confirm, b"client")
```

Each side compares in constant time and aborts on mismatch. Because
`dh2` needs the household private key and `dh3` needs the device private
key, a correct `confirm_home` proves the peer is the household and a
correct `confirm_client` proves the peer is the approved device. The
relay possesses neither and therefore cannot produce either tag.

### 3.4 Why an ephemeral-ephemeral term

`dh2 || dh3` alone would be a static-static-ish exchange: identical for
every session, so one leaked private key would retroactively open every
recorded session. `dh1` makes each session's key depend on two ephemeral
scalars that are discarded when the link closes.

## 4. Sealed frame

```
 offset  size  field
 0       8     counter    uint64, per-direction, starts at 0, strictly increasing
 8       ...   ciphertext AES-256-GCM output (16-byte tag appended)
```

* Key: `k_c2h` for client → home, `k_h2c` for home → client.
* Nonce (12 bytes): `direction_prefix(4) || counter(8)`, where the prefix
  is ASCII `"C2H_"` or `"H2C_"`.
* AAD: `version(1) || direction_prefix(4) || counter(8)`.
* The receiver rejects a counter that is not strictly greater than the
  last accepted one, which makes replay and reordering fail closed.
* A link is closed and rekeyed by reconnecting well before `2^32` frames;
  neither side ever reuses a counter under a key.

## 5. Inner frame

```
 offset  size  field
 0       1     type
 1       4     stream_id   uint32, client-allocated, odd; 0 = link-level
 5       4     header_len  uint32
 9       ...   header      UTF-8 JSON, header_len bytes
 9+hl    ...   body        raw bytes to end of frame
```

| Type | Name | Header |
|---|---|---|
| `0x01` | `REQ` | `{"method","path","headers":{},"streaming":bool}` |
| `0x02` | `REQ_CHUNK` | `{}` |
| `0x03` | `REQ_END` | `{}` |
| `0x11` | `RES_HEAD` | `{"status":200,"headers":{}}` |
| `0x12` | `RES_CHUNK` | `{}` |
| `0x13` | `RES_END` | `{}` |
| `0x21` | `WS_OPEN` | `{"path","headers":{}}` |
| `0x22` | `WS_OPEN_OK` | `{}` |
| `0x23` | `WS_DATA` | `{"binary":bool}` |
| `0x24` | `WS_CLOSE` | `{"code":1000,"reason":""}` |
| `0x31` | `PING` | `{"ts":1699999999}` |
| `0x32` | `PONG` | `{"ts":1699999999}` |
| `0x41` | `ERROR` | `{"code":"…","message":"…"}` |

### 5.1 Bodies and streaming

A response is `RES_HEAD`, then zero or more `RES_CHUNK`, then `RES_END`.
The home server never buffers a whole response: media and library
downloads are read from the local socket and sealed chunk by chunk, with
a 64 KiB chunk size chosen so one chunk is well inside a comfortable
WebSocket message size. This is what keeps a two-hour audiobook from
becoming a two-hour memory allocation.

### 5.2 Header hygiene

The home server strips hop-by-hop headers (`connection`,
`keep-alive`, `transfer-encoding`, `upgrade`, `proxy-*`) in both
directions, and rewrites `host` to the local origin it is dialing. It
never forwards a `Set-Cookie` domain that would scope a Domovoi session
cookie to an LTL domain.

### 5.3 Error codes

`DEVICE_NOT_APPROVED`, `PATH_NOT_ALLOWED`, `LOCAL_UNREACHABLE`,
`BODY_TOO_LARGE`, `TOO_MANY_STREAMS`, `PROTOCOL_ERROR`,
`QUOTA_EXCEEDED`, `SUBSCRIPTION_INACTIVE`, `HOUSEHOLD_OFFLINE`.

The last three originate at the **relay**, which delivers them as
plaintext `LINK_CLOSE` — the relay cannot seal a frame, so anything it
needs to say it says in the clear. Clients render these differently from
sealed `ERROR` frames precisely because they come from a party that
cannot be authenticated end to end.

## 6. Control frames

`CONTROL` (`0x10`) carries relay ↔ agent housekeeping as JSON, outside
any link:

| `t` | Direction | Purpose |
|---|---|---|
| `hello` | agent → relay | `{household_id, agent_version, plugin_version, sig}` — see §8 |
| `hello_ok` | relay → agent | `{server_time, heartbeat_sec, max_links, plan}` |
| `heartbeat` | both | liveness; the relay drops an agent that misses two |
| `quota` | relay → agent | `{used_bytes, limit_bytes, period_end}` — surfaced on the dashboard |
| `revoke` | relay → agent | `{reason}` — subscription ended; close cleanly |
| `device_pending` | relay → agent | a new device registered and awaits local approval |

## 7. Key pinning and fingerprints

The household publishes two P-256 public keys at enrollment: `dh` (used
in §3) and `sig` (used in §8). The **fingerprint** covers both:

```
fp_bytes = SHA-256( b"ltl-remote/v1 fp" || dh_pub || sig_pub )[:16]
fp_text  = uppercase hex of fp_bytes, in eight space-separated groups of four
```

Example rendering: `3F9A 21C4 8E10 77BB 05D2 6A3E C918 44F7`.

The Domovoi dashboard shows this on its Remote Access page. Every client
shows the fingerprint it actually negotiated. **They must match.** The
client pins the key on first successful handshake and refuses to connect
if it ever changes, showing both fingerprints and requiring an explicit
re-pin.

This is trust-on-first-use, with the same limits TOFU always has: it
protects every session after the first, and the *first* one is protected
only if a human compares the two strings. [SECURITY.md](SECURITY.md) says
so plainly rather than implying the fingerprint is decorative.

## 8. Agent authentication to the relay

Separate from, and weaker than, the E2E layer — this only decides who may
occupy a household's agent slot, not who may enter the house.

1. The relay sends a 32-byte challenge on connect.
2. The agent replies in its `hello` with
   `sig = ECDSA-P256-SHA256(household_sig_priv, challenge || household_id)`
   and the bearer relay token issued at claim time.
3. The relay verifies the signature against the stored `sig` public key
   **and** the token hash. Both must pass.

The token alone would make a stolen token sufficient; the signature alone
would make key rotation painful. Requiring both means a leaked token is
useless without the household's private key, and a rotated token
invalidates old agents immediately.

## 9. Versioning

`version` is the first byte of every outer frame, and `v` appears in
every handshake message. A peer that sees a version it does not implement
closes with `PROTOCOL_ERROR` rather than guessing. The relay is version-
agnostic by construction — it routes any version it can parse an outer
header for, so client and home can move to `v2` without a relay deploy.
