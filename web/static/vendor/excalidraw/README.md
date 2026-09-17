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

## The "Google API Key" secret-scanning alert

GitHub secret scanning flags a `google_api_key` inside
`excalidraw.production.min.js`. That string is Excalidraw's own Firebase
*client* config (`VITE_APP_FIREBASE_CONFIG`, Firebase project
`excalidraw-room-persistence`), which the upstream 0.17.x build inlines
into the npm package; the vendored file is byte-identical to the published
tarball (compare `sha256sum` against `package/dist/` from the tarball). It
is not a Domovoi credential, nothing in this repo owns or can rotate it,
and the bundle never initializes Firebase: only the excalidraw.com app
uses that config, for its collaboration rooms. Firebase web API keys are
public identifiers by design; Google's own guidance is that keys
restricted to Firebase services "do not need to be treated as secrets, and
it's safe to include them in your code or configuration files".

Close the alert as a false positive with a note pointing here, and do the
same if a re-vendor reopens it. A `paths-ignore` entry for
`web/static/vendor/**` in `.github/secret_scanning.yml` would stop GitHub
raising it at all, at the cost of not scanning the vendor tree; that is a
deliberate maintainer choice, not something this README assumes.
