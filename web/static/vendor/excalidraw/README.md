# Excalidraw (vendored, pinned 0.17.6 UMD)

The Drawings page (`web/static/drawings.jsx`) loads Excalidraw from this
directory — `/vendor/excalidraw/excalidraw.production.min.js` plus the
`excalidraw-assets/` fonts and locales it lazy-loads via
`window.EXCALIDRAW_ASSET_PATH`. Like React, Babel, lucide, marked and
x-spreadsheet, it is served from this box so the dashboard makes zero
external requests.

**Why 0.17.6:** Excalidraw went ESM-only at 0.18.0. The dashboard has no
bundler (UMD + Babel-in-browser), so 0.17.6 — the last UMD release — is
pinned, and must stay in lockstep with `EXCALIDRAW_VERSION` in
`drawings.jsx` and in `scripts/vendor_excalidraw.py`.

**Why there is no `dist/` here:** the packaging section of `.gitignore`
ignores `dist/` at *any* depth. The bundle used to be referenced at
`/vendor/excalidraw/dist/…`, so it could never be committed, and every
clone 404'd with the editor stuck on "Loading Excalidraw…" (finding F-003).
The path is flat now — same shape as the other vendored libraries — and
`.gitignore` explicitly un-ignores `web/static/vendor/**`.

## Populating it

```
python scripts/vendor_excalidraw.py
git add web/static/vendor/excalidraw && git commit
```

That downloads the pinned npm tarball once and unpacks `package/dist/`
(minus the dev-only asset copy) here. It is a one-time step for the repo,
not a build step for each box: once committed, a fresh clone is complete.
