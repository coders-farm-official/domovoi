// Render markdown the way the document editor's preview does, outside a
// browser: the dashboard's OWN vendored `marked` and its OWN sanitiser
// (web/static/sanitize_html.js), in that order, in a vm with a plain
// object for `window`.
//
// What comes back is the exact string doc_editor.jsx would hand to
// dangerouslySetInnerHTML, so the pytest module next to this file can
// assert on what the preview would actually put in the page.
//
// Usage: node markdown_preview_harness.js <repo-root> '<cases json>'
//   cases: { "<name>": {markdown: "..."} | {html: "..."} | {chat: "..."} }
//     markdown — run through marked, then the sanitiser (the real path)
//     html     — run through the sanitiser alone (a direct probe)
//     chat     — run through chat_markdown.js, the chat reply renderer
//                (its own marked instance, then the sanitiser)
// Output: { "<name>": { raw, sanitized } }
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = process.argv[2];
const cases = JSON.parse(process.argv[3]);

const sandbox = { window: {}, console, module: undefined, exports: undefined };
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
vm.createContext(sandbox);

const run = (file) => vm.runInContext(
  fs.readFileSync(path.join(root, file), 'utf8'), sandbox, { filename: file },
);

run('web/static/vendor/marked/marked.min.js');
run('web/static/sanitize_html.js');
run('web/static/chat_markdown.js');

const marked = sandbox.marked || sandbox.window.marked;
if (!marked || typeof marked.parse !== 'function') {
  throw new Error('vendored marked did not expose parse()');
}
const sanitize = sandbox.window.sanitizeHtml;
if (typeof sanitize !== 'function') {
  throw new Error('sanitize_html.js did not expose window.sanitizeHtml');
}

const result = {};
for (const [name, sc] of Object.entries(cases)) {
  if (sc.chat !== undefined) {
    const out = sandbox.window.chatMarkdownHtml(sc.chat);
    result[name] = { raw: out, sanitized: out };
    continue;
  }
  const raw = sc.html !== undefined ? sc.html : marked.parse(sc.markdown);
  result[name] = { raw, sanitized: sanitize(raw) };
}
process.stdout.write(JSON.stringify(result));
