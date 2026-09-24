// Run the SHIPPED web/static/auth.js and report what its
// `normalizeDeviceToken` does to a table of values, so a Python test can
// hold it against domovoi.admin_auth.normalize_device_token character for
// character.
//
// This matters because the set dialog (web/static/settings.jsx) tells the
// admin, live as they type, WHICH of the two matching rules their token
// will be stored under — forgiving (the stored token is its own canonical
// form) or exact. If the JS canonical test and the Python one ever part
// company, that line lies about how the token will behave, which is worse
// than saying nothing at all.
//
// auth.js is loaded the way the browser loads it (a plain script that
// declares `const Auth = (() => { ... })()` at top level), in a vm context
// with just enough of a browser around it. Nothing is stubbed in place of
// the function under test.
//
// Usage: node device_token_normalize_harness.js <repo-root> '<json>'
//   json: { "values": [...], "extra": { "<name>": "<js arrow-fn source>" } }
//   out:  { "auth": { "<value>": "<result>" },
//           "extra": { "<name>": { "<value>": "<result>" } } }
// `extra` exists so a test can put the copy of the helper it stubs into a
// JSX-harness scenario through the same table and prove the stub has not
// drifted from the shipped one.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = process.argv[2];
const { values, extra = {} } = JSON.parse(process.argv[3]);

const noop = () => {};
const sandbox = {
  console,
  setTimeout,
  clearTimeout,
  encodeURIComponent,
  decodeURIComponent,
  URLSearchParams,
  window: { location: { hash: '', href: 'http://test/', origin: 'http://test' },
            addEventListener: noop, removeEventListener: noop },
  document: { documentElement: { getAttribute: () => null, setAttribute: noop },
              addEventListener: noop },
  localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
  navigator: { userAgent: 'harness' },
  fetch: () => Promise.reject(new Error('no network in the harness')),
};
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
vm.createContext(sandbox);

const src = fs.readFileSync(path.join(root, 'web/static/auth.js'), 'utf8');
vm.runInContext(src, sandbox, { filename: 'web/static/auth.js' });

// `const Auth` is a lexical binding in the context's global scope, not a
// property of the sandbox object, so it is read back with a script.
const normalize = vm.runInContext('Auth.normalizeDeviceToken', sandbox);
if (typeof normalize !== 'function') {
  throw new Error('web/static/auth.js no longer exports normalizeDeviceToken');
}

const table = (fn) => {
  const out = {};
  for (const v of values) out[v] = fn(v);
  return out;
};

const result = { auth: table(normalize), extra: {} };
for (const [name, source] of Object.entries(extra)) {
  const fn = vm.runInContext(`(${source})`, sandbox, { filename: `extra:${name}` });
  result.extra[name] = table(fn);
}
process.stdout.write(JSON.stringify(result));
