// Build the Documents editor's vendored browser bundles.
//
//   cd scripts/vendor/editors
//   npm ci --ignore-scripts
//   node build.mjs
//
// Writes three self-contained IIFE bundles (no CDN, no build step on the
// server; the dashboard loads them as plain <script> files):
//
//   web/static/vendor/yjs/yjs.min.js        window.Y + window.YKit
//   web/static/vendor/tiptap/tiptap.min.js  window.DomovoiTiptap (needs window.Y)
//   web/static/vendor/mammoth/mammoth.min.js window.mammoth (.docx -> HTML)
//
// Every npm package that ends up inside a bundle is read from esbuild's
// metafile: its exact version and licence go into that bundle's
// THIRD-PARTY.json, its licence text into licenses/, and the build FAILS
// if a licence is not on the permissive list (docs/LICENSES.md, the
// dependency policy). Versions are pinned exactly in package.json and
// package-lock.json; commit the bundles and this folder (not node_modules).
import { build } from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..', '..');
const VENDOR = path.join(REPO, 'web', 'static', 'vendor');

// SPDX expressions accepted as-is, and the choice recorded for the dual
// ones (jszip is MIT OR GPL-3.0-or-later: used under MIT).
const PERMISSIVE = new Map([
  ['MIT', 'MIT'], ['ISC', 'ISC'], ['BSD-2-Clause', 'BSD-2-Clause'],
  ['BSD-3-Clause', 'BSD-3-Clause'], ['Apache-2.0', 'Apache-2.0'],
  ['BSD', 'BSD-2-Clause'],                       // duck: BSD 2-clause text
  ['(MIT OR GPL-3.0-or-later)', 'MIT'],          // jszip, used under MIT
  ['(MIT AND Zlib)', 'MIT AND Zlib'],            // pako: both permissive
]);
// npm packages that ship no licence file of their own: the text comes
// from the project's repository, kept in ./licenses.
const LICENCE_FALLBACK = {
  '@tiptap/': 'tiptap-LICENSE.md',
  'isarray': 'isarray-LICENSE',
};

// Packages that arrive as an upstream PREBUILT file (esbuild sees only
// the outer package), with the packages that prebuilt file carries inside
// it: jszip's browser build is dist/jszip.min.js, which contains these.
const EMBEDDED = {
  jszip: ['pako', 'lie', 'immediate', 'setimmediate'],
};

const BUNDLES = [
  {
    name: 'yjs',
    entry: 'yjs-entry.js',
    out: path.join(VENDOR, 'yjs', 'yjs.min.js'),
    shimYjs: false,
  },
  {
    name: 'tiptap',
    entry: 'tiptap-entry.js',
    out: path.join(VENDOR, 'tiptap', 'tiptap.min.js'),
    shimYjs: true,
  },
  {
    name: 'mammoth',
    entry: 'mammoth-entry.js',
    out: path.join(VENDOR, 'mammoth', 'mammoth.min.js'),
    shimYjs: false,
  },
];

// Every `import ... from 'yjs'` inside the tiptap bundle reads window.Y,
// so the page holds exactly one Yjs (yjs.min.js's).
const yjsFromWindow = {
  name: 'yjs-from-window',
  setup(b) {
    b.onResolve({ filter: /^yjs$/ }, () => ({ path: 'yjs', namespace: 'window-y' }));
    b.onLoad({ filter: /.*/, namespace: 'window-y' }, () => ({
      contents: "if (!window.Y) throw new Error('load vendor/yjs/yjs.min.js before vendor/tiptap/tiptap.min.js');\nmodule.exports = window.Y;",
      loader: 'js',
    }));
  },
};

const packageOf = (input) => {
  const norm = input.replace(/\\/g, '/');
  const i = norm.lastIndexOf('node_modules/');
  if (i === -1) return null;
  const rest = norm.slice(i + 'node_modules/'.length).split('/');
  const name = rest[0].startsWith('@') ? `${rest[0]}/${rest[1]}` : rest[0];
  return { name, dir: norm.slice(0, i) + 'node_modules/' + name };
};

const licenceFile = (dir, name) => {
  const hit = fs.readdirSync(dir).find((f) => /^(licen[cs]e|copying)/i.test(f));
  if (hit) return path.join(dir, hit);
  for (const [prefix, file] of Object.entries(LICENCE_FALLBACK)) {
    if (name === prefix || name.startsWith(prefix)) return path.join(HERE, 'licenses', file);
  }
  throw new Error(`${name}: no licence file in the package and no fallback in ./licenses`);
};

const options = (bundle, versions) => ({
  entryPoints: [path.join(HERE, bundle.entry)],
  bundle: true,
  format: 'iife',
  platform: 'browser',
  target: ['es2020'],
  minify: true,
  legalComments: 'none',          // the texts are in licenses/ beside the bundle
  metafile: true,
  write: false,
  define: { __VERSIONS__: JSON.stringify(versions), 'process.env.NODE_ENV': '"production"' },
  plugins: bundle.shimYjs ? [yjsFromWindow] : [],
  logLevel: 'warning',
});

for (const bundle of BUNDLES) {
  // Pass 1: which packages are inside, at which versions.
  const probe = await build(options(bundle, {}));
  const pkgs = new Map();
  for (const input of Object.keys(probe.metafile.inputs)) {
    const p = packageOf(path.resolve(HERE, input));
    if (p && !pkgs.has(p.name)) pkgs.set(p.name, p.dir);
  }
  for (const [outer, inner] of Object.entries(EMBEDDED)) {
    if (!pkgs.has(outer)) continue;
    for (const name of inner) pkgs.set(name, path.join(HERE, 'node_modules', name));
  }
  const rows = [];
  for (const [name, dir] of [...pkgs.entries()].sort()) {
    const meta = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8'));
    const declared = meta.license || (meta.licenses && meta.licenses.map((l) => l.type).join(' OR '));
    const used = PERMISSIVE.get(declared);
    if (!used) throw new Error(`${name}@${meta.version}: licence ${declared} is not on the permissive list`);
    rows.push({ name, version: meta.version, license: used, declared, file: licenceFile(dir, name) });
  }
  const versions = Object.fromEntries(rows.map((r) => [r.name, r.version]));

  // Pass 2: the real bundle, with its versions baked in.
  const result = await build(options(bundle, versions));
  const outDir = path.dirname(bundle.out);
  fs.mkdirSync(path.join(outDir, 'licenses'), { recursive: true });
  for (const f of fs.readdirSync(path.join(outDir, 'licenses'))) fs.unlinkSync(path.join(outDir, 'licenses', f));
  const banner = `/* Domovoi vendored bundle "${bundle.name}" (scripts/vendor/editors/build.mjs). ` +
    `Contains: ${rows.map((r) => `${r.name}@${r.version} (${r.license})`).join(', ')}. ` +
    'Licence texts: licenses/ beside this file. */\n';
  fs.writeFileSync(bundle.out, banner + result.outputFiles[0].text);
  for (const r of rows) {
    const safe = r.name.replace('/', '__');
    fs.copyFileSync(r.file, path.join(outDir, 'licenses', `${safe}.txt`));
  }
  fs.writeFileSync(path.join(outDir, 'THIRD-PARTY.json'), JSON.stringify(
    rows.map(({ name, version, license, declared }) => ({ name, version, license, declared })), null, 2) + '\n');
  const kb = Math.round(fs.statSync(bundle.out).size / 1024);
  console.log(`${bundle.name}: ${path.relative(REPO, bundle.out)} ${kb} KiB, ${rows.length} packages`);
}
