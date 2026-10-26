# Yjs (vendored, one copy for the whole dashboard)

`yjs.min.js` sets two globals:

- `window.Y`: yjs 13.6.33 (`Y.Doc`, `Y.UndoManager`, `Y.encodeStateAsUpdate`, ...).
- `window.YKit`: the helpers that share that same `Y`:
  `awareness` and `sync` (y-protocols 1.0.7), `encoding` and `decoding`
  (lib0 0.2.119), `WebsocketProvider` (y-websocket 3.1.0, client only),
  `IndexeddbPersistence` (y-indexeddb 9.0.12), and `versions`.

The Documents editor bundle (`../tiptap/tiptap.min.js`) carries
y-prosemirror 1.3.7 bound to this `window.Y`, so load this file **first**.
A page must never load a second Yjs: two copies break Yjs's own type checks.

Exact versions and licences of everything inside: `THIRD-PARTY.json`;
licence texts: `licenses/`. All MIT.

Rebuild (one-time step for the repo, never on a server):

```
cd scripts/vendor/editors
npm ci --ignore-scripts
node build.mjs
```
