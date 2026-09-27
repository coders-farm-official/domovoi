// List every top-level declaration each dashboard script makes AFTER the
// in-browser Babel compiles it, exactly as the page does.
//
// index.html loads each file as <script type="text/babel" data-presets="react">.
// @babel/standalone compiles such a tag with the named presets PLUS its
// default script-tag plugins (class properties, object rest/spread, flow
// strip), and every compiled script runs as a classic script in ONE global
// scope. So a helper the compiler emits at top level — `const _excluded`
// for `{ a, ...rest }` — is a global, and a second script declaring the same
// `const` is a SyntaxError that stops that whole script from running.
// Compiling with presets:['react'] alone (what the render harnesses do)
// never emits those helpers, which is how one slipped through.
//
// Usage: node script_scope_check.js <repo-root>
// Output: { scripts: [...], decls: { <script>: { lexical: [...], other: [...] } } }
'use strict';
const fs = require('fs');
const path = require('path');

const root = process.argv[2];
const staticDir = path.join(root, 'web', 'static');
const babelMod = require(path.join(staticDir, 'vendor', 'babel', 'babel.min.js'));
const Babel = babelMod.transform ? babelMod : (global.Babel || babelMod.default || babelMod);

// @babel/standalone's script-tag defaults (transformScriptTags), checked
// against the page on 2026-09-27: this is what produces `const _excluded`.
const SCRIPT_TAG_PLUGINS = ['transform-class-properties', 'transform-object-rest-spread', 'transform-flow-strip-types'];

const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const sources = [];
const tagRe = /<script\b([^>]*)>([\s\S]*?)<\/script>/g;
let m;
let inline = 0;
while ((m = tagRe.exec(html)) !== null) {
  const attrs = m[1];
  if (!/type="text\/babel"/.test(attrs)) continue;
  const presets = (attrs.match(/data-presets="([^"]*)"/) || [, 'react'])[1].split(',').map((s) => s.trim());
  const src = (attrs.match(/\ssrc="([^"]+)"/) || [])[1];
  if (src) {
    const file = path.join(staticDir, src.split('?')[0]);
    sources.push({ name: src, presets, code: fs.readFileSync(file, 'utf8') });
  } else {
    inline += 1;
    sources.push({ name: `index.html inline #${inline}`, presets, code: m[2] });
  }
}

const decls = {};
for (const s of sources) {
  const out = Babel.transform(s.code, {
    presets: s.presets, plugins: SCRIPT_TAG_PLUGINS, filename: s.name, ast: true, code: false,
  });
  const lexical = [];
  const other = [];
  for (const node of out.ast.program.body) {
    if (node.type === 'VariableDeclaration') {
      for (const d of node.declarations) {
        if (d.id && d.id.type === 'Identifier') (node.kind === 'var' ? other : lexical).push(d.id.name);
      }
    } else if (node.type === 'ClassDeclaration' && node.id) {
      lexical.push(node.id.name);
    } else if (node.type === 'FunctionDeclaration' && node.id) {
      other.push(node.id.name);
    }
  }
  decls[s.name] = { lexical, other };
}
process.stdout.write(JSON.stringify({ scripts: sources.map((s) => s.name), decls }));
