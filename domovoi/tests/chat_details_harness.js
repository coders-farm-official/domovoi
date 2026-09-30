// Run web/static/chat_details.js outside a browser (plain object for
// `window`) and print what it returns for the cases given, so
// test_web_chat_details.py asserts on the dashboard's real formatting.
//
// Usage: TZ=<zone> node chat_details_harness.js <repo-root> '<cases json>'
//   cases: { "<name>": {stamp: iso, now: iso} | {rows: message, thread: id, now: iso} }
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = process.argv[2];
const cases = JSON.parse(process.argv[3]);
const sandbox = { window: {}, console, Intl, Date };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(root, 'web/static/chat_details.js'), 'utf8'), sandbox,
                { filename: 'chat_details.js' });
const CD = sandbox.window.ChatDetails;
const out = {};
for (const [name, c] of Object.entries(cases)) {
  const now = new Date(c.now);
  out[name] = c.stamp !== undefined ? CD.stamp(c.stamp, now) : CD.rows(c.rows, c.thread, now);
}
process.stdout.write(JSON.stringify(out));
