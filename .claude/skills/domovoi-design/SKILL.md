---
name: domovoi-design
description: Use this skill to generate well-branded interfaces and assets for Domovoi — the local-first home voice assistant with the cat house-spirit mascot — whenever working on files under this repo (the web dashboard, plugin web panels, docs visuals) or building prototypes/mocks for it. Contains essential design guidelines, colors, type, fonts, assets, and pointers to the production UI kit.
user-invocable: true
---

Read the `README.md` file within this skill, and explore the other available files.

If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets out and create static HTML files for the user to view. If working on production code, read the rules here to become an expert in designing with this brand — the live component kit is `web/static/` in this repo, not a copy inside the skill.

If the user invokes this skill without any other guidance, ask them what they want to build or design, ask some questions, and act as an expert designer who outputs HTML artifacts _or_ production code, depending on the need.

## Quick orientation
- `colors_and_type.css` — standalone token file for prototypes outside the repo. The **canonical** production token file is `web/static/colors_and_type.css` (same values, plus vendored webfonts and a `--bg` alias) — production code references that one.
- `web/static/assets/domovoi.svg` — the brand cat glyph (the domovoi, a cat house-spirit; the mascot has no personal name). `web/static/assets/domovoi-sleeping.svg` — empty-state cat. `web/static/assets/wordmark.svg` — full lockup.
- `web/static/components.jsx` — the production primitives (`Sidebar`, `Topbar`, `Pill`, `RoomChip`, `StatusDot`, `Card`, `Empty`, `Button`, `Avatar`, `DomovoiGlyph`, `SleepingDomovoi`, `HeadphonesDomovoi`). Reuse these; don't fork them into new copies.
- `preview/` — token + component specimen cards, useful as a visual cheatsheet.

## Hard rules
- One accent — amber. Never introduce a second.
- No emoji. Status uses `<StatusDot/>`, not a colored-circle character.
- Live things pulse (`domovoi-pulse`). Idle things don't.
- Lowercase chrome (`now playing`, `online`, `failed`). Sentence case for content.
- Cat glyph in three places only: top-left wordmark, next to assistant-attributed lines in feeds, empty states.
- Media acquisition UI speaks generically: "provider plugin", "source", "acquisition". Never name a specific external media platform or downloader tool anywhere in UI copy, sample data, or code (banned patterns, any case: `yout[u]be`, `yt[-_]?d[l]p` — written bracket-split here so this file itself passes the repo's vocabulary gate).
- Likewise these reserved token patterns must never appear anywhere (any case): `har[l]ey`, `ric[h]ard`, `orche[s]trator`. The only product name is Domovoi; the mascot is just "the domovoi".
