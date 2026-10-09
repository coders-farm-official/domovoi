# Brand assets

| File | What it is | Where it goes |
|---|---|---|
| `banner.jpg` | The 1376×768 header art | Top of the repo `README.md` |
| `social-preview.jpg` | The banner cropped to 2:1, 1280×640 | GitHub → Settings → General → Social preview (uploaded by hand; GitHub doesn't read it from the repo) |
| `domovoi-icon.png` | The amber cat head, transparent, 1024×1024 | The master every app icon below is cut from |

Derived from `domovoi-icon.png`:

* **Android launcher** (`android/app/src/main/res/`): `mipmap-*/ic_launcher_foreground.png`
  (the head, inside the 66dp safe circle so a round mask never clips the ears),
  `mipmap-*/ic_launcher_monochrome.png` (its silhouette with the face cut out,
  for Android 13+ themed icons) and `drawable/ic_launcher_background.xml`
  (`#1F1A14`), joined in `mipmap-anydpi-v26/ic_launcher.xml`.
* **Play Store**: `android/app/src/main/ic_launcher-playstore.png`, 512×512, a
  full square on `#1F1A14` (Play rounds the corners itself).
* **Web dashboard** (`web/static/assets/`): `icon-192.png` and `icon-512.png`
  (install icons), `icon-maskable-512.png` (on `#1F1A14`, the head inside the
  central 80% a maskable icon may keep), `apple-touch-icon.png` (180×180, no
  transparency) and `favicon-32.png`.

The cat glyph inside the dashboard itself stays the line-art
`web/static/assets/domovoi.svg` (see [`docs/design/`](../design/README.md)).

The banner and the icon were made with an image generator. The text in the
banner is part of the picture, so changing a word means regenerating it.
