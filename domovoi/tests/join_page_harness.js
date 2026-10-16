// Run domovoi/join_page/join.js against a stand-in DOM, for
// test_android_app.py: the element ids join.html has, a scripted
// XMLHttpRequest answering GET /v1/android-app, and a chosen URL and
// browser. Prints what the page shows: each element's text, hidden flag
// and href.
//
// Usage: node join_page_harness.js <repo-root> '<scenario json>'
//   scenario: {href, search, ua, platform, app (the /v1/android-app body, or null for a 500)}
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = process.argv[2];
const sc = JSON.parse(process.argv[3]);
const html = fs.readFileSync(path.join(root, 'domovoi/join_page/join.html'), 'utf8');
const src = fs.readFileSync(path.join(root, 'domovoi/join_page/join.js'), 'utf8');

const els = {};
const make = (id, hidden) => ({
  id, hidden, textContent: '', className: '', attrs: {}, children: [],
  get href() { return this.attrs.href; }, set href(v) { this.attrs.href = v; },
  setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; },
  appendChild(c) { this.children.push(c); this.textContent += c.textContent; return c; },
  set innerHTML(v) { throw new Error('innerHTML used on #' + id); },
});
for (const m of html.matchAll(/<[a-z0-9]+\b[^>]*\bid="([^"]+)"([^>]*)>/g)) {
  els[m[1]] = make(m[1], /\bhidden\b/.test(m[0]));
}
const document = {
  readyState: 'complete', title: '',
  getElementById: (id) => els[id] || null,
  createElement: (tag) => make(tag, false),
  createTextNode: (t) => ({ textContent: t }),
  addEventListener() {},
};
function XMLHttpRequest() {}
XMLHttpRequest.prototype.open = function (m, p) { this.path = p; };
XMLHttpRequest.prototype.setRequestHeader = function () {};
XMLHttpRequest.prototype.send = function () {
  this.readyState = 4;
  if (this.path === '/v1/android-app' && sc.app !== null) { this.status = 200; this.responseText = JSON.stringify(sc.app); }
  else { this.status = 500; this.responseText = ''; }
  this.onreadystatechange();
};
const url = new URL(sc.href);
const window = { location: { href: sc.href, search: url.search, protocol: url.protocol, host: url.host } };
vm.runInNewContext(src, {
  window, document, XMLHttpRequest, navigator: { userAgent: sc.ua, platform: sc.platform },
  encodeURIComponent, decodeURIComponent, JSON, Math, String, URL: sc.noUrl ? undefined : URL,
});
const out = { doc_title: document.title };
for (const [id, el] of Object.entries(els)) out[id] = { text: el.textContent, hidden: el.hidden, href: el.attrs.href || null, cls: el.className };
process.stdout.write(JSON.stringify(out));
