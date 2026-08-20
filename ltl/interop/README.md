# Cross-implementation interop check

`ltl-remote/v1` has three implementations. Two of them do the
cryptography: the Domovoi plugin (Python) and the browser client
(JavaScript). Unit tests on either side prove each is self-consistent,
which is not the same as proving they agree.

This harness runs a real handshake between them — the actual
`ClientHandshake` from `ltl-frontend/js/e2e.js` against the actual
`HomeHandshake` from the plugin's `crypto.py`, over a pipe — then seals
traffic in both directions and checks that a replayed frame is refused.

```bash
node interop/client.mjs
```

Expected output:

```
handshake: OK (mutual confirmation verified both ways)
client -> home: OK (...)
home -> client: OK (...)
replay rejected: OK (replayed or reordered frame)

JS and Python implementations interoperate.
```

Requires Node 18+ (for WebCrypto) and the `cryptography` package.
`plugin-ltl-remote/tests/test_interop.py` runs this from pytest and skips
when Node is unavailable.

If this fails, one of two files changed without the other:
`ltl-frontend/js/e2e.js` or
`plugin-ltl-remote/domovoi_plugin_ltl_remote/crypto.py`. The protocol
they both implement is described in [`../docs/PROTOCOL.md`](../docs/PROTOCOL.md).
