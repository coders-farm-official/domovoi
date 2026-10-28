// Headless check of the browser side of co-editing: the vendored bundles
// (web/static/vendor/yjs: Yjs, y-websocket's client, y-indexeddb) and
// web/static/collab.js, run in Node 22+
// (which has a WebSocket global) against a live web server.
//
//   node collab_client_harness.js <static dir> <http base> <doc path>
//
// Prints one JSON line with what happened; the pytest wrapper
// (test_collab_client_headless.py) judges it. No IndexedDB in Node: the
// helper's offline copy is skipped, as in a private window.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const [staticDir, base, docPath] = process.argv.slice(2);

globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.API_BASE = base;
globalThis.location = new URL(base);
for (const f of ['vendor/yjs/yjs.min.js', 'collab.js']) {
  vm.runInThisContext(fs.readFileSync(path.join(staticDir, f), 'utf8'), { filename: f });
}

const until = async (pred, what, ms = 10000) => {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (pred()) return;
    await new Promise((r) => setTimeout(r, 25));
  }
  throw new Error('timed out waiting for ' + what);
};

(async () => {
  const out = { ok: false };
  const saves = [];
  try {
    // Both register the same seeder; the server picks one, once.
    let seeds = 0;
    const seedWith = (ydoc) => { seeds += 1; ydoc.getText('text').insert(0, '# Plan\n'); };
    const A = await window.DomovoiCollab.open(docPath);
    A.onSeed(seedWith);
    await A.whenSynced;
    await A.whenReady;
    out.aSeed = seeds === 1;
    const B = await window.DomovoiCollab.open(docPath);
    B.onSeed(seedWith);
    await B.whenSynced;
    await B.whenReady;
    await new Promise((r) => setTimeout(r, 300));
    out.bSeed = seeds !== 1;
    out.bFirst = B.ydoc.getText('text').toString();

    B.setSaver(async ({ headers }) => {
      const text = B.ydoc.getText('text').toString();
      const r = await fetch(`${base}/api/documents/text/${encodeURIComponent(docPath)}`, {
        method: 'PUT',
        headers: Object.assign({ 'Content-Type': 'application/json', 'X-Requested-With': 'h' }, headers),
        body: JSON.stringify({ text }),
      });
      saves.push({ status: r.status, header: headers['X-Domovoi-Collab'], text });
    });

    // Concurrent edits on both sides.
    A.ydoc.getText('text').insert(7, 'from A\n');
    B.ydoc.getText('text').insert(0, 'B: ');
    const ta = () => A.ydoc.getText('text').toString();
    const tb = () => B.ydoc.getText('text').toString();
    await until(() => ta() === tb() && ta().includes('from A') && ta().startsWith('B: '), 'convergence');
    out.converged = ta();

    await until(() => A.presence().length === 2 && B.presence().length === 2, 'presence');
    out.presence = A.presence().map((p) => ({ name: p.name, self: p.self }));
    out.status = A.statusText();

    // B edited last, so B is asked to save once typing stops (~2.5 s).
    await until(() => saves.length >= 1, 'a save request', 15000);
    out.saves = saves.slice();

    // saveNow() (the editor's Ctrl+S / tab-hidden path since
    // wf/editors-followups): a co-editing save with the header, at once,
    // not after the idle wait, and never a plain one.
    const before = saves.length;
    B.ydoc.getText('text').insert(0, 'now ');
    await new Promise((r) => setTimeout(r, 100));
    const t0 = Date.now();
    out.saveNowSent = B.saveNow();
    await until(() => saves.length > before, 'a save after saveNow', 2000);
    out.saveNowMs = Date.now() - t0;
    out.saveNowSave = saves[saves.length - 1];

    // Leaving with an unsaved edit saves on close().
    out.savedMsgs = [];
    B.on('saved', (m) => out.savedMsgs.push(m));
    B.on('save-failed', (e) => out.savedMsgs.push({ failed: String(e) }));
    B.ydoc.getText('text').insert(0, 'last ');
    await new Promise((r) => setTimeout(r, 200));
    await B.close();
    out.savesAfterClose = saves.length;
    out.lastSaved = saves[saves.length - 1].text;
    await A.close();
    out.ok = true;
  } catch (e) {
    out.error = String((e && e.stack) || e);
  }
  console.log(JSON.stringify(out));
  setTimeout(() => process.exit(0), 50);
})();
