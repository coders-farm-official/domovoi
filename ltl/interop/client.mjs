import { spawn } from 'node:child_process';
import readline from 'node:readline';
import { ClientHandshake, b64u, unb64u } from '../ltl-frontend/js/e2e.js';

const py = spawn('python3', ['-u', new URL('./home.py', import.meta.url).pathname]);
py.stderr.on('data', (d) => process.stderr.write(d));
const lines = readline.createInterface({ input: py.stdout });
const queue = [];
let resolveNext = null;
lines.on('line', (line) => {
  const msg = JSON.parse(line);
  if (resolveNext) { const r = resolveNext; resolveNext = null; r(msg); }
  else queue.push(msg);
});
const next = () => queue.length ? Promise.resolve(queue.shift())
                                : new Promise((r) => { resolveNext = r; });
const send = (obj) => py.stdin.write(JSON.stringify(obj) + '\n');

const fail = (m) => { console.error('FAIL:', m); process.exit(1); };

const householdMsg = await next();
const householdDh = unb64u(householdMsg.dh);

const deviceKey = await crypto.subtle.generateKey(
  { name: 'ECDH', namedCurve: 'P-256' }, false, ['deriveBits']);

const hs = new ClientHandshake(deviceKey, householdDh, 'd_interop');
send(await hs.hello());
const homeHello = await next();
if (homeHello.t !== 'home_hello') fail('expected home_hello, got ' + homeHello.t);

const { confirm, link } = await hs.finish(homeHello);
send(confirm);
const ack = await next();
if (ack.t !== 'sealed_ok') fail('home rejected our confirmation');
console.log('handshake: OK (mutual confirmation verified both ways)');

const message = 'GET /api/satellites — through the tunnel';
send({ t: 'sealed', frame: b64u(await link.seal(new TextEncoder().encode(message))) });

const opened = await next();
if (opened.text !== message) fail('home decrypted wrong plaintext: ' + opened.text);
console.log('client -> home: OK ("' + opened.text + '")');

const reply = await next();
const plain = new TextDecoder().decode(await link.open(unb64u(reply.frame)));
if (plain !== 'home received: ' + message) fail('client decrypted wrong plaintext: ' + plain);
console.log('home -> client: OK ("' + plain + '")');

// Replay must fail.
try {
  await link.open(unb64u(reply.frame));
  fail('replayed frame was accepted');
} catch (e) { console.log('replay rejected: OK (' + e.message + ')'); }

send({ t: 'done' });
py.stdin.end();
console.log('\nJS and Python implementations interoperate.');
