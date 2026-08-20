# ltl-frontend

The Lazy Thumb Labs web app: marketing, pricing, sign-in, account
management, and the remote console that talks to a household over the
end-to-end encrypted tunnel.

Static HTML, one hand-written stylesheet, vanilla ES modules. No build
step, no npm dependency tree — the same front-end idiom as `scooped-web`,
minus Thymeleaf, because this app is served as static files by Caddy
rather than rendered by the Spring process.

Not styled like Domovoi, deliberately: the amber-and-cat design system
belongs to the dashboard this product connects you to, not to the product
itself.

---

## Files

| File | What it does |
|---|---|
| `js/e2e.js` | **The important one.** The browser half of `ltl-remote/v1` — ECDH P-256, HKDF, AES-GCM, HMAC, all native WebCrypto. Mirrors the plugin's `crypto.py` exactly. |
| `js/tunnel.js` | Inner framing plus the relay socket, wrapped in a `fetch`-shaped API. Device keys live in IndexedDB; household keys are pinned on first use. |
| `js/api.js` | The LTL control-plane client. Unwraps the `{data, error}` envelope. |
| `js/app.js` | Account dashboard: households, devices, usage, billing. |
| `js/pricing.js` · `js/signin.js` · `js/remote.js` | One module per page. |
| `css/styles.css` | The whole design system: tokens, light and dark, ~320 lines. |

## Running it

Any static server, pointed at this directory:

```bash
python3 -m http.server 5173
```

The API and relay default to the page's own origin, which is what the
Caddy config in [`../deploy`](../deploy) sets up. To point at a backend
somewhere else, set the globals before the module scripts load:

```html
<script>
  window.LTL_API_BASE = 'https://api.lazythumblabs.com';
  window.LTL_RELAY_URL = 'wss://relay.lazythumblabs.com/relay/v1/client';
</script>
```

## Why there is no build step

Because the security-relevant file benefits most from being readable.
`e2e.js` has to agree byte for byte with a Python implementation; anyone
auditing it should be able to open it in a browser's devtools and see the
same text that is in the repo, without a source map in between.

Everything it uses — ECDH P-256, HKDF-SHA256, AES-256-GCM, HMAC — is
native WebCrypto, so there is nothing to bundle in the first place. P-256
rather than X25519 for the same reason: it has been in every shipping
browser for a decade, and a key agreement that fails on a two-year-old
phone is not a security feature.

## What the remote console does, and does not, do

`remote.html` opens a real tunnel, shows the negotiated fingerprint for
comparison, and issues real requests to the Domovoi dashboard API
through it. It also has a button that asks for a blocked path, so you can
watch the household refuse it.

It is **not** a mirror of the whole Domovoi dashboard. Doing that means
intercepting the dashboard's own relative `fetch` calls and routing them
through the tunnel, which needs a Service Worker scoped to a proxy path
plus an iframe pointing into it. That is the natural next step and it
would use the same `Tunnel` class unchanged — the tunnel is the part that
has to be right first, and it is finished and tested.

Shipping a half-working iframe mirror would have looked more impressive
in a screenshot and been worse to build on.

## Device keys

A device keypair is generated on first use with `extractable: false` and
stored as a `CryptoKey` in IndexedDB. Page script — including `e2e.js`
itself — can ask the browser to use it but cannot read it out.
`localStorage` could not do this: it stores strings, and a string is by
definition extractable.

This does not protect against someone holding your unlocked laptop.
Treat an approved device as a house key, and revoke it from the Domovoi
dashboard if you lose it. [`../docs/SECURITY.md`](../docs/SECURITY.md)
says the same thing at more length.

## Keeping the two halves honest

`js/e2e.js` and the plugin's `crypto.py` are one protocol in two
languages. [`../interop`](../interop) runs an actual handshake between
them and fails if they have drifted:

```bash
node ../interop/client.mjs
```
