// Runs web/static/sanitize_html.js's DOCUMENT allowlist (sanitizeDocumentHtml,
// what the Documents editor applies before anything reaches it) outside a
// browser, in a vm with a plain `window`. Reads a JSON list of strings on
// stdin; writes the list of results.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = process.argv[2];
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const sandbox = { window: {}, console };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(root, 'web/static/sanitize_html.js'), 'utf8'), sandbox,
  { filename: 'sanitize_html.js' });
const clean = sandbox.window.sanitizeDocumentHtml;
if (typeof clean !== 'function') throw new Error('sanitizeDocumentHtml is missing');
process.stdout.write(JSON.stringify(input.map((s) => clean(s))));
