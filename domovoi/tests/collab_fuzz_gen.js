// Seeded fuzz for co-editing: three clients on the vendored Yjs bundle make
// random edits (text inserts and deletes, paragraphs in the XML fragment,
// marks, list nesting, map values), and every update each one produces is
// recorded in the order a server would relay it. Deliveries between the
// clients are batched at random, so concurrent edits really interleave.
//
//   node collab_fuzz_gen.js <static dir> <seed> <steps>
//
// Prints JSON: {updates: [[client, hex], ...], final: hex}. The pytest
// side (test_collab_review.py) replays the updates into a room, one socket
// per client, and checks nothing is refused and the room ends where Yjs did.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const [staticDir, seedArg, stepsArg] = process.argv.slice(2);
globalThis.window = globalThis;
vm.runInThisContext(fs.readFileSync(path.join(staticDir, 'vendor/yjs/yjs.min.js'), 'utf8'));
const Y = window.Y;

let seed = Number(seedArg) >>> 0;
const rnd = () => {                       // mulberry32
  seed = (seed + 0x6D2B79F5) >>> 0;
  let t = seed;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};
const pick = (n) => Math.floor(rnd() * n);
const hex = (u) => Buffer.from(u).toString('hex');

const docs = [0, 1, 2].map((i) => { const d = new Y.Doc(); d.clientID = 1000 + i; return d; });
const outbox = [[], [], []];
const relayed = [];
docs.forEach((d, i) => d.on('update', (u, origin) => { if (origin !== 'relay') outbox[i].push(u); }));

function deliver(i) {
  for (const u of outbox[i].splice(0)) {
    relayed.push([i, hex(u)]);
    docs.forEach((d, j) => { if (j !== i) Y.applyUpdate(d, u, 'relay'); });
  }
}

// Emoji only in paragraphs (deleted whole): a random delete in the plain
// text root can split a surrogate pair, which Yjs and yrs encode
// differently (an editor never does that: it deletes whole characters).
const words = ['milk', 'eggs', 'bread', 'ünïcode', 'tea', ' ', '\n'];
const paraWords = words.concat(['😀 emoji']);

function edit(d) {
  const op = pick(7);
  const text = d.getText('text');
  const frag = d.getXmlFragment('default');
  if (op === 0 || (op === 1 && text.length === 0)) {
    text.insert(pick(text.length + 1), words[pick(words.length)], pick(3) === 0 ? { bold: true } : undefined);
  } else if (op === 1) {
    const at = pick(text.length);
    text.delete(at, Math.min(1 + pick(4), text.length - at));
  } else if (op === 2 || frag.length === 0) {
    const p = new Y.XmlElement(pick(2) ? 'paragraph' : 'heading');
    if (p.nodeName === 'heading') p.setAttribute('level', String(1 + pick(3)));
    const t = new Y.XmlText();
    t.insert(0, paraWords[pick(paraWords.length)]);
    p.insert(0, [t]);
    frag.insert(pick(frag.length + 1), [p]);
  } else if (op === 3) {
    frag.delete(pick(frag.length), 1);
  } else if (op === 4) {
    // a list nested a few levels (well inside the depth limit)
    let outer = new Y.XmlElement('bulletList');
    const top = outer;
    for (let k = 0; k < 1 + pick(4); k++) {
      const li = new Y.XmlElement('listItem');
      const inner = new Y.XmlElement('bulletList');
      li.insert(0, [inner]);
      outer.insert(0, [li]);
      outer = inner;
    }
    frag.insert(pick(frag.length + 1), [top]);
  } else if (op === 5) {
    const el = frag.get(pick(frag.length));
    // the whole run: formatting one UTF-16 unit could split an emoji (above)
    if (el && el.get && el.get(0) && el.get(0).format) el.get(0).format(0, el.get(0).length, { italic: true });
  } else if (text.length > 1) {
    // marks with JSON values (a link), on the plain-text root
    const at = pick(text.length - 1);
    text.format(at, 1 + pick(text.length - at - 1), pick(2) ? { link: { href: 'https://x', n: pick(9) } } : { bold: null });
  }
}

const steps = Number(stepsArg) || 400;
for (let s = 0; s < steps; s++) {
  edit(docs[pick(3)]);
  if (pick(4) === 0) deliver(pick(3));    // batched, interleaved deliveries
}
for (let i = 0; i < 3; i++) deliver(i);
console.log(JSON.stringify({ updates: relayed, final: hex(Y.encodeStateAsUpdate(docs[0])) }));
