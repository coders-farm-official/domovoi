// The no-build dashboard's two load-time checks, without a browser
// (.github/workflows/ci.yml, the web job; runs anywhere Node does):
//
// 1. Every .jsx compiles with the vendored Babel standalone
//    (web/static/vendor/babel/babel.min.js), the same transform the browser
//    applies when the page loads. A syntax error there blanks the page.
// 2. No two scripts index.html loads declare the same top-level name. Every
//    <script type="text/babel"> runs in ONE global scope, so `const X` in
//    two files is a SyntaxError that stops whichever loads second (the
//    "Files tab shows Music" class of bug). The suite's
//    domovoi/tests/script_scope_check.js also checks what Babel itself emits
//    at top level; this is the quick pass over the sources.
//
// Usage: node scripts/dev/jsxcheck.js [repo-root]
// Exit status: 0 when both pass, 1 otherwise. No npm packages.
'use strict';
const fs = require('fs');
const path = require('path');

const root = path.resolve(process.argv[2] || path.join(__dirname, '..', '..'));
const staticDir = path.join(root, 'web', 'static');
const babelMod = require(path.join(staticDir, 'vendor', 'babel', 'babel.min.js'));
const Babel = babelMod.transform ? babelMod : (global.Babel || babelMod.default || babelMod);

// The dashboard's own scripts and the bundled plugin's web panels.
const jsxDirs = [staticDir, path.join(root, 'plugins', 'radio', 'web', 'static')];
const jsxFiles = [];
for (const dir of jsxDirs) {
  if (!fs.existsSync(dir)) continue;
  for (const name of fs.readdirSync(dir).sort()) {
    if (name.endsWith('.jsx')) jsxFiles.push(path.join(dir, name));
  }
}

let bad = 0;
const rel = (f) => path.relative(root, f).split(path.sep).join('/');
for (const f of jsxFiles) {
  try {
    Babel.transform(fs.readFileSync(f, 'utf8'), { presets: ['react'], filename: f });
  } catch (e) {
    bad++;
    console.log(`FAIL ${rel(f)}: ${String((e && e.message) || e).split('\n')[0]}`);
  }
}
console.log(`compile: ${jsxFiles.length - bad} of ${jsxFiles.length} .jsx files compile`);

// Duplicate top-level declarations across the scripts index.html loads.
const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const scripts = [];
for (const m of html.matchAll(/<script[^>]*\ssrc="([^"]+\.(?:jsx|js))(?:\?[^"]*)?"[^>]*>/g)) {
  if (!m[1].startsWith('vendor/')) scripts.push(m[1]);
}
const seen = new Map();
let dups = 0;
for (const f of scripts) {
  const file = path.join(staticDir, f);
  if (!fs.existsSync(file)) {
    dups++;
    console.log(`MISSING ${f}: index.html loads it, but there is no such file`);
    continue;
  }
  const code = fs.readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  for (const m of code.matchAll(/^(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)/gm)) {
    const name = m[1];
    if (!seen.has(name)) {
      seen.set(name, f);
    } else if (seen.get(name) !== f) {
      dups++;
      console.log(`DUP  ${name}: ${seen.get(name)} and ${f}`);
    }
  }
}
console.log(`globals: ${scripts.length} scripts, ${dups} duplicate top-level declaration(s)`);
process.exit(bad || dups ? 1 : 0);
