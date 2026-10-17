# Domovoi design system

Design system for **Domovoi** — a local-first home voice assistant. A domovoi is a Slavic household guardian spirit, often taking the form of a cat; the brand mascot is **the domovoi**, a line-art cat house-spirit. It has no personal name — it's just "the domovoi", lowercase in chrome.

This is a tech-forward management UI you'd reach from a laptop or a phone on the same Wi-Fi as your Pi satellites. The dashboard (web backend, port `6369`) currently ships these core surfaces, plus routes that installed plugins contribute:

- **Home** — the default page (`#home`): the house at a glance, and on a phone the "everything" grid behind the phone bar (home · music · videos · chat by default; each device picks its own two to five tabs in Settings → Phone bar)
- **Chat** — threaded text chat with the local models (vision-capable when a message carries images); the cat glyph marks assistant lines
- **Music / Podcasts / Audiobooks / Videos / News** — library, acquisitions, now-playing across rooms, video playback with resume (image browsing lives in Files; the Images generation page comes from the Image Generation plugin)
- **People** — recognized voices, last-heard timestamps, presence tiers
- **Settings → Satellites** — per-room Pi status (online/offline, Wi-Fi rx/tx, version); a Settings tab, not a nav page (the old `#satellites` opens it)
- **Calendar** — local + synced events, timers, reminders
- **Documents**, **Plugins**, and plugin-supplied pages (the sidebar interleaves core and plugin nav items by `nav_order`)

## Hard rules

