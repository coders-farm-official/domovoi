# mammoth 1.13.0 (vendored)

`mammoth.min.js` sets `window.mammoth`: Word (.docx) to HTML in the
browser. The Documents editor loads it only when a .docx is opened, and
the HTML it makes goes through the same sanitiser as every other document.

mammoth is BSD-2-Clause. Inside it: jszip 3.10.2 (MIT or GPL-3.0, used
under MIT; its prebuilt browser file carries pako, lie, immediate and
setimmediate), @xmldom/xmldom, lop, option, dingbat-to-unicode, underscore,
xmlbuilder, base64-js. Exact versions: `THIRD-PARTY.json`; licence texts:
`licenses/`.

Rebuild: `scripts/vendor/editors/build.mjs` (see `../yjs/README.md`).
