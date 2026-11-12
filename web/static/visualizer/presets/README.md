# The visualizer's standard presets

Twelve MilkDrop presets, in Butterchurn 3's JSON format, that every
Domovoi install ships with. `index.json` lists them in the order the
visualizer offers them, and names the one a new browser starts on.

**They were written for Domovoi, from scratch, and are MIT-licensed with
the rest of this repository.** No line of them comes from an existing
preset pack.

Why our own: MilkDrop presets have been shared for twenty years by
hundreds of authors, nearly always without a licence. The packs that
collect them don't change that: one labels the whole collection MIT under
the packager's name; another says outright that the presets were "not
released under any specific license" and that each author keeps the
copyright. MilkDrop 2's open-source release (BSD, 2013) is the plugin's
code only; its installer copied the stock presets from a local Winamp, and
none are in the release. With no preset set whose authors granted a
permissive licence, Domovoi doesn't ship anyone else's. Households can add
the presets they like for their own house (the visualizer's "add presets",
admin only).

What's in a file: `baseVals` (MilkDrop's settings), up to four custom
`shapes` and `waves`, the equations as MilkDrop EEL (`*_eqs_eel`, compiled
to WebAssembly in the browser), and `warp` / `comp` shaders as GLSL (empty
means the engine's standard ones; several here use a small glow
composite). Editing one: change the JSON, reload the dashboard.