- One accent in Domovoi's own UI: amber. Never introduce a second. Plugin pages are the one exception, and only inside the plugin frame: there the brand tokens are the plugin's accent (or the neutral plugin slate), never amber, so a plugin reads as a plugin (see "Plugin pages" below).
- No emoji. Status uses `<StatusDot/>`, not a colored-circle character.
- Live things pulse (`domovoi-pulse`). Idle things don't.
- Lowercase chrome (`now playing`, `online`, `failed`). Sentence case for content.
- Cat glyph in three places only: top-left wordmark, next to assistant-attributed lines in feeds, empty states.
- Media acquisition UI speaks generically: "provider plugin", "source", "acquisition". Never name a specific external media platform or downloader tool anywhere in UI copy, sample data, or code (banned patterns, any case: `yout[u]be`, `yt[-_]?d[l]p` — written bracket-split here so this file itself passes the repo's vocabulary gate).
- Likewise these reserved token patterns must never appear anywhere (any case): `har[l]ey`, `ric[h]ard`, `orche[s]trator`. The only product name is Domovoi; the mascot is just "the domovoi".
- Reuse the primitives in `web/static/components.jsx`; don't fork copies of them.
- The app icon (`docs/assets/domovoi-icon.png`: launcher, Play Store, web install icons) is the glossy amber cat head; inside the UI the cat stays the line-art glyph. `docs/assets/README.md` lists every derived size.

## Where the truth lives

The **production frontend in this repo is the living UI kit**. Don't fork copies of it into prototypes when working on production code — extend it.

- `web/static/colors_and_type.css` — canonical token file (light + dark). The copy in this folder (`docs/design/colors_and_type.css`) is the standalone version for out-of-repo prototypes: identical token values, but it loads Inter/JetBrains Mono from CDNs instead of the vendored `/vendor/fonts` files, and lacks the `--bg` alias.
- `web/static/components.jsx` — shared primitives: `Sidebar`, `Topbar`, `PageHeader`, `Stat`, `Card`, `Empty`, `Button`, `IconButton`, `Pill`, `RoomChip`, `Avatar`, `StatusDot`, `DomovoiGlyph`, `SleepingDomovoi`, `HeadphonesDomovoi`.
- `web/static/assets/domovoi.svg` — the cat glyph (awake). `domovoi-sleeping.svg` — empty-state cat. `wordmark.svg` — full lockup.
- `web/static/styles.css` — page-shell layout (grid, sidebar, topbar, tables).
- `preview/` in this folder — token and component specimen cards, a visual cheatsheet. They link the folder's own token file and the repo's `web/static/assets/` SVGs, so open them from inside the repo checkout.

## The aesthetic in one paragraph

A modern technical dashboard, in the lineage of Linear / Vercel / Tailscale admin: information-dense without being cluttered, sharp grotesque typography, hairline borders, generous-but-not-wasteful whitespace, and a single warm accent (**amber** — see below). Light mode is clean white-on-warm-gray; dark mode is near-black canvas with a hint of warmth in the surfaces. Real-time presence is communicated via small pulsing dots, never via heavy color washes.

## Why amber and not blue

Linear is iris-purple, Vercel is monochrome, Tailscale is teal — picking a cool blue would make Domovoi a fourth knock-off in that lineup. Amber gives the product a distinct, slightly domestic feel (it's a *home* assistant — hearth-colored suits a house spirit), reads well on both near-white and near-black canvases, and pairs naturally with the cat motif. It's used **sparingly** — primary buttons, the active nav indicator, link color, and "live" pulses. Status colors (green / amber / red / gray) follow standard convention; the brand amber is a slightly different, deeper amber than the warning amber so they don't collide.

---

## Content fundamentals

The product talks like a competent, slightly dry sysadmin who knows you personally. Not chatty, not corporate, not cute — a *terminal* that happens to have rounded corners.

**Voice & tone**
- Direct. "3 satellites online" — never "You currently have 3 satellites that are online."
- Lowercase for system labels and small chrome (`now playing`, `last heard`, `wi-fi`, `running`, `done`).
- Sentence case for content the user wrote or sees as data ("Dinner with Mom", "Creep — Radiohead").
- Title Case never, except in a few proper nouns (Domovoi as the product name, Wi-Fi).
- Short. Most labels are one or two words. Tooltips and toasts top out around twelve.
- "you" sparingly, "we" never. The system isn't a personality, it's a control panel. The domovoi (the cat) is the only personality, and it doesn't talk — it appears.

**Naming**
- Rooms are lowercase single words: `kitchen`, `garage`, `office`. They appear in the UI inside hairline rounded chips with a leading dot.
- People are first-name only: `Kamron`, `Sarah`, `Alex`. Never "User #1."
- Statuses: `online` / `offline` / `idle` / `running` / `pending` / `done` / `failed`. Lowercase; backed by a status dot.
- Times are relative wherever possible (`2m ago`, `just now`, `yesterday`), with the absolute time on hover.

**Numbers and units**
- `39.0 / 72.2 Mbit` — one decimal, lowercase unit, slash separator for rx/tx pairs.
- Durations: `3:58` for tracks, `2m ago` / `1h ago` / `3d ago` for timestamps. Never "3 minutes ago".
- Counts: `3 online · 1 offline` with a thin middle-dot separator.

**Empty states**
- One line, lowercase, ending without punctuation: `no acquisitions yet`, `nothing playing`, `no events this week`.
- Optionally, the sleeping cat silhouette above the line (`SleepingDomovoi`; `HeadphonesDomovoi` on music surfaces). This is one of the only places the mascot leaves the top-left corner.

**Errors**
- Keep them short and machine-readable: `acquisition: source unavailable` is fine as-is. Don't rewrite to "Sorry, that item couldn't be fetched." The user is technical.
- Media acquisition copy is **provider-generic**: "provider plugin", "source", "acquisition", "external source". UI copy, sample data, and code never name a specific external media platform or downloader tool — see the hard rules above and the permanent conventions in the repo's `AGENTS.md`.

**No emoji**, ever. Status is a colored dot, not an emoji circle. The cat glyph is a real SVG, not a cat emoji. Unicode middle-dot (·) and en-dash (–) are the only special characters used routinely.

---

## Visual foundations

### Type
- **Inter Variable** for everything UI. Geometric grotesque, optical-sized, reads well at 11–13 px.
- **JetBrains Mono** for any value that's structurally code-shaped: file paths, IDs, durations like `3:58`, IP addresses, transfer rates. Tabular numerals on by default for monospace.
- **Cash Currency** (`fonts/Cash_Currency.ttf`, also vendored in `web/static/fonts/`) is the display/brand face used by the wordmark lockup only.
- Numbers in Inter use `font-variant-numeric: tabular-nums` so columns of `Mbit` values, durations, and counts line up.
- One-step type scale: 32 / 24 / 18 / 15 / 13 / 11. No 14, no 16. The whole system runs on a 13-px body.

### Color
- Warm-tinted neutrals, not pure gray. The light canvas is `oklch(0.985 0.005 80)` (a hair of yellow), and the dark canvas is `oklch(0.155 0.005 80)`. Pure `#000` and pure `#fff` appear nowhere.
- Surfaces step up from canvas → card → raised → overlay in roughly 3 % luminance increments. Borders are always a step lighter/darker than the surface they're on.
- One accent: **amber** at `oklch(0.78 0.16 75)`. Used at 100 % for primary buttons and the active nav indicator, at ~12 % alpha for hover/selection highlights, and at 20 % alpha for "live" pulse halos.
- Status: `green` (online), `amber-warn` (warning, distinct from brand amber), `red` (error), `gray` (idle/offline). Each has a 10 % tint variant for backgrounds.
- Contrast: every text colour (`--fg`, `--fg-muted`, `--fg-subtle`, `--fg-faint`) reads at 4.5:1 or better on canvas, card, raised and sunken in both themes; icons and disabled controls at 3:1 (`domovoi/tests/test_web_contrast.py` holds the tokens to it; `scripts/dev/contrast_audit.py` measures a running dashboard). Amber or a status colour used as **text or an icon** goes through its ink token (`--brand-ink`, `--ok-ink`, `--warn-ink`, `--err-ink`): the same hue, deeper in light mode (the fills read about 2:1 on white), equal to the fill in dark mode. Fills, dots and washes keep the plain tokens. Destructive buttons use `--danger` / `--danger-fg`. Never write a colour past the tokens (`white`, a hex, a `var(--x, #fallback)` for a token that doesn't exist): it is right in one theme at most.
- Plugin nav icons are drawn as a mask in the text colour (their shape, whatever colour the plugin drew them in), so they follow the theme and the active state like the lucide glyphs; a plugin may ship `nav_icon_dark` to draw its own dark-theme icon.

### Plugin pages: the frame and the plugin accent
- Every plugin page renders inside `PluginPageFrame` (`web/static/components.jsx`; the shell wraps plugin pages only). The frame is one band across the top of the page, spanning the content gutter like the shell's own notices: the plugin's nav icon, its name (600) and `by <publisher>` (muted), a lowercase `plugin` badge, and a `manage` link (with the version, mono) to its row on the Plugins page (`#plugins/<slug>`). 40 px tall on a desktop, one 44 px row on a phone (the version drops, the publisher ellipsizes). Its background is the accent's soft wash over the surface, with the hairline border below; the icon and badge text are the accent's ink. Plugin content starts right under it.
- Inside the frame `--brand`, `--brand-hover`, `--brand-press`, `--brand-fg` / `--fg-on-brand`, `--brand-soft`, `--brand-halo`, `--brand-ring` and `--brand-ink` are the plugin's, set inline per theme on the frame. Nothing else is re-scoped: surfaces, text and status colours stay the dashboard's. The sidebar's active row, the topbar and every core page keep amber.
- **The plugin accent** is the manifest's optional `[plugin].accent` (`"#rrggbb"`, with an optional `accent_dark`). Use it when a plugin has an identity of its own; leave it out otherwise. Core derives the tokens (`domovoi/plugins_runtime/accent.py`) and holds them to the contrast rules on canvas, card, raised and sunken in both themes: `--brand-ink` 4.5:1 (also on its own soft wash), the `--brand` fill 3:1 as a UI part, `--brand-fg` 4.5:1 on the fill and on its hover and press shades (near-white or near-black, whichever reads), and 3:1 on a disabled button (the whole button at the disabled opacity). A colour that falls short moves in lightness, same hue, until it passes (darker in light, lighter in dark), with a load warning that names the colour used. A value that isn't a hex colour, or one in amber's family (hue within 20 degrees of amber at chroma 0.08 or more), is refused with a warning and the page gets the default. It never fails the plugin.
- **The default plugin accent is slate**: about `oklch(0.46 0.05 240)` in light (`#3e5c71`) and `oklch(0.74 0.05 240)` in dark (`#8fb0c8`). Why slate: it is the palette's existing colour for "comes from outside Domovoi" (the synced-calendar chip, `oklch(0.6 0.04 240)`), it sits opposite amber on the hue wheel so a plugin page never reads as core, and its low chroma keeps it a neutral that doesn't compete with the shell or look like a status (green, orange, red and grey all mean something here).

### Spacing
- 4 px base. Stack: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48 / 64. The stepped system is enforced — nothing in between.
- The sidebar is 232 px wide (`--sidebar-w`); the topbar is 52 px tall (`--topbar-h`); the content gutter is 24 px on desktop, 16 px on phones.
- Tables and rows are 40 px tall by default, 36 px in compact mode. Cells get a 12-px horizontal pad.

### Radius
- 6 px is the default for buttons, inputs, chips, status pills.
- 10 px for cards.
- 14 px for floating panels and the command-K menu.
- Full (`9999px`) for circular things (avatar bubble, room dots, toggle thumb).
- Nothing has a corner radius bigger than 14 px. No "blob" shapes, no glassmorphism.

### Borders
- A single `1px` hairline at `border` color token. Never thicker, never dashed.
- Cards have a hairline border AND a one-pixel inner highlight (`box-shadow: inset 0 1px 0 var(--surface-highlight)`) — the trick that makes Linear / Vercel cards feel etched.

### Shadows
- Three steps: `xs` (focus / hover lift), `sm` (popovers), `md` (command palette / modal).
- Shadows are warm-grey tinted, not pure black. In dark mode shadows mostly disappear; we use border + surface-step contrast instead.

### Animation
- 120 ms for hovers, 180 ms for state changes, 240 ms for layout. Never longer than 300 ms.
- Easing: `cubic-bezier(0.2, 0, 0, 1)` (a subtle ease-out) for everything except the live pulse, which uses an `ease-in-out` 1.6 s loop.
- No bounces, no overshoots, no springs. This is a control panel.
- The "live" pulse (`@keyframes domovoi-pulse`): a 6 px dot with an outer halo that fades from 60 % alpha to 0 over 1.6 s, scaled from 1× to 2.4×. Used on online satellites, currently-playing rooms, and any acquisition in `running` state.

### Hover & press
- Hover: surface lifts one step (e.g. card → raised), border stays put. Buttons get a 4 % alpha overlay.
- Press: surface drops one step, plus a 1 px Y translate for tactile feedback on touch devices.
- Focus: 2 px amber ring at 40 % alpha, offset 2 px from the element. Not the browser default.

### Density, layout rules, fixed elements
- Sidebar and topbar are fixed; the content scrolls inside its own pane on desktop.
- Mobile collapses the sidebar to a bottom-tab strip: the device's phone bar (home · music · videos · chat unless that device picked two to five others in Settings → Phone bar; Home is always first, and a shared screen never shows its personal pages there); every other page is on Home's "everything" grid, whose "edit the bar" link opens the editor. The docked mini-player sits on top of the strip as one compact row (cover, title, play/pause, next, "open player", 44px targets); everything else it does — seek, volume, where it plays, the queue — is in the sheet "open player" brings up over the screen above the strip, which closes with its chevron or the back gesture. Topbar stays; its "domovoi" crumb (and the desktop brand row) links to Home.
- The command palette (`⌘K`) is centred, 640 px wide, 14 px radius, `md` shadow.
- No transparency or backdrop-blur on the main chrome. The only blur is on the command-palette scrim (8 px) and the mobile nav scrim.
- Imagery: there is essentially none. If artwork appears (album art), it's in a 40 × 40 rounded-6 thumbnail with a hairline border, never bled into the layout. The only "decorative" mark in the system is the cat glyph.

### Cards
- 10 px radius, hairline border, `card` surface, inner highlight. No drop shadow at rest. On hover, the card lifts to `raised` surface and gets an `xs` shadow. The border color does **not** change on hover.

---

## Iconography

- **Lucide** is the default icon set. 16 px stroke-1.5 inside chrome (sidebar, buttons, table cells); 18 px stroke-1.5 in headers; 20 px stroke-1.5 only in empty-state illustrations and the play/pause transport. Stroke color follows the surrounding text color. Plugin pages may supply their own nav icon SVG via the manifest; it renders at the same size and inherits the same stroke color.
- The only custom icon in the system is the **domovoi cat glyph** (`web/static/assets/domovoi.svg` and `domovoi-sleeping.svg`). It appears in exactly three places: the top-left wordmark, next to assistant-attributed lines in conversation feeds (the "from the domovoi" mark, 12 px), and in empty-state illustrations. **Nowhere else.**
- No emoji. No Unicode glyphs as icons. Status is communicated by the `<StatusDot/>` component, never by an emoji circle.
- Album / artist artwork, when present, is treated as user-content imagery, not iconography — see the Cards rules.

---

## Index — what's in this folder

```
README.md                     ← you are here (this folder is docs/design/)
colors_and_type.css           ← standalone token file for out-of-repo prototypes
                                 (canonical: web/static/colors_and_type.css)
fonts/
  Cash_Currency.ttf           ← wordmark display face
preview/
  type-*.html                 ← typography specimen cards
  color-*.html                ← color scale cards
  spacing-*.html              ← radius / shadow / spacing token cards
  components-*.html           ← button / input / status / card cards
  brand-*.html                ← wordmark / pulse / cat-glyph cards
```

The live component kit and assets live in the repo at `web/static/` — see "Where the truth lives" above.
