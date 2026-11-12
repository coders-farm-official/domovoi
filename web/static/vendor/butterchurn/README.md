# Butterchurn (vendored, pinned 3.0.0-beta.5)

The Now Playing visualizer (`web/static/visualizer.jsx`) draws MilkDrop
presets with Butterchurn, a WebGL 2 implementation of the MilkDrop 2 music
visualizer. `butterchurn.min.js` here is served from this box like every
other vendored script, so the dashboard makes no external request. It is
loaded only when someone turns the visualizer on.

* Package: `butterchurn` 3.0.0-beta.5 on npm, by Jordan Berg
  (github.com/jberg/butterchurn).
* Licence: MIT (`LICENSE.txt`, the package's own).
* Tarball: `https://registry.npmjs.org/butterchurn/-/butterchurn-3.0.0-beta.5.tgz`,
  npm integrity `sha512-BStK4OAbBb9Pvt8PuQlS4WVmYBwU1KuDMRHF1V89QjoIFauAqq7tpV4EpYXj7K563r5daLrMX+2y5DBhZZ9Xig==`
  (checked on download, 2026-11-11).
* `butterchurn.min.js` is `package/dist/butterchurn.min.js` from that
  tarball (sha256 `b5353af542b95cb5264069a0a4f52a23174e45745b58c48e245a3557d229b640`)
  with ONE change, below: sha256 now
  `c90f94234af2902fb98108bfbf868274d1498da0097e3318878b75116d86993a`
  (`domovoi/tests/test_web_visualizer.py` holds it to this). Its last line
  names a source map that isn't shipped; a browser only asks for it with
  its developer tools open.

Why the 3.0 beta and not 2.6.7 (npm's `latest`, from 2019): 3.0 runs a
preset's equations as MilkDrop's own EEL, compiled to WebAssembly by
eel-wasm. 2.x needs them converted to JavaScript first and runs that
through `new Function`; the dashboard creates the engine with
`onlyUseWASM: true`, so no preset can ever run JavaScript in a browser of
the house. 3.0 is an ES module (`export default`), imported with a
dynamic `import()`.

The bundle includes its dependencies:

| Code | Licence | Text |
|---|---|---|
| eel-wasm 0.0.x (Jordan Eldredge) | MIT (declared in its package.json; the package ships no licence file) | the MIT terms as in `LICENSE.txt` |
| @assemblyscript/loader 0.17.x | Apache-2.0 | `LICENSE-assemblyscript-loader.txt`, `NOTICE-assemblyscript.txt` (from the AssemblyScript repository at v0.17.14) |
| ecma-proposal-math-extensions 0.0.2 (Rick Waldron) | MIT (declared in its package.json) | the MIT terms as in `LICENSE.txt` |

**The one change:** the engine embeds a 128×128 noise texture
("clouds2", a base64 JPEG) that presets may sample. Its origin is very
likely the original MilkDrop's own texture, whose licence nobody has
stated, so it is replaced here (2026-11-11) by an original texture
generated for Domovoi: seeded, tileable value noise in four octaves
(numpy + Pillow, seed 20261111), grey, saved as a quality-90 JPEG at the
same size, in the same `this.clouds2Image.src="data:image/jpeg;base64,…"`
spot. A preset that samples `sampler_clouds2` gets a similar soft cloud
pattern. Domovoi's own presets don't use it. The only other embedded image
is a 1×1 blank GIF (the engine's "empty" texture). Upgrading the engine
means making this replacement again.

No presets come from this package. The standard set Domovoi ships is its
own (`web/static/visualizer/presets/`, MIT with the rest of the repo); see
that folder's README for why.
