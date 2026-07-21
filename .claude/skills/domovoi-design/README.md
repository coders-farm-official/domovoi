# Domovoi design system

Design system for **Domovoi** — a local-first home voice assistant. A domovoi is a Slavic household guardian spirit, often taking the form of a cat; the brand mascot is **the domovoi**, a line-art cat house-spirit. It has no personal name — it's just "the domovoi", lowercase in chrome.

This is a tech-forward management UI you'd reach from a laptop or a phone on the same Wi-Fi as your Pi satellites. The dashboard (web backend, port `6369`) currently ships these core surfaces, plus routes that installed plugins contribute:

- **Music / Podcasts / Audiobooks / News** — library, acquisitions, now-playing across rooms
- **People** — recognized voices, last-heard timestamps, presence tiers
- **Satellites** — per-room Pi status (online/offline, Wi-Fi rx/tx, version)
- **Calendar** — local + synced events, timers, reminders
- **Documents**, **Plugins**, and plugin-supplied pages (the sidebar interleaves core and plugin nav items by `nav_order`)

## Where the truth lives

The **production frontend in this repo is the living UI kit**. Don't fork copies of it into prototypes when working on production code — extend it.

- `web/static/colors_and_type.css` — canonical token file (light + dark). The copy in this skill folder is the standalone version for out-of-repo prototypes: identical token values, but it loads Inter/JetBrains Mono from CDNs instead of the vendored `/vendor/fonts` files, and lacks the `--bg` alias.
- `web/static/components.jsx` — shared primitives: `Sidebar`, `Topbar`, `PageHeader`, `Stat`, `Card`, `Empty`, `Button`, `IconButton`, `Pill`, `RoomChip`, `Avatar`, `StatusDot`, `DomovoiGlyph`, `SleepingDomovoi`, `HeadphonesDomovoi`.
- `web/static/assets/domovoi.svg` — the cat glyph (awake). `domovoi-sleeping.svg` — empty-state cat. `wordmark.svg` — full lockup.
- `web/static/styles.css` — page-shell layout (grid, sidebar, topbar, tables).
- `preview/` in this skill — token and component specimen cards, a visual cheatsheet. They link the skill-local token file and the repo's `web/static/assets/` SVGs, so open them from inside the repo checkout.

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
- Media acquisition copy is **provider-generic**: "provider plugin", "source", "acquisition", "external source". UI copy, sample data, and code never name a specific external media platform or downloader tool — see the vocabulary rules in `SKILL.md` and the repo `CLAUDE.md`.

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
- Mobile collapses the sidebar to a bottom-tab strip. Topbar stays.
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
README.md                     ← you are here
SKILL.md                      ← Agent Skill manifest (hard rules live there too)
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
