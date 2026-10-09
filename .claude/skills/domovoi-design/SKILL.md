---
name: domovoi-design
description: Use this skill to generate well-branded interfaces and assets for Domovoi — the local-first home voice assistant with the cat house-spirit mascot — whenever working on files under this repo (the web dashboard, plugin web panels, docs visuals) or building prototypes/mocks for it. Contains essential design guidelines, colors, type, fonts, assets, and pointers to the production UI kit.
user-invocable: true
---

The design system is tool-neutral and lives in `docs/design/`, so every
coding agent can use it; this skill only points there. Read
`docs/design/README.md` in full: the hard rules, tokens, type, voice, and
where the production UI kit lives (`web/static/`).
`docs/design/colors_and_type.css` and `docs/design/fonts/` are for
prototypes outside the repo; `docs/design/preview/` holds specimen cards.

If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets out and create static HTML files for the user to view. If working on production code, follow `docs/design/README.md`; the live component kit is `web/static/` in this repo, not a copy in the design folder.

If the user invokes this skill without any other guidance, ask them what they want to build or design, ask some questions, and act as an expert designer who outputs HTML artifacts _or_ production code, depending on the need.
