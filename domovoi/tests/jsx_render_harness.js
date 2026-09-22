// Render one dashboard component to a flat list of elements, outside a
// browser. Used by the pytest modules next to this file.
//
// The dashboard is Babel-in-browser JSX in one global scope, so a page
// file is compiled here with the SAME vendored Babel the browser uses and
// evaluated in a vm with a plain-object React: createElement builds
// {type, props} records, hooks return their initial values, and the
// shared components a page leans on (Stat, Card, Icon, ...) are stubs
// that keep their props. Function components are expanded recursively,
// so what comes out is every element the component would hand to React
// for the given props — enough to assert on labels, values and text.
//
// Usage: node jsx_render_harness.js <repo-root> '<scenarios json>'
//   scenario: { file, component, props, fnProps?, apiObject?, apiList?, preload? }
//   fnProps  — prop names to supply as no-op functions
//   apiObject/apiList — what useApiObject/useApiList return in this render
//   preload  — page files evaluated before `file` (e.g. web/static/components.jsx
//              when the component leans on a shared helper such as relTime or
//              webHref); a preloaded file's real components take the place of
//              the stubs below for that scenario
// Output: { <scenario name>: [ { type, props, text }, ... ] }
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

const STUBS = ['Stat', 'Empty', 'Card', 'Icon', 'Button', 'IconButton', 'Pill', 'Tabs',
               'PageHeader', 'StatusDot', 'Sidebar', 'Topbar', 'LoginModal'];

const render = ({ file, component, props = {}, fnProps = [], apiObject = null, apiList = null, preload = [] }) => {
  const React = {
    createElement: (type, p, ...children) => ({ type, props: { ...(p || {}), children: children.flat(Infinity) } }),
    Fragment: 'Fragment',
    useState: (v) => [typeof v === 'function' ? v() : v, () => {}],
    useEffect: () => {},
    useLayoutEffect: () => {},
    useMemo: (f) => f(),
    useCallback: (f) => f,
    useRef: (v) => ({ current: v }),
    useReducer: (r, v) => [v, () => {}],
  };
  const sandbox = { window: {}, console, React, setTimeout, clearTimeout };
  for (const name of STUBS) sandbox[name] = { [name]: (p) => ({ type: name, props: p }) }[name];
  sandbox.useApiObject = () => ({ data: null, loading: false, error: null, refresh() {}, ...(apiObject || {}) });
  sandbox.useApiList = () => ({ items: [], loading: false, error: null, refresh() {}, setItems() {}, ...(apiList || {}) });
  sandbox.useStateEvents = () => {};
  sandbox.useDebouncedValue = (v) => v;
  sandbox.stateBus = { subscribe: () => () => {} };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  for (const pre of preload) vm.runInContext(compile(pre), sandbox, { filename: pre });
  vm.runInContext(compile(file) + `\n;window.__component = ${component};`, sandbox, { filename: file });
  const Component = sandbox.window.__component;

  const fullProps = { ...props };
  for (const name of fnProps) fullProps[name] = () => {};

  const out = [];
  const walk = (node) => {
    if (node == null || typeof node === 'boolean') return;
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (typeof node !== 'object') return;
    if (typeof node.type === 'function' && !STUBS.includes(node.type.name)) {
      walk(node.type(node.props));
      return;
    }
    const type = typeof node.type === 'function' ? node.type.name : String(node.type);
    const rendered = typeof node.type === 'function' ? node.type(node.props) : node;
    const p = (rendered && rendered.props) || {};
    const plain = {};
    for (const [k, v] of Object.entries(p)) {
      if (k === 'children' || typeof v === 'function' || (v && typeof v === 'object')) continue;
      plain[k] = v;
    }
    const text = (p.children || []).filter((c) => typeof c === 'string' || typeof c === 'number').join('');
    out.push({ type, props: plain, text });
    walk(p.children);
  };
  walk(Component(fullProps));
  return out;
};

const result = {};
for (const [name, sc] of Object.entries(scenarios)) result[name] = render(sc);
process.stdout.write(JSON.stringify(result));
