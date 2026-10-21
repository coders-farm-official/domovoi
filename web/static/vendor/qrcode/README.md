# QR Code Generator for JavaScript (vendored, pinned 1.4.4)

Settings → Rooms → **Add a spare device** draws the enrolment payload
(`domovoi-room:1?…`, see `docs/uml/satellite-protocol.md`) as a QR code for
a phone to scan. `qrcode.js` here is that library, served from this box like
every other vendored script, so the dashboard makes no external request. It
is loaded only when that card opens.

* Package: `qrcode-generator` 1.4.4 on npm, by Kazuhiko Arase.
* Licence: MIT (`LICENSE.txt`; the file's own header says the same).
* Tarball: `https://registry.npmjs.org/qrcode-generator/-/qrcode-generator-1.4.4.tgz`,
  npm integrity `sha512-HM7yY8O2ilqhmULxGMpcHSF1EhJJ9yBj8gvDEuZ6M+KGJ0YY2hKpnXvRD+hZPLrDVck3ExIGhmPtSdcjC+guuw==`
  (checked on download, 2026-10-13).
* `qrcode.js` is `package/qrcode.js` from that tarball, unchanged:
  sha256 `18ae399f81182bc9de916e9c77b195df20cc58d6f2d55a62b085a299f1bf1780`
  (`domovoi/tests/test_web_spare_device_enrol.py` holds it to this).

Used through `qrcode(0, 'M')`, `addData`, `make`, `getModuleCount` and
`isDark`; the card draws the modules itself as SVG rectangles.
