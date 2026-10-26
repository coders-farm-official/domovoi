// Run web/static/fonts.jsx's window.DomovoiFonts registry outside a
// browser: compiled by the dashboard's OWN vendored Babel (presets react,
// as jsxcheck does), then run in a vm with small stand-ins for the page
// (apiGet, document, localStorage, stateBus). The pytest module beside
// this file (test_web_fonts.py) asserts on what comes back.
//
// Usage: node fonts_registry_harness.js <repo-root>
// Output: JSON of the observations below.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(process.argv[2]);
const babelMod = require(path.join(root, 'web/static/vendor/babel/babel.min.js'));
const Babel = (babelMod && babelMod.transform) ? babelMod : (global.Babel || babelMod.default || babelMod);
const src = fs.readFileSync(path.join(root, 'web/static/fonts.jsx'), 'utf8');
const compiled = Babel.transform(src, { presets: ['react'], filename: 'fonts.jsx' }).code;

const head = [];
const loadsAsked = [];
const store = {};
let apiAnswer = null;
let apiCalls = 0;
const busSubs = [];
const element = (tag) => ({ tag, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } });

const sandbox = {
  console,
  window: {},
  API_BASE: '',
  setTimeout, clearTimeout,
  setInterval: () => 0,
  apiGet: async (p) => { apiCalls += 1; if (!apiAnswer) throw new Error('offline'); return JSON.parse(JSON.stringify(apiAnswer)); },
  apiPost: async () => ({ family: null }),
  apiDelete: async () => null,
  fetch: async () => ({ ok: false, json: async () => ({}) }),
  stateBus: { subscribe: (fn) => { busSubs.push(fn); return () => {}; } },
  localStorage: {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  },
  document: {
    head: { appendChild: (el) => head.push(el) },
    createElement: element,
    addEventListener: () => {},
    visibilityState: 'visible',
    fonts: { load: async (spec) => { loadsAsked.push(spec); return []; } },
  },
  React: { createElement: () => null, useState: () => [null, () => {}], useEffect: () => {}, useRef: () => ({}) },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(compiled, sandbox, { filename: 'fonts.jsx' });

const F = sandbox.window.DomovoiFonts;
const fam = (family, slug, category, source, faces) => ({
  family, slug, category, source, licence: 'OFL-1.1', weights: [400, 700], italic: true, faces,
});
const face = (slug, file, style, w) => ({ url: `/fonts/${slug}/${file}`, style, weight: w, format: 'woff2' });

(async () => {
  const out = {};
  out.exports = Object.keys(F).sort();
  out.pickerIsFunction = typeof sandbox.window.DomovoiFontPicker === 'function';
  out.settingsPanel = !!(sandbox.window.DomovoiSettingsSections
    && sandbox.window.DomovoiSettingsSections.fonts
    && typeof sandbox.window.DomovoiSettingsSections.fonts.Panel === 'function');
  out.listBeforeLoad = F.list().length;

  apiAnswer = { families: [
    fam('Lora', 'lora', 'serif', 'bundled', [face('lora', 'lora-normal-400-700.woff2', 'normal', [400, 700])]),
    fam('Liberation Sans', 'liberationsans', 'sans', 'bundled', [face('liberationsans', 'liberationsans-normal-400.woff2', 'normal', [400, 400])]),
    fam('Gloock', 'gloock', 'serif', 'fetched', [face('gloock', 'gloock-normal-400.ttf', 'normal', [400, 400])]),
  ] };
  let changes = 0;
  F.onChange(() => { changes += 1; });
  await F.load();
  out.listAfterLoad = F.list().map((f) => `${f.family}|${f.category}|${f.source}|${f.weights.join(',')}`);
  out.changesAfterFirstLoad = changes;
  out.listKeys = Object.keys(F.list()[0]).sort();

  out.status = {
    lora: F.status('Lora'), loraQuoted: F.status("'lora'"), arial: F.status('Arial'),
    calibri: F.status('Calibri'), comic: F.status('Comic Sans MS'), serif: F.status('serif'), empty: F.status(''),
  };
  out.stack = { lora: F.stack('Lora'), unknown: F.stack('Papyrus') };
  out.substitute = F.substituteFor('Times New Roman');

  out.ensureLora = await F.ensure('Lora', { weights: [400, 700], italic: true });
  out.ensureGloock = await F.ensure('Gloock');
  out.ensureMissing = await F.ensure('Comic Sans MS');
  out.ensureArial = await F.ensure('Arial');
  out.head = head.map((el) => ({ tag: el.tag, href: el.href || null, marker: el.attrs['data-domovoi-fonts'] || null,
                                 css: el.textContent || '' }));
  out.loadsAsked = loadsAsked;

  out.html = F.familiesInHTML('<p style="font-family: &quot;Lora&quot;, serif">a</p>'
    + '<span style="font-family:Comic Sans MS">b</span><font face="Gloock">c</font>'
    + '<span style="font-family: serif">d</span><span style=\'font-family: "Papyrus", fantasy\'>e</span>');
  out.missing = F.missingInHTML('<span style="font-family:Comic Sans MS">b</span><span style="font-family:Arial">x</span><span style="font-family:Lora">y</span>');

  F.markUsed('Lora'); F.markUsed('Gloock'); F.markUsed('lora');
  out.recent = F.recent();

  // A family added elsewhere: the realtime event re-reads, onChange fires once.
  apiAnswer.families.push(fam('Oi', 'oi', 'display', 'fetched', [face('oi', 'oi-normal-400.ttf', 'normal', [400, 400])]));
  const before = changes;
  busSubs.forEach((fn) => fn({ type: 'fonts.changed', data: [] }));
  await new Promise((r) => setTimeout(r, 20));
  out.changesAfterEvent = changes - before;
  out.hasOi = F.status('Oi');
  // The same list again: no change, no callback.
  await F.load(true);
  out.changesAfterSameList = changes - before;

  // Offline (/api/fonts refused): the bundled manifest is the fallback,
  // and a failure leaves the last list in place.
  apiAnswer = null;
  await F.load(true);
  out.afterRefusal = F.list().length;
  out.apiCalls = apiCalls;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
