// Drive one dashboard component through clicks and typing, outside a
// browser. The sibling jsx_render_harness.js renders a component ONCE
// with every hook at its initial value; this one keeps hook state per
// component instance and re-renders after every event, so a scenario can
// click a trash icon and assert that a confirm dialog appeared, type into
// a form and assert what the page POSTed.
//
// The dashboard's JSX files are compiled with the SAME vendored Babel the
// browser uses and evaluated in one vm context, in the order index.html
// loads them, on top of a tiny React: createElement builds plain records,
// the hooks keep state on a per-instance "fiber" keyed by the element's
// position in the tree, and setState marks the root dirty so the whole
// tree renders again (effects run afterwards, cleanups first). Function
// components are expanded; `Icon` is kept as a leaf so its name is
// visible in the output.
//
// The data layer is scripted, never fetched: useApiObject/useApiList and
// apiGet/apiPost/apiPatch/apiDelete resolve from the scenario's `api`
// table ({"GET /api/x": body, "POST /api/y": body, ...}; missing keys
// resolve to null); every mutation is appended to `calls`.
//
// Usage: node jsx_interact_harness.js <repo-root> '<scenarios json>'
//   scenario: { files, component, props?, fnProps?, api?, setup?, script }
//   setup    — JS source evaluated INSIDE the sandbox before the files
//              load, for a scenario that needs a different Auth /
//              ServerStore / navigator than the defaults below.
//   script   — a JS function body run with (h) — the helpers below —
//              whose return value is the scenario's result (JSON).
// Helpers on h: render(), rerender(), tree(), find(sel), findAll(sel),
//   text(), click(sel), type(sel, value), change(sel, value),
//   submit(sel), key(sel, key), plain(el), calls, fnCalls, hookCalls,
//   api, settle(),
//   global(name) (a sandbox global, e.g. what a `setup` stub recorded).
//   sel is {type?, text?, title?, placeholder?, icon?, value?, name?, nth?}
//   or a predicate (el) => boolean; `text` and `title` match substrings.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = process.argv[2];
const scenarios = JSON.parse(process.argv[3]);

const babelMod = require(path.join(root, 'web/static/vendor/babel/babel.min.js'));
const Babel = babelMod.transform ? babelMod : (global.Babel || babelMod.default || babelMod);

const compiled = new Map();
const compile = (file) => {
  if (!compiled.has(file)) {
    const src = fs.readFileSync(path.join(root, file), 'utf8');
    compiled.set(file, Babel.transform(src, { presets: ['react'], filename: file }).code);
  }
  return compiled.get(file);
};

const LEAF_COMPONENTS = new Set(['Icon']);

const depsEqual = (a, b) =>
  Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));

