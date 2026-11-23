# xterm.js 5.5.0 and its fit add-on 0.10.0 (vendored)

`xterm.min.js` is `@xterm/xterm` 5.5.0's `lib/xterm.js` (sets
`window.Terminal`), `xterm.css` its `css/xterm.css`, and
`addon-fit.min.js` is `@xterm/addon-fit` 0.10.0's `lib/addon-fit.js` (sets
`window.FitAddon`). Both MIT; licence texts in `licenses/`, versions in
`THIRD-PARTY.json`. Copied byte for byte from the npm packages, with only
the trailing `sourceMappingURL` comment removed (the maps are not shipped).

Loaded only by the satellite support-session terminal
(`web/static/support_session.jsx`), on demand, never by any other page. No
other add-on ships: no links add-on and no clipboard (OSC 52) add-on, and
`windowOptions` is left all-off (the default). Terminal output is drawn by
xterm only; it never becomes HTML.

Update: `npm pack @xterm/xterm@<v> @xterm/addon-fit@<v>`, copy the same
three files and both LICENSE files, and change the versions here, in
`THIRD-PARTY.json` and in docs/LICENSES.md.