/* ── the tiny React ─────────────────────────────────────────────────── */
const createRuntime = () => {
  const Fragment = Symbol('Fragment');
  const fibers = new Map();          // path → { hooks, pending, alive }
  let current = null;
  let hookIdx = 0;
  let dirty = false;

  const fiberHook = () => {
    if (!current) throw new Error('hook called outside a component render');
    return [current, hookIdx++];
  };

  const React = {
    Fragment,
    createElement(type, p, ...children) {
      const props = { ...(p || {}) };
      if (children.length) props.children = children.flat(Infinity);
      return { type, props };
    },
    useState(init) {
      const [f, i] = fiberHook();
      if (!(i in f.hooks)) f.hooks[i] = typeof init === 'function' ? init() : init;
      const set = (v) => {
        const next = typeof v === 'function' ? v(f.hooks[i]) : v;
        if (!Object.is(next, f.hooks[i])) { f.hooks[i] = next; dirty = true; }
      };
      return [f.hooks[i], set];
    },
    useReducer(reducer, init, lazy) {
      const [f, i] = fiberHook();
      if (!(i in f.hooks)) f.hooks[i] = lazy ? lazy(init) : init;
      const dispatch = (action) => {
        const next = reducer(f.hooks[i], action);
        if (!Object.is(next, f.hooks[i])) { f.hooks[i] = next; dirty = true; }
      };
      return [f.hooks[i], dispatch];
    },
    useRef(v) {
      const [f, i] = fiberHook();
      if (!(i in f.hooks)) f.hooks[i] = { current: v };
      return f.hooks[i];
    },
    useMemo(fn, deps) {
      const [f, i] = fiberHook();
      const prev = f.hooks[i];
      if (prev && depsEqual(prev.deps, deps)) return prev.value;
      const value = fn();
      f.hooks[i] = { value, deps };
      return value;
    },
    useCallback(fn, deps) { return React.useMemo(() => fn, deps); },
    useEffect(fn, deps) {
      const [f, i] = fiberHook();
      const prev = f.hooks[i];
      if (!prev) f.hooks[i] = { deps: undefined, cleanup: null };
      if (!prev || deps === undefined || !depsEqual(prev.deps, deps)) f.pending.push({ i, fn, deps });
    },
    useContext(ctx) { return ctx._value; },
    createContext(v) { return { _value: v, Provider: ({ children }) => children }; },
    memo: (c) => c,
    forwardRef: (c) => c,
  };
  React.useLayoutEffect = React.useEffect;

  let rootEl = null;
  let out = [];

  const textOf = (children) =>
    (children || []).filter((c) => typeof c === 'string' || typeof c === 'number').join('');

  const renderNode = (node, pathKey) => {
    if (node == null || typeof node === 'boolean') return;
    if (Array.isArray(node)) {
      node.forEach((c, i) => {
        const k = c && typeof c === 'object' && c.props && c.props.key != null ? `k${c.props.key}` : String(i);
        renderNode(c, `${pathKey}/${k}`);
      });
      return;
    }
    if (typeof node !== 'object') return;   // text: already on the parent's `text`
    if (node.type === Fragment) { renderNode(node.props.children, `${pathKey}/frag`); return; }
    if (typeof node.type === 'function') {
      const name = node.type.name || 'anon';
      if (LEAF_COMPONENTS.has(name)) { out.push({ type: name, props: node.props, text: '' }); return; }
      const key = `${pathKey}/${name}`;
      let f = fibers.get(key);
      if (!f) { f = { hooks: [], pending: [], alive: true }; fibers.set(key, f); }
      f.alive = true;
      const prevCurrent = current; const prevIdx = hookIdx;
      current = f; hookIdx = 0;
      let result;
      try { result = node.type(node.props); } finally { current = prevCurrent; hookIdx = prevIdx; }
      renderNode(result, key);
      return;
    }
    const el = { type: String(node.type), props: node.props, text: textOf(node.props.children) };
    out.push(el);
    renderNode(node.props.children, `${pathKey}/${el.type}`);
  };

  const flush = () => {
    let passes = 0;
    do {
      dirty = false;
      for (const f of fibers.values()) f.alive = false;
      out = [];
      renderNode(rootEl, '');
      for (const [key, f] of [...fibers.entries()]) {
        if (f.alive) continue;
        for (const h of f.hooks) if (h && typeof h.cleanup === 'function') h.cleanup();
        fibers.delete(key);
      }
      for (const f of fibers.values()) {
        const pending = f.pending; f.pending = [];
        for (const e of pending) {
          const h = f.hooks[e.i];
          if (h.cleanup) { try { h.cleanup(); } catch (_) {} }
          const c = e.fn();
          h.cleanup = typeof c === 'function' ? c : null;
          h.deps = e.deps;
        }
      }
      if (++passes > 60) throw new Error('render did not settle after 60 passes (effect/setState loop?)');
    } while (dirty);
    return out;
  };

  return {
    React,
    mount(el) { rootEl = el; return flush(); },
    rerender() { return flush(); },
    tree() { return out; },
  };
};

/* ── scripted data layer ────────────────────────────────────────────── */
const makeApi = (table) => {
  const calls = [];
  const lookup = (method, p) => {
    const key = `${method} ${p}`;
    if (key in table) return table[key];
    const bare = `${method} ${String(p).split('?')[0]}`;
    if (bare in table) return table[bare];
    return null;
  };
  const clone = (v) => (v == null ? v : JSON.parse(JSON.stringify(v)));
  const call = (method, p, body) => {
    calls.push({ method, path: p, body: clone(body) });
    const hit = lookup(method, p);
    if (hit && typeof hit === 'object' && hit.__error) {
      const e = new Error(hit.__error.message || `${hit.__error.status} error`);
      e.status = hit.__error.status; e.detail = hit.__error.detail;
      return Promise.reject(e);
    }
    return Promise.resolve(clone(hit));
  };
  return { calls, lookup, clone, call };
};

/* ── one scenario ───────────────────────────────────────────────────── */
const run = async ({ files, component, props = {}, fnProps = [], api: table = {}, setup = '', script }) => {
  const rt = createRuntime();
  const React = rt.React;
  const api = makeApi(table);
  const window = {
    location: { hash: '', href: 'http://test/' },
    confirm: () => true,
    addEventListener() {}, removeEventListener() {},
    lucide: null,
  };
  const noop = () => {};
  const sandbox = {
    window, console, React, setTimeout, clearTimeout, setInterval: () => 0, clearInterval: noop,
    URLSearchParams, FormData, encodeURIComponent, decodeURIComponent,
    document: { documentElement: { getAttribute: () => null, setAttribute: noop }, createElement: () => ({ setAttribute: noop, style: {} }) },
    localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
    navigator: { userAgent: 'harness' },
    apiGet: (p) => api.call('GET', p),
    apiPost: (p, body) => api.call('POST', p, body),
    apiPatch: (p, body) => api.call('PATCH', p, body),
    apiDelete: (p) => api.call('DELETE', p),
    apiUpload: (p, body) => api.call('UPLOAD', p, null),
    apiErrorText: (e) => String((e && e.message) || e),
    isAuthFailure: (e) => !!(e && e.loginPrompted),
    useStateEvents: noop,
    useDebouncedValue: (v) => v,
    stateBus: { subscribe: () => noop },
    DeviceIdentity: { id: () => 'dev-1', name: () => 'harness',
                      suggestedName: () => 'Harness on test',
                      register: () => Promise.resolve({ device_id: 'dev-1', name: 'harness' }),
                      rename: (name) => Promise.resolve({ device_id: 'dev-1', name }) },
    deviceDownload: (url) => api.calls.push({ method: 'DOWNLOAD', path: url, body: null }),
    usePlayback: () => ({ available: false, playItems: noop, enqueue: noop, playNext: noop }),
    NowPlayingPanel: () => null,
    Auth: { status: {}, subscribe: () => noop, isLoggedIn: () => true, headers: () => ({}), modalOpen: false },
    ServerStore: { current: () => null },
    fetch: () => Promise.reject(new Error('no network in the harness')),
    Audio: function () { return { play: () => Promise.resolve(), pause: noop }; },
  };
  // The scripted hooks read the table synchronously, so the first render
  // already sees the data (no loading flash to step through). A table
  // entry of {__error: {status, message}} is what a failed fetch leaves
  // in the hook: no data, `error` set. Every path a hook asked for is
  // recorded on h.hookCalls (deduplicated), so a test can assert that a
  // page never fetched something.
  const hookCalls = [];
  const hookLookup = (p) => {
    if (!p) return { hit: null, error: null };
    if (!hookCalls.includes(p)) hookCalls.push(p);
    const hit = api.clone(api.lookup('GET', p));
    const errored = hit && typeof hit === 'object' && !Array.isArray(hit) && hit.__error;
    if (!errored) return { hit, error: null };
    return { hit: null, error: Object.assign(new Error(hit.__error.message || 'error'), hit.__error) };
  };
  sandbox.useApiObject = (p) => {
    const { hit, error } = hookLookup(p);
    return { data: hit, loading: false, error, refresh: () => Promise.resolve() };
  };
  sandbox.useApiList = (p, { pickItems = (x) => x } = {}) => {
    const { hit, error } = hookLookup(p);
    const items = error ? [] : (pickItems(hit) || []);
    return { items, loading: false, error, refresh: () => Promise.resolve(), setItems: noop };
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  if (setup) vm.runInContext(setup, sandbox, { filename: 'setup.js' });
  for (const file of files) vm.runInContext(compile(file), sandbox, { filename: file });
  vm.runInContext(`window.__component = ${component};`, sandbox);
  const Component = sandbox.window.__component;
  if (typeof Component !== 'function') throw new Error(`${component} is not a component`);

  // A function prop is a RECORDER, not a bare noop: a component that
  // reports through a callback (an editor's `fire` toast, an overlay's
  // onClose) is only testable if the scenario can read what it said.
  // h.fnCalls is [{name, args}] in call order.
  const fnCalls = [];
  const fullProps = { ...props };
  for (const name of fnProps) {
    fullProps[name] = (...args) => {
      fnCalls.push({ name, args: args.map((a) => (a && typeof a === 'object' ? '[object]' : a)) });
    };
  }

  const settle = async () => { for (let i = 0; i < 4; i++) await new Promise((r) => setImmediate(r)); };
  const matches = (el, sel) => {
    if (typeof sel === 'function') return sel(el);
    const p = el.props || {};
    if (sel.type && el.type !== sel.type) return false;
    if (sel.text != null && !String(el.text).includes(sel.text)) return false;
    if (sel.title != null && !String(p.title || '').includes(sel.title)) return false;
    if (sel.placeholder != null && p.placeholder !== sel.placeholder) return false;
    if (sel.name != null && p.name !== sel.name) return false;
    if (sel.value != null && p.value !== sel.value) return false;
    if (sel.icon != null) {
      // a Button's icon prop or a leaf Icon element
      const icon = el.type === 'Icon' ? p.name : p.icon;
      if (icon !== sel.icon) return false;
    }
    return true;
  };
  const h = {
    calls: api.calls,
    fnCalls,
    hookCalls,
    api: table,
    settle,
    global(name) { return sandbox[name]; },
    render() { return rt.mount(React.createElement(Component, fullProps)); },
    // Render again with no event: what a scenario needs after `await
    // h.settle()` has let a mount-time fetch resolve.
    rerender() { return rt.rerender(); },
    tree() { return rt.tree(); },
    findAll(sel) { return rt.tree().filter((el) => matches(el, sel)); },
    find(sel) {
      const all = h.findAll(sel);
      const nth = (typeof sel === 'object' && sel.nth) || 0;
      return all[nth] || null;
    },
    text() { return rt.tree().map((el) => el.text).filter(Boolean); },
    plain(el) {
      if (!el) return null;
      const props = {};
      for (const [k, v] of Object.entries(el.props || {})) {
        if (k === 'children' || typeof v === 'function' || (v && typeof v === 'object')) continue;
        props[k] = v;
      }
      return { type: el.type, props, text: el.text };
    },
    async fire(sel, handler, event) {
      const el = h.find(sel);
      if (!el) throw new Error(`no element matches ${JSON.stringify(sel)}`);
      const fn = el.props[handler];
      if (typeof fn !== 'function') throw new Error(`${JSON.stringify(sel)} has no ${handler}`);
      const r = fn(event);
      if (r && typeof r.then === 'function') { try { await r; } catch (e) { h.lastError = String(e && e.message || e); } }
      await settle();
      rt.rerender();
      await settle();
      return rt.rerender();
    },
    click(sel) {
      return h.fire(sel, 'onClick', { preventDefault: noop, stopPropagation: noop, target: {} });
    },
    type(sel, value) {
      return h.fire(sel, 'onChange', { target: { value, checked: !!value }, preventDefault: noop });
    },
    change(sel, value) { return h.type(sel, value); },
    submit(sel) { return h.fire(sel, 'onSubmit', { preventDefault: noop }); },
    key(sel, key) { return h.fire(sel, 'onKeyDown', { key, preventDefault: noop }); },
  };
  const fn = new Function('h', `return (async () => { ${script} })();`);
  return await fn(h);
};

(async () => {
  const result = {};
  for (const [name, sc] of Object.entries(scenarios)) {
    try {
      result[name] = await run(sc);
    } catch (e) {
      result[name] = { __harness_error: String((e && e.stack) || e) };
    }
  }
  process.stdout.write(JSON.stringify(result));
})().catch((e) => { console.error((e && e.stack) || e); process.exit(2); });
