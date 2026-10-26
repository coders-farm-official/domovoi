/* The Documents editor: a live, one-pane editor that feels like a word
 * processor (window.DomovoiDocEditor), and the full-screen surface the
 * Files page and the phone app open it in (window.DocEditor).
 *
 * Built on Tiptap 2 / ProseMirror (MIT), vendored in vendor/tiptap with
 * Yjs in vendor/yjs (scripts/vendor/editors/build.mjs); both are loaded
 * the first time a document opens, never with the dashboard.
 *
 *   DomovoiDocEditor.create(el, { content, format: 'html'|'md'|'docx',
 *       collab?: { ydoc, provider, user, field?, seed? }, fonts, onChange,
 *       readOnly, onSaveAsFormatted?, onNotice? })
 *     → { ready, getHTML(), getMarkdown(), getJSON(), setContent(html),
 *         isEmpty(), focus(), destroy(), setFonts(registry), tiptap, format }
 *
 * Three kinds of file, each saved as itself (web/backend/api/documents.py,
 * PUT /api/documents/doc):
 *   * a formatted document (.html): everything the toolbar does, pictures
 *     embedded, one self-contained page; people see it as "Document";
 *   * markdown (.md): the same editor, saved as markdown; fonts, colours,
 *     sizes and alignment are greyed out (markdown can't hold them), with
 *     one press to "Save as formatted document";
 *   * Word (.docx): converted on open (mammoth); on save the person picks
 *     Word (formatting may shift) or a formatted document.
 *
 * Everything that reaches the editor goes through the document allowlist
 * first (sanitize_html.js, sanitizeDocumentHtml), and the server applies
 * its own on every load and save (domovoi/richdoc). Pictures are
 * downscaled to about 2000 px and compressed in the browser before they
 * are embedded.
 *
 * The whole file is one function scope: the dashboard's scripts share one
 * global scope, so nothing here is declared at the top level. No object
 * rest destructuring either (Babel hoists a `_excluded` constant for it).
 */
(() => {
  const VENDOR = {
    yjs: '/vendor/yjs/yjs.min.js',
    tiptap: '/vendor/tiptap/tiptap.min.js',
    mammoth: '/vendor/mammoth/mammoth.min.js',
  };

  /* ───────────────────────── loading ───────────────────────── */
  const pending = {};
  const loadScript = (src) => {
    if (pending[src]) return pending[src];
    pending[src] = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src;
      s.async = false;
      s.onload = () => resolve();
      s.onerror = () => { delete pending[src]; reject(new Error(`couldn't load ${src}`)); };
      document.head.appendChild(s);
    });
    return pending[src];
  };
  // Yjs first, always: the tiptap bundle's y-prosemirror is bound to window.Y.
  const loadYjs = () => (window.Y ? Promise.resolve(window.Y) : loadScript(VENDOR.yjs).then(() => window.Y));
  const load = () => loadYjs()
    .then(() => (window.DomovoiTiptap ? null : loadScript(VENDOR.tiptap)))
    .then(() => window.DomovoiTiptap);
  const loadMammoth = () => (window.mammoth ? Promise.resolve(window.mammoth)
    : loadScript(VENDOR.mammoth).then(() => window.mammoth));

  const sanitizeDoc = (html) => {
    if (!window.sanitizeDocumentHtml) throw new Error('the document sanitiser is not loaded');
    return window.sanitizeDocumentHtml(html || '');
  };

  /* ───────────────────────── fonts ───────────────────────── */
  const GENERIC = { serif: 'serif', monospace: 'monospace', mono: 'monospace',
    handwriting: 'cursive', display: 'sans-serif', 'sans-serif': 'sans-serif', sans: 'sans-serif' };
  // Used when builder C's registry (window.DomovoiFonts) is not on the page.
  const BUILTIN_FONTS = [
    { family: 'Serif', label: 'Serif', category: 'serif', css: 'Georgia, "Times New Roman", serif' },
    { family: 'Monospace', label: 'Monospace', category: 'monospace', css: 'ui-monospace, Consolas, "Courier New", monospace' },
    { family: 'Inter', label: 'Inter', category: 'sans-serif', css: '"Inter", "Inter var", system-ui, sans-serif' },
    { family: 'JetBrains Mono', label: 'JetBrains Mono', category: 'monospace', css: '"JetBrains Mono", ui-monospace, monospace' },
  ];
  // The registry (builder C, web/static/fonts.jsx) writes the CSS value
  // itself (stack: the family, then its group's generic), so a document
  // names a font the same way whichever editor set it.
  let activeRegistry = null;
  const fontCss = (f) => {
    if (f.css) return f.css;
    if (activeRegistry && typeof activeRegistry.stack === 'function') {
      try { return activeRegistry.stack(f.family); } catch (e) { /* below */ }
    }
    return `"${f.family}", ${GENERIC[(f.category || '').toLowerCase()] || 'sans-serif'}`;
  };
  const firstFamily = (css) => String(css || '').split(',')[0].trim().replace(/^['"]|['"]$/g, '');
  const normaliseFonts = (list) => (list || [])
    .filter((f) => f && f.family && /^[A-Za-z0-9 _-]{1,80}$/.test(f.family))
    .map((f) => ({ family: f.family, label: f.label || f.family, category: f.category || 'sans-serif',
                   css: f.css || null, source: f.source || 'builtin' }));
  const fontsFrom = async (registry) => {
    try {
      if (registry && typeof registry.list === 'function') {
        // fonts.jsx: list() is empty until load() has read /api/fonts
        if (typeof registry.load === 'function') await registry.load();
        const got = normaliseFonts(await registry.list());
        if (got.length) return got;
      }
    } catch (e) { /* fall back to the built-in list */ }
    return BUILTIN_FONTS;
  };
  const ensureFont = (registry, family) => {
    try { if (registry && typeof registry.ensure === 'function') return Promise.resolve(registry.ensure(family)); } catch (e) { /* the font just falls back */ }
    return Promise.resolve();
  };
  // The families a document names (its text styles), for the picker.
  const documentFamilies = (editor) => {
    const used = new Set();
    editor.state.doc.descendants((n) => n.marks.forEach((m) => {
      if (m.type.name === 'textStyle' && m.attrs.fontFamily) used.add(firstFamily(m.attrs.fontFamily));
    }));
    return Array.from(used);
  };
  const FONT_SIZES = [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 60, 72];

  const TEXT_COLOURS = [
    '#000000', '#434343', '#666666', '#999999', '#b7b7b7', '#d9d9d9', '#efefef', '#ffffff',
    '#980000', '#e60000', '#ff9900', '#e6b800', '#00a933', '#00a3a3', '#1a5fb4', '#9900ff', '#c2185b',
    '#5b0f00', '#7f6000', '#274e13', '#0c343d', '#073763', '#20124d', '#4c1130',
  ];
  const HIGHLIGHTS = ['#fff3a3', '#ffe0b2', '#ffcdd2', '#f8bbd0', '#e1bee7', '#c5cae9',
    '#bbdefb', '#b2ebf2', '#c8e6c9', '#dcedc8', '#eeeeee'];

  /* ───────────────────────── pictures ───────────────────────── */
  const MAX_EDGE = 2000;
  const JPEG_QUALITY = 0.86;
  const MAX_SOURCE_BYTES = 60 * 1024 * 1024;
  const RASTER = /^data:image\/(png|jpe?g|gif|webp|avif|bmp);base64,/i;

  const decodePicture = async (blob) => {
    if (window.createImageBitmap) {
      try { return await createImageBitmap(blob, { imageOrientation: 'from-image' }); } catch (e) { /* older engines: an <img> below */ }
    }
    const url = URL.createObjectURL(blob);
    try {
      const img = new Image();
      img.decoding = 'async';
      await new Promise((res, rej) => { img.onload = res; img.onerror = () => rej(new Error('not a picture')); img.src = url; });
      return img;
    } finally { setTimeout(() => URL.revokeObjectURL(url), 1000); }
  };
  const hasTransparency = (ctx, w, h) => {
    const data = ctx.getImageData(0, 0, w, h).data;
    for (let i = 3; i < data.length; i += 4) if (data[i] < 250) return true;
    return false;
  };
  /* A canvas → the data URI that goes in the document: PNG when the
   * picture has see-through parts, JPEG (much smaller for photos) otherwise. */
  const encodeCanvas = (canvas, maybeAlpha) => {
    const ctx = canvas.getContext('2d');
    if (maybeAlpha && hasTransparency(ctx, canvas.width, canvas.height)) return canvas.toDataURL('image/png');
    // JPEG has no transparency: paint white under it so nothing goes black.
    const flat = document.createElement('canvas');
    flat.width = canvas.width; flat.height = canvas.height;
    const fctx = flat.getContext('2d');
    fctx.fillStyle = '#ffffff';
    fctx.fillRect(0, 0, flat.width, flat.height);
    fctx.drawImage(canvas, 0, 0);
    return flat.toDataURL('image/jpeg', JPEG_QUALITY);
  };
  const pictureFromBlob = async (blob) => {
    if (!blob || !/^image\//.test(blob.type || 'image/')) throw new Error('That file is not a picture.');
    if (blob.size > MAX_SOURCE_BYTES) throw new Error('That picture is too large (over 60 MB).');
    const src = await decodePicture(blob);
    const w0 = src.width || src.naturalWidth;
    const h0 = src.height || src.naturalHeight;
    if (!w0 || !h0) throw new Error('That picture could not be read.');
    const scale = Math.min(1, MAX_EDGE / Math.max(w0, h0));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(w0 * scale));
    canvas.height = Math.max(1, Math.round(h0 * scale));
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(src, 0, 0, canvas.width, canvas.height);
    if (src.close) src.close();
    const maybeAlpha = !/jpe?g/i.test(blob.type || '');
    return { src: encodeCanvas(canvas, maybeAlpha), width: canvas.width, height: canvas.height };
  };
  const dataUrlToBlob = async (url) => (await fetch(url)).blob();

  /* ───────────────────────── markdown ───────────────────────── */
  let mdParser = null;
  const markdownToHtml = (T, md) => {
    if (!mdParser) mdParser = new T.MarkdownIt({ html: true, linkify: false, breaks: false });
    const html = mdParser.render(String(md || ''));
    // markdown-it has no task lists: "- [ ] x" / "- [x] x" become the
    // editor's checklist.
    const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html');
    // A list can mix both ("- milk" then "- [ ] call"), so it is split into
    // runs: plain items stay a bulleted list, checklist items become one.
    const marker = /^\s*\[( |x|X)\]\s+/;
    Array.from(doc.querySelectorAll('ul')).forEach((ul) => {
      const items = Array.from(ul.children).filter((c) => c.tagName === 'LI');
      if (!items.some((li) => marker.test(li.textContent))) return;
      const runs = [];
      items.forEach((li) => {
        const task = marker.test(li.textContent);
        if (!runs.length || runs[runs.length - 1].task !== task) runs.push({ task, items: [] });
        runs[runs.length - 1].items.push(li);
      });
      runs.forEach((run) => {
        const list = doc.createElement('ul');
        if (run.task) list.setAttribute('data-type', 'taskList');
        run.items.forEach((li) => {
          if (run.task) {
            // the first text that isn't the whitespace between tags
            const walker = doc.createTreeWalker(li, NodeFilter.SHOW_TEXT);
            let first = walker.nextNode();
            while (first && !first.nodeValue.trim()) first = walker.nextNode();
            const m = first && marker.exec(first.nodeValue);
            li.setAttribute('data-type', 'taskItem');
            li.setAttribute('data-checked', m && /x/i.test(m[1]) ? 'true' : 'false');
            if (first && m) first.nodeValue = first.nodeValue.replace(marker, '');
          }
          list.appendChild(li);
        });
        ul.parentNode.insertBefore(list, ul);
      });
      ul.remove();
    });
    return unwrapLonePictures(doc.body.innerHTML);
  };

  const buildMarkdownSerializer = (T) => {
    const D = T.defaultMarkdownSerializer;
    const cellText = (serializer, cell) => serializer.serialize(cell)
      .trim().replace(/\n{2,}/g, '<br>').replace(/\n/g, ' ').replace(/\|/g, '\\|');
    let self = null;
    const nodes = {
      doc: (state, node) => state.renderContent(node),
      paragraph: D.nodes.paragraph,
      text: D.nodes.text,
      heading: (state, node) => {
        state.write(`${'#'.repeat(Math.min(6, node.attrs.level || 1))} `);
        state.renderInline(node);
        state.closeBlock(node);
      },
      blockquote: D.nodes.blockquote,
      horizontalRule: (state, node) => { state.write('---'); state.closeBlock(node); },
      hardBreak: D.nodes.hard_break,
      codeBlock: (state, node) => {
        const fence = node.textContent.indexOf('```') === -1 ? '```' : '~~~~';
        state.write(fence + (node.attrs.language || '') + '\n');
        state.text(node.textContent, false);
        state.ensureNewLine();
        state.write(fence);
        state.closeBlock(node);
      },
      bulletList: (state, node) => state.renderList(node, '  ', () => '- '),
      orderedList: (state, node) => {
        const start = node.attrs.start || 1;
        const w = String(start + node.childCount - 1).length;
        const space = ' '.repeat(w + 2);
        state.renderList(node, space, (i) => {
          const n = String(start + i);
          return `${' '.repeat(w - n.length)}${n}. `;
        });
      },
      listItem: (state, node) => state.renderContent(node),
      taskList: (state, node) => state.renderList(node, '  ', () => '- '),
      taskItem: (state, node) => {
        state.write(node.attrs.checked ? '[x] ' : '[ ] ');
        state.renderContent(node);
      },
      figure: (state, node) => {
        const alt = String(node.attrs.alt || node.attrs.caption || '').replace(/[\[\]\\]/g, '');
        state.write(`![${alt}](${node.attrs.src})`);
        state.closeBlock(node);
      },
      table: (state, node) => {
        const rows = [];
        node.forEach((row) => {
          const cells = [];
          row.forEach((cell) => cells.push(cellText(self, cell)));
          rows.push(cells);
        });
        const width = Math.max(1, ...rows.map((r) => r.length));
        rows.forEach((r, i) => {
          while (r.length < width) r.push('');
          state.write(`| ${r.join(' | ')} |`);
          state.ensureNewLine();
          if (i === 0) {
            state.write(`|${' --- |'.repeat(width)}`);
            state.ensureNewLine();
          }
        });
        state.closeBlock(node);
      },
      tableRow: () => {},
      tableCell: (state, node) => state.renderContent(node),
      tableHeader: (state, node) => state.renderContent(node),
    };
    const marks = {
      bold: { open: '**', close: '**', mixable: true, expelEnclosingWhitespace: true },
      italic: { open: '*', close: '*', mixable: true, expelEnclosingWhitespace: true },
      strike: { open: '~~', close: '~~', mixable: true, expelEnclosingWhitespace: true },
      code: D.marks.code,
      link: D.marks.link,
      underline: { open: '<u>', close: '</u>', mixable: true, expelEnclosingWhitespace: true },
      highlight: { open: '<mark>', close: '</mark>', mixable: true, expelEnclosingWhitespace: true },
      textStyle: { open: '', close: '', mixable: true },
    };
    // tight lists: no blank line between items, as people write them
    self = new T.MarkdownSerializer(nodes, marks, { tightLists: true });
    return self;
  };

  /* A picture alone in a paragraph (how Word and web pages hold one) is a
   * picture, not a paragraph around one: without this, each leaves an
   * empty line above and below it in the document. */
  const unwrapLonePictures = (html) => {
    const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html');
    doc.querySelectorAll('p').forEach((p) => {
      const imgs = p.querySelectorAll('img');
      if (imgs.length && !p.textContent.trim()) {
        imgs.forEach((img) => p.parentNode.insertBefore(img, p));
        p.remove();
      }
    });
    return doc.body.innerHTML;
  };

  /* ───────────────────────── pasted Word / web HTML ───────────────────────── */
  /* Word puts its lists in as paragraphs with mso-list styles and a typed
   * bullet; Google Docs wraps the whole selection in a bold tag that isn't
   * bold. Both are turned into what the editor understands before
   * ProseMirror reads them (and the allowlist runs after). */
  const cleanPastedHtml = (html) => {
    let src = String(html || '');
    const fromWord = /urn:schemas-microsoft-com:office|class="?Mso|mso-/i.test(src);
    if (fromWord) {
      src = src.replace(/<!--\[if[\s\S]*?<!\[endif\]-->/gi, '')
        .replace(/<\/?o:p>/gi, '').replace(/<\/?(w|v|o|m):[^>]*>/gi, '');
    }
    const doc = new DOMParser().parseFromString(src, 'text/html');
    // Google Docs
    doc.querySelectorAll('b[id^="docs-internal-guid"]').forEach((b) => {
      const parent = b.parentNode;
      while (b.firstChild) parent.insertBefore(b.firstChild, b);
      parent.removeChild(b);
    });
    if (fromWord) {
      const paras = Array.from(doc.body.querySelectorAll('p'));
      let list = null; let listKind = null; let lastLevel = 1; let stack = [];
      paras.forEach((p) => {
        const style = p.getAttribute('style') || '';
        const m = /mso-list:\s*\S+\s+level(\d)/i.exec(style) || (/MsoListParagraph/i.test(p.className) ? [null, '1'] : null);
        if (!m) { list = null; stack = []; return; }
        const level = Math.max(1, Math.min(6, parseInt(m[1], 10) || 1));
        const ignore = p.querySelector('span[style*="mso-list:Ignore"], span[style*="mso-list: Ignore"]');
        const bullet = ignore ? ignore.textContent.trim() : '';
        const ordered = /^(\(?[0-9]+[.)]|\(?[a-zA-Z][.)]|\(?[ivxlcdmIVXLCDM]+[.)])$/.test(bullet);
        if (ignore) ignore.remove();
        const li = doc.createElement('li');
        const inner = doc.createElement('p');
        while (p.firstChild) inner.appendChild(p.firstChild);
        li.appendChild(inner);
        const kind = ordered ? 'ol' : 'ul';
        if (!list || (level === 1 && kind !== listKind)) {
          list = doc.createElement(kind);
          listKind = kind;
          p.parentNode.insertBefore(list, p);
          stack = [list];
          lastLevel = 1;
        }
        while (level > stack.length) {
          const parentLi = stack[stack.length - 1].lastElementChild;
          const sub = doc.createElement(kind);
          (parentLi || stack[stack.length - 1]).appendChild(sub);
          stack.push(sub);
        }
        while (level < stack.length) stack.pop();
        stack[stack.length - 1].appendChild(li);
        lastLevel = level;
        p.remove();
      });
      // Word's empty spacer paragraphs and its style attribute noise
      doc.body.querySelectorAll('[style]').forEach((el) => {
        const kept = (el.getAttribute('style') || '').split(';')
          .filter((d) => /^\s*(color|background(-color)?|font-family|font-size|text-align|font-weight|font-style|text-decoration[a-z-]*)\s*:/i.test(d)
            && !/mso-/i.test(d));
        if (kept.length) el.setAttribute('style', kept.join(';')); else el.removeAttribute('style');
      });
      void lastLevel;
    }
    // Pictures Word pastes as file:// paths can never be loaded here.
    doc.querySelectorAll('img').forEach((img) => {
      const s = img.getAttribute('src') || '';
      if (!RASTER.test(s) && !/^https?:/i.test(s)) img.remove();
    });
    return unwrapLonePictures(doc.body.innerHTML);
  };

  /* ───────────────────────── the editor's own CSS ───────────────────────── */
  const CSS = `
.de-shell { display: flex; flex-direction: column; min-height: 0; height: 100%; background: var(--canvas, #f3f3f1); }
.de-toolbar { display: flex; align-items: center; gap: 2px; padding: 6px 10px; border-bottom: 1px solid var(--border);
  background: var(--card); overflow-x: auto; overflow-y: hidden; white-space: nowrap; scrollbar-width: thin; flex-shrink: 0; }
.de-toolbar::-webkit-scrollbar { height: 4px; }
.de-btn { display: inline-flex; align-items: center; justify-content: center; gap: 4px; min-width: 32px; height: 32px; padding: 0 6px;
  border: 1px solid transparent; border-radius: var(--r-sm, 6px); background: transparent; color: var(--fg); cursor: pointer;
  font: inherit; font-size: 13px; flex-shrink: 0; }
.de-btn:hover:not(:disabled) { background: var(--brand-soft); }
.de-btn.on { background: var(--brand-soft); color: var(--brand-ink, var(--fg)); border-color: var(--brand-ring, transparent); }
.de-btn:disabled { opacity: .38; cursor: not-allowed; }
.de-btn:focus-visible, .de-select:focus-visible { outline: 2px solid var(--brand); outline-offset: 1px; }
.de-sep { width: 1px; height: 20px; background: var(--border); margin: 0 4px; flex-shrink: 0; }
.de-select { height: 32px; border: 1px solid var(--border); border-radius: var(--r-sm, 6px); background: var(--card); color: var(--fg);
  font: inherit; font-size: 13px; padding: 0 6px; flex-shrink: 0; max-width: 150px; }
.de-select:disabled { opacity: .38; cursor: not-allowed; }
.de-swatch-under { display: block; height: 3px; width: 16px; border-radius: 1px; margin-top: -2px; }
.de-colour-btn { flex-direction: column; gap: 0; }
.de-note { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 6px 14px; font-size: 12px; color: var(--fg-muted);
  background: var(--sunken); border-bottom: 1px solid var(--border-soft, var(--border)); flex-shrink: 0; }
.de-scroll { flex: 1; min-height: 0; overflow: auto; padding: 24px 16px 64px; }
.de-paper { box-sizing: border-box; max-width: 816px; min-height: 1056px; margin: 0 auto; padding: 72px 80px; background: #fff; color: #1d1d1f;
  box-shadow: 0 1px 3px rgba(0,0,0,.14), 0 0 0 1px rgba(0,0,0,.04); color-scheme: light; }
.de-paper .ProseMirror { outline: none; min-height: 900px; font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  overflow-wrap: break-word; caret-color: #1d1d1f; }
.de-paper .ProseMirror > :first-child { margin-top: 0; }
.de-paper h1 { font-size: 2em; line-height: 1.25; margin: .8em 0 .4em; }
.de-paper h2 { font-size: 1.5em; line-height: 1.3; margin: .9em 0 .4em; }
.de-paper h3 { font-size: 1.25em; margin: 1em 0 .4em; }
.de-paper h4 { font-size: 1em; margin: 1em 0 .4em; }
.de-paper p { margin: 0 0 .7em; }
.de-paper a { color: #1a5fb4; text-decoration: underline; cursor: text; }
.de-paper blockquote { margin: 1em 0; padding: .2em 0 .2em 1em; border-left: 3px solid #c8c8c8; color: #555; }
.de-paper pre { background: #f5f5f4; border-radius: 6px; padding: 12px 14px; overflow-x: auto; font: 14px/1.5 ui-monospace, "JetBrains Mono", Consolas, monospace; color: #1d1d1f; }
.de-paper code { font-family: ui-monospace, "JetBrains Mono", Consolas, monospace; font-size: .92em; background: #f1f1ef; border-radius: 4px; padding: .1em .3em; }
.de-paper pre code { background: none; padding: 0; font-size: inherit; }
.de-paper hr { border: 0; border-top: 1px solid #d0d0d0; margin: 1.5em 0; }
.de-paper mark { background: #fff3a3; color: inherit; padding: 0 .1em; }
.de-paper ul, .de-paper ol { padding-left: 1.6em; margin: 0 0 .7em; }
.de-paper li > p { margin: 0 0 .2em; }
.de-paper ul[data-type="taskList"] { list-style: none; padding-left: .2em; }
.de-paper ul[data-type="taskList"] li { display: flex; gap: .5em; align-items: flex-start; }
.de-paper ul[data-type="taskList"] li > label { flex: 0 0 auto; margin-top: .25em; user-select: none; }
.de-paper ul[data-type="taskList"] li > label input { width: 16px; height: 16px; cursor: pointer; }
.de-paper ul[data-type="taskList"] li > div { flex: 1 1 auto; min-width: 0; }
.de-paper ul[data-type="taskList"] li[data-checked="true"] > div { color: #777; text-decoration: line-through; }
.de-paper .tableWrapper { overflow-x: auto; margin: 1em 0; }
.de-paper table { border-collapse: collapse; width: 100%; table-layout: fixed; margin: 0; }
.de-paper td, .de-paper th { border: 1px solid #c8c8c8; padding: 6px 8px; vertical-align: top; position: relative; min-width: 2em; }
.de-paper th { background: #f3f3f1; font-weight: 600; text-align: left; }
.de-paper td > p, .de-paper th > p { margin: 0; }
.de-paper .selectedCell::after { content: ""; position: absolute; inset: 0; background: rgba(26,95,180,.12); pointer-events: none; }
.de-paper .column-resize-handle { position: absolute; right: -2px; top: 0; bottom: 0; width: 4px; background: #1a5fb4; pointer-events: none; }
.de-paper.resize-cursor { cursor: col-resize; }
.de-paper p.is-editor-empty:first-child::before { content: attr(data-placeholder); color: #9a9a9a; float: left; height: 0; pointer-events: none; }
.de-paper .ProseMirror-selectednode { outline: none; }
.de-paper .ProseMirror-gapcursor:after { border-top-color: #1d1d1f; }
.de-fig { margin: 1em auto; max-width: 100%; position: relative; }
.de-fig[data-align="left"] { float: left; margin: .3em 1.2em .8em 0; }
.de-fig[data-align="right"] { float: right; margin: .3em 0 .8em 1.2em; }
.de-fig[data-align="center"] { margin-left: auto; margin-right: auto; }
.de-fig-frame { position: relative; line-height: 0; }
.de-fig img { display: block; width: 100%; height: auto; border-radius: 2px; }
.de-fig.sel img { outline: 2px solid #1a5fb4; outline-offset: 2px; }
.de-fig-h { position: absolute; top: 50%; width: 14px; height: 44px; margin-top: -22px; border-radius: 7px; background: #1a5fb4;
  border: 2px solid #fff; box-shadow: 0 1px 3px rgba(0,0,0,.3); cursor: ew-resize; display: none; touch-action: none; }
.de-fig-h.l { left: -9px; } .de-fig-h.r { right: -9px; }
.de-fig.sel .de-fig-h { display: block; }
.de-fig-bar { position: absolute; left: 50%; top: 8px; transform: translateX(-50%); display: none; gap: 2px; padding: 3px; z-index: 2;
  background: #fff; border: 1px solid #d0d0d0; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,.18); line-height: normal; white-space: nowrap; }
.de-fig.sel .de-fig-bar { display: flex; }
.de-fig-bar button { min-width: 30px; height: 30px; border: 0; border-radius: 6px; background: transparent; color: #1d1d1f; cursor: pointer;
  font: 12px system-ui, sans-serif; display: inline-flex; align-items: center; justify-content: center; gap: 4px; padding: 0 6px; }
.de-fig-bar button:hover { background: #eef3fb; } .de-fig-bar button.on { background: #dbe7f8; }
.de-fig-cap { display: block; width: 100%; box-sizing: border-box; border: 0; background: transparent; text-align: center; font: .88em/1.4 system-ui, sans-serif;
  color: #666; margin-top: .4em; padding: 2px 4px; outline: none; }
.de-fig-cap:focus { background: #f5f8fd; }
.de-fig-cap.empty { display: none; } .de-fig.sel .de-fig-cap.empty { display: block; }
.de-paper::after { content: ""; display: block; clear: both; }
.collaboration-cursor__caret { border-left: 1px solid #0d0d0d; border-right: 1px solid #0d0d0d; margin-left: -1px; margin-right: -1px;
  pointer-events: none; position: relative; word-break: normal; }
.collaboration-cursor__label { border-radius: 3px 3px 3px 0; color: #0d0d0d; font: 600 12px/normal system-ui, sans-serif; left: -1px;
  padding: .1rem .3rem; position: absolute; top: -1.4em; user-select: none; white-space: nowrap; }
.de-pop { position: fixed; z-index: 120; background: var(--card); color: var(--fg); border: 1px solid var(--border); border-radius: var(--r-md, 8px);
  box-shadow: var(--shadow-md, 0 6px 20px rgba(0,0,0,.2)); padding: 8px; max-width: calc(100vw - 16px); }
.de-swatches { display: grid; grid-template-columns: repeat(8, 24px); gap: 4px; }
.de-swatch { width: 24px; height: 24px; border-radius: 4px; border: 1px solid rgba(0,0,0,.15); cursor: pointer; padding: 0; }
.de-swatch:focus-visible { outline: 2px solid var(--brand); outline-offset: 1px; }
.de-menu { display: flex; flex-direction: column; min-width: 190px; }
.de-menu button { text-align: left; justify-content: flex-start; width: 100%; }
.de-pop input[type="url"], .de-pop input[type="text"] { height: 32px; border: 1px solid var(--border); border-radius: 6px; padding: 0 8px; background: var(--bg);
  color: var(--fg); font: inherit; font-size: 13px; width: 260px; max-width: 70vw; }
@media (max-width: 700px) {
  .de-scroll { padding: 0; }
  .de-paper { padding: 20px 16px 80px; box-shadow: none; min-height: 100%; max-width: none; }
  .de-paper .ProseMirror { min-height: 60vh; }
  .de-fig[data-align="left"], .de-fig[data-align="right"] { float: none; margin: 1em auto; }
}
`;
  const injectCss = () => {
    if (document.getElementById('de-style')) return;
    const s = document.createElement('style');
    s.id = 'de-style';
    s.textContent = CSS;
    document.head.appendChild(s);
  };

  /* ───────────────────────── extensions ───────────────────────── */
  const clampPct = (v) => {
    const n = parseInt(v, 10);
    return Number.isFinite(n) && n >= 5 && n <= 100 ? n : null;
  };
  const okSrc = (s) => RASTER.test(s || '') || /^https?:\/\//i.test(s || '');

  const buildFontSize = (T) => T.Extension.create({
    name: 'fontSize',
    addOptions() { return { types: ['textStyle'] }; },
    addGlobalAttributes() {
      return [{
        types: this.options.types,
        attributes: {
          fontSize: {
            default: null,
            parseHTML: (el) => {
              const v = (el.style && el.style.fontSize) || '';
              return /^\d{1,3}(\.\d{1,2})?(px|pt|em|rem|%)$/i.test(v) ? v : null;
            },
            renderHTML: (attrs) => (attrs.fontSize ? { style: `font-size: ${attrs.fontSize}` } : {}),
          },
        },
      }];
    },
    addCommands() {
      return {
        setFontSize: (size) => ({ chain }) => chain().setMark('textStyle', { fontSize: size }).run(),
        unsetFontSize: () => ({ chain }) => chain().setMark('textStyle', { fontSize: null }).removeEmptyTextStyle().run(),
      };
    },
  });

  /* A picture: one block node, embedded data URI, with width (percent of
   * the column), alignment and a caption, written as <figure>. */
  const buildFigure = (T, env) => T.Node.create({
    name: 'figure',
    group: 'block',
    atom: true,
    draggable: true,
    selectable: true,
    addAttributes() {
      return {
        src: { default: null },
        alt: { default: '' },
        caption: { default: '' },
        align: { default: 'center' },
        width: { default: null },
      };
    },
    parseHTML() {
      return [
        {
          tag: 'figure',
          getAttrs: (el) => {
            const img = el.querySelector('img');
            const src = img && img.getAttribute('src');
            if (!okSrc(src)) return false;
            const cap = el.querySelector('figcaption');
            const align = el.getAttribute('data-align');
            return {
              src, alt: img.getAttribute('alt') || '',
              caption: cap ? cap.textContent.trim().slice(0, 500) : '',
              align: ['left', 'center', 'right'].includes(align) ? align : 'center',
              width: clampPct(el.getAttribute('data-width')),
            };
          },
        },
        {
          tag: 'img[src]',
          getAttrs: (el) => {
            const src = el.getAttribute('src');
            return okSrc(src) ? { src, alt: el.getAttribute('alt') || '' } : false;
          },
        },
      ];
    },
    renderHTML({ node }) {
      const a = node.attrs;
      const fig = { 'data-align': a.align || 'center' };
      if (a.width) { fig['data-width'] = String(a.width); fig.style = `width: ${a.width}%`; }
      const img = ['img', { src: a.src, alt: a.alt || '' }];
      return a.caption ? ['figure', fig, img, ['figcaption', {}, a.caption]] : ['figure', fig, img];
    },
    addNodeView() {
      return (props) => figureView(props, env);
    },
  });

  const lucideSvg = (name) => {
    // A tiny inline icon for the picture bar (the node view lives outside React).
    const paths = {
      left: '<path d="M21 6H3M15 12H3M17 18H3"/>',
      center: '<path d="M21 6H3M17 12H7M19 18H5"/>',
      right: '<path d="M21 6H3M21 12H9M21 18H7"/>',
      crop: '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/>',
      trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    };
    return `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]}</svg>`;
  };

  const figureView = (props, env) => {
    const { editor, getPos } = props;
    let node = props.node;
    const dom = document.createElement('figure');
    dom.className = 'de-fig';
    const frame = document.createElement('div');
    frame.className = 'de-fig-frame';
    const img = document.createElement('img');
    img.draggable = false;
    frame.appendChild(img);
    const hl = document.createElement('span'); hl.className = 'de-fig-h l';
    const hr = document.createElement('span'); hr.className = 'de-fig-h r';
    const bar = document.createElement('div'); bar.className = 'de-fig-bar';
    const cap = document.createElement('input');
    cap.className = 'de-fig-cap';
    cap.type = 'text';
    cap.placeholder = 'Add a caption';
    cap.setAttribute('aria-label', 'Picture caption');
    cap.maxLength = 500;
    const formatted = !env.markdown;
    const button = (label, icon, onClick, title) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.title = title || label;
      b.setAttribute('aria-label', title || label);
      b.innerHTML = lucideSvg(icon) + (icon === 'crop' ? '<span>Crop &amp; rotate</span>' : '');
      b.addEventListener('mousedown', (e) => e.preventDefault());
      b.addEventListener('click', (e) => { e.preventDefault(); onClick(); });
      bar.appendChild(b);
      return b;
    };
    const setAttrs = (patch) => {
      const pos = typeof getPos === 'function' ? getPos() : null;
      if (pos == null) return;
      const tr = editor.view.state.tr.setNodeMarkup(pos, undefined, Object.assign({}, node.attrs, patch));
      editor.view.dispatch(tr);
    };
    const alignBtns = {};
    if (formatted) {
      alignBtns.left = button('Left', 'left', () => setAttrs({ align: 'left' }), 'Align left, text wraps');
      alignBtns.center = button('Centre', 'center', () => setAttrs({ align: 'center' }), 'Centre');
      alignBtns.right = button('Right', 'right', () => setAttrs({ align: 'right' }), 'Align right, text wraps');
      frame.appendChild(hl);
      frame.appendChild(hr);
    }
    button('Crop & rotate', 'crop', async () => {
      const out = await openCropDialog(node.attrs.src);
      if (out) setAttrs({ src: out });
    }, 'Crop and rotate');
    button('Delete', 'trash', () => {
      const pos = getPos();
      if (pos == null) return;
      editor.view.dispatch(editor.view.state.tr.delete(pos, pos + node.nodeSize));
      editor.commands.focus();
    }, 'Remove picture');
    frame.appendChild(bar);
    dom.appendChild(frame);
    if (formatted) dom.appendChild(cap);

    const render = () => {
      const a = node.attrs;
      if (img.getAttribute('src') !== a.src) img.setAttribute('src', a.src || '');
      img.alt = a.alt || a.caption || '';
      dom.setAttribute('data-align', formatted ? (a.align || 'center') : 'center');
      dom.style.width = formatted && a.width ? `${a.width}%` : '';
      if (document.activeElement !== cap) cap.value = a.caption || '';
      cap.classList.toggle('empty', !(a.caption || ''));
      Object.keys(alignBtns).forEach((k) => alignBtns[k].classList.toggle('on', (a.align || 'center') === k));
      const editable = editor.isEditable;
      bar.style.visibility = editable ? '' : 'hidden';
      cap.readOnly = !editable;
    };
    render();

    // caption: an attribute, typed in place
    let capTimer = null;
    cap.addEventListener('input', () => {
      cap.classList.toggle('empty', !cap.value);
      clearTimeout(capTimer);
      capTimer = setTimeout(() => setAttrs({ caption: cap.value.slice(0, 500) }), 250);
    });
    cap.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === 'Escape') {
        e.preventDefault();
        clearTimeout(capTimer);
        setAttrs({ caption: cap.value.slice(0, 500) });
        // the cursor goes to the line after the picture (one is made when
        // the picture is the last thing in the document)
        const pos = getPos();
        cap.blur();
        editor.view.focus();
        if (pos == null) return;
        const after = pos + node.nodeSize;
        const next = editor.state.doc.nodeAt(after);
        if (next && next.isTextblock) editor.commands.setTextSelection(after + 1);
        else editor.chain().insertContentAt(after, { type: 'paragraph' }).setTextSelection(after + 1).run();
      }
    });
    cap.addEventListener('blur', () => {
      clearTimeout(capTimer);
      if ((node.attrs.caption || '') !== cap.value) setAttrs({ caption: cap.value.slice(0, 500) });
    });

    // resize from either side; a centred picture grows on both sides
    const startResize = (side) => (e) => {
      if (!editor.isEditable) return;
      e.preventDefault();
      e.stopPropagation();
      const column = editor.view.dom.clientWidth || 1;
      const startX = e.clientX;
      const startW = frame.getBoundingClientRect().width;
      const factor = (node.attrs.align || 'center') === 'center' ? 2 : 1;
      let pct = node.attrs.width || Math.round((startW / column) * 100);
      const move = (ev) => {
        const dx = (ev.clientX - startX) * (side === 'r' ? 1 : -1) * factor;
        pct = Math.max(10, Math.min(100, Math.round(((startW + dx) / column) * 100)));
        dom.style.width = `${pct}%`;
      };
      const up = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        setAttrs({ width: pct >= 100 ? null : pct });
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
    };
    hl.addEventListener('pointerdown', startResize('l'));
    hr.addEventListener('pointerdown', startResize('r'));

    return {
      dom,
      update: (next) => {
        if (next.type !== node.type) return false;
        node = next;
        render();
        return true;
      },
      selectNode: () => { dom.classList.add('sel'); render(); },
      deselectNode: () => dom.classList.remove('sel'),
      stopEvent: (e) => {
        const t = e.target;
        return t === cap || bar.contains(t) || t === hl || t === hr;
      },
      ignoreMutation: () => true,
      destroy: () => clearTimeout(capTimer),
    };
  };

  /* ───────────────────────── crop & rotate ───────────────────────── */
  const CropDialog = ({ src, onDone }) => {
    const [rot, setRot] = React.useState(0);
    const [img, setImg] = React.useState(null);
    const [box, setBox] = React.useState({ x: 0, y: 0, w: 1, h: 1 }); // fractions of the rotated picture
    const stageRef = React.useRef(null);
    React.useEffect(() => {
      const i = new Image();
      i.onload = () => setImg(i);
      i.src = src;
    }, [src]);
    const turned = rot % 180 !== 0;
    const iw = img ? (turned ? img.naturalHeight : img.naturalWidth) : 1;
    const ih = img ? (turned ? img.naturalWidth : img.naturalHeight) : 1;
    const previewUrl = React.useMemo(() => {
      if (!img) return '';
      const c = document.createElement('canvas');
      const scale = Math.min(1, 900 / Math.max(iw, ih));
      c.width = Math.round(iw * scale); c.height = Math.round(ih * scale);
      const ctx = c.getContext('2d');
      ctx.translate(c.width / 2, c.height / 2);
      ctx.rotate((rot * Math.PI) / 180);
      const dw = (turned ? c.height : c.width); const dh = (turned ? c.width : c.height);
      ctx.drawImage(img, -dw / 2, -dh / 2, dw, dh);
      return c.toDataURL('image/png');
    }, [img, rot]);

    const drag = (mode) => (e) => {
      e.preventDefault();
      const stage = stageRef.current.getBoundingClientRect();
      const start = { x: e.clientX, y: e.clientY, box: Object.assign({}, box) };
      const move = (ev) => {
        const dx = (ev.clientX - start.x) / stage.width;
        const dy = (ev.clientY - start.y) / stage.height;
        const b = Object.assign({}, start.box);
        const min = 0.05;
        if (mode === 'move') {
          b.x = Math.min(1 - b.w, Math.max(0, b.x + dx));
          b.y = Math.min(1 - b.h, Math.max(0, b.y + dy));
        } else {
          if (mode.includes('l')) { const nx = Math.min(b.x + b.w - min, Math.max(0, b.x + dx)); b.w += b.x - nx; b.x = nx; }
          if (mode.includes('r')) b.w = Math.min(1 - b.x, Math.max(min, b.w + dx));
          if (mode.includes('t')) { const ny = Math.min(b.y + b.h - min, Math.max(0, b.y + dy)); b.h += b.y - ny; b.y = ny; }
          if (mode.includes('b')) b.h = Math.min(1 - b.y, Math.max(min, b.h + dy));
        }
        setBox(b);
      };
      const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
    };
    const apply = () => {
      if (!img) return;
      const full = document.createElement('canvas');
      full.width = iw; full.height = ih;
      const ctx = full.getContext('2d');
      ctx.translate(iw / 2, ih / 2);
      ctx.rotate((rot * Math.PI) / 180);
      ctx.drawImage(img, -img.naturalWidth / 2, -img.naturalHeight / 2);
      const out = document.createElement('canvas');
      out.width = Math.max(1, Math.round(box.w * iw));
      out.height = Math.max(1, Math.round(box.h * ih));
      out.getContext('2d').drawImage(full, Math.round(box.x * iw), Math.round(box.y * ih), out.width, out.height, 0, 0, out.width, out.height);
      onDone(encodeCanvas(out, /^data:image\/(png|webp|gif)/i.test(src)));
    };
    const handle = (mode, style) => (
      <span onPointerDown={drag(mode)}
            style={Object.assign({ position: 'absolute', width: 18, height: 18, background: '#fff', border: '2px solid #1a5fb4',
                                   borderRadius: 4, touchAction: 'none', boxSizing: 'border-box' }, style)}/>
    );
    const pct = (v) => `${v * 100}%`;
    return (
      <div role="dialog" aria-modal="true" aria-label="Crop and rotate"
           style={{ position: 'fixed', inset: 0, zIndex: 130, background: 'rgba(0,0,0,.72)', display: 'flex',
                    flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 16, gap: 14 }}>
        <div ref={stageRef} style={{ position: 'relative', maxWidth: '92vw', maxHeight: '70vh', lineHeight: 0, userSelect: 'none' }}>
          {previewUrl && <img src={previewUrl} alt="" draggable={false}
                              style={{ maxWidth: '92vw', maxHeight: '70vh', display: 'block' }}/>}
          {img && (
            <div onPointerDown={drag('move')}
                 style={{ position: 'absolute', left: pct(box.x), top: pct(box.y), width: pct(box.w), height: pct(box.h),
                          border: '2px solid #fff', boxShadow: '0 0 0 9999px rgba(0,0,0,.5)', cursor: 'move', touchAction: 'none' }}>
              {handle('tl', { left: -9, top: -9, cursor: 'nwse-resize' })}
              {handle('tr', { right: -9, top: -9, cursor: 'nesw-resize' })}
              {handle('bl', { left: -9, bottom: -9, cursor: 'nesw-resize' })}
              {handle('br', { right: -9, bottom: -9, cursor: 'nwse-resize' })}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'center' }}>
          <Button icon="rotate-cw" onClick={() => { setRot((rot + 90) % 360); setBox({ x: 0, y: 0, w: 1, h: 1 }); }}>Rotate</Button>
          <Button icon="undo-2" onClick={() => { setRot(0); setBox({ x: 0, y: 0, w: 1, h: 1 }); }}>Reset</Button>
          <Button onClick={() => onDone(null)}>Cancel</Button>
          <Button variant="primary" icon="check" disabled={!img} onClick={apply}>Apply</Button>
        </div>
      </div>
    );
  };
  const openCropDialog = (src) => new Promise((resolve) => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const root = ReactDOM.createRoot(host);
    const done = (v) => { root.unmount(); host.remove(); resolve(v); };
    root.render(<CropDialog src={src} onDone={done}/>);
  });

  /* ───────────────────────── building the Tiptap editor ───────────────────────── */
  const buildEditor = (T, mount, opts, env) => {
    const collab = opts.collab || null;
    const exts = [
      T.StarterKit.configure({
        history: collab ? false : { depth: 200 },
        heading: { levels: [1, 2, 3, 4] },
        codeBlock: { HTMLAttributes: {} },
        dropcursor: { color: '#1a5fb4', width: 2 },
      }),
      T.Underline,
      T.TextStyle,
      T.Color,
      T.Highlight.configure({ multicolor: true }),
      T.TextAlign.configure({ types: ['heading', 'paragraph'] }),
      T.FontFamily,
      buildFontSize(T),
      T.Link.configure({
        openOnClick: false,
        autolink: true,
        linkOnPaste: true,
        defaultProtocol: 'https',
        protocols: ['mailto', 'tel'],
        HTMLAttributes: { target: '_blank', rel: 'noopener noreferrer nofollow' },
        validate: (href) => /^(https?:|mailto:|tel:|#)/i.test(href),
      }),
      T.Table.configure({ resizable: !env.markdown, lastColumnResizable: true }),
      T.TableRow, T.TableHeader, T.TableCell,
      T.TaskList,
      T.TaskItem.configure({ nested: true }),
      buildFigure(T, env),
      T.Placeholder.configure({ placeholder: opts.placeholder || 'Start typing…' }),
    ];
    if (collab) {
      exts.push(T.Collaboration.configure({ document: collab.ydoc, field: collab.field || 'default' }));
      if (collab.provider && collab.provider.awareness) {
        exts.push(T.CollaborationCursor.configure({
          provider: collab.provider,
          user: collab.user || { name: 'Someone', color: '#1a5fb4' },
        }));
      }
    }
    const editor = new T.Editor({
      element: mount,
      extensions: exts,
      content: collab ? undefined : env.initialHtml,
      editable: env.editable,
      editorProps: {
        attributes: { spellcheck: 'true', 'aria-label': 'Document', role: 'textbox', 'aria-multiline': 'true' },
        transformPastedHTML: (html) => sanitizeDoc(cleanPastedHtml(html)),
        handlePaste: (view, event) => {
          const dt = event.clipboardData;
          if (!dt) return false;
          const html = dt.getData('text/html');
          const files = Array.from(dt.files || []).filter((f) => /^image\//.test(f.type));
          if (files.length && !(html && /<(p|div|span|h\d|li|table)\b/i.test(html))) {
            env.insertPictures(files, null);
            return true;
          }
          return false;
        },
        handleDrop: (view, event, slice, moved) => {
          if (moved) return false;
          const files = Array.from((event.dataTransfer && event.dataTransfer.files) || []).filter((f) => /^image\//.test(f.type));
          if (!files.length) return false;
          event.preventDefault();
          const at = view.posAtCoords({ left: event.clientX, top: event.clientY });
          env.insertPictures(files, at ? at.pos : null);
          return true;
        },
      },
    });
    if (collab && env.initialHtml) {
      // A brand-new shared document: ONE client writes the file's content
      // into it, once; co-editing (collab.js) picks which and when, and
      // calls the seeder after the first sync. Not undoable: undo is the
      // person's own edits, not the file's content.
      const seedNow = () => {
        if (editor.isDestroyed) return;
        const first = editor.state.doc.firstChild;
        const empty = editor.state.doc.childCount <= 1 && editor.state.doc.textContent === ''
          && !(first && /figure|table/.test(first.type.name));
        if (empty) editor.chain().setMeta('addToHistory', false).setContent(env.initialHtml, true).run();
      };
      if (typeof collab.onSeed === 'function') collab.onSeed(() => seedNow());
      else if (collab.seed) {
        seedNow();
        if (typeof collab.markSeeded === 'function') collab.markSeeded();
      }
    }
    return editor;
  };

  /* Pictures someone pasted from a web page arrive as outside addresses;
   * a document never points outside, so each one is fetched and embedded,
   * or removed when the site won't hand it over. */
  const embedOutsidePictures = async (editor, notice) => {
    const wanted = [];
    editor.state.doc.descendants((n, pos) => {
      if (n.type.name === 'figure' && !RASTER.test(n.attrs.src || '')) wanted.push(n.attrs.src);
    });
    if (!wanted.length) return;
    let failed = 0;
    for (const src of Array.from(new Set(wanted))) {
      let replacement = null;
      try {
        const r = await fetch(src, { mode: 'cors', credentials: 'omit', referrerPolicy: 'no-referrer' });
        if (r.ok) replacement = (await pictureFromBlob(await r.blob())).src;
      } catch (e) { replacement = null; }
      const tr = editor.state.tr;
      const hits = [];
      editor.state.doc.descendants((n, pos) => { if (n.type.name === 'figure' && n.attrs.src === src) hits.push([n, pos]); });
      hits.reverse().forEach(([n, pos]) => {
        if (replacement) tr.setNodeMarkup(pos, undefined, Object.assign({}, n.attrs, { src: replacement }));
        else { tr.delete(pos, pos + n.nodeSize); failed += 1; }
      });
      if (hits.length) editor.view.dispatch(tr);
    }
    if (failed && notice) {
      notice(failed === 1
        ? "One pasted picture couldn't be copied from its website. Save it to this device, then use Insert photo."
        : `${failed} pasted pictures couldn't be copied from their websites. Save them to this device, then use Insert photo.`);
    }
  };

  /* ───────────────────────── toolbar ───────────────────────── */
  const Popover = ({ anchor, onClose, children, label }) => {
    const ref = React.useRef(null);
    const [pos, setPos] = React.useState(null);
    React.useLayoutEffect(() => {
      if (!anchor) return;
      const r = anchor.getBoundingClientRect();
      const w = ref.current ? ref.current.offsetWidth : 220;
      const left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left));
      setPos({ left, top: r.bottom + 4 });
    }, [anchor]);
    React.useEffect(() => {
      const down = (e) => {
        if (ref.current && !ref.current.contains(e.target) && !(anchor && anchor.contains(e.target))) onClose();
      };
      const key = (e) => { if (e.key === 'Escape') onClose(); };
      document.addEventListener('pointerdown', down, true);
      document.addEventListener('keydown', key);
      return () => { document.removeEventListener('pointerdown', down, true); document.removeEventListener('keydown', key); };
    }, [anchor, onClose]);
    return (
      <div ref={ref} className="de-pop" role="dialog" aria-label={label}
           style={pos ? { left: pos.left, top: pos.top } : { visibility: 'hidden', left: 0, top: 0 }}>
        {children}
      </div>
    );
  };

  const TB = ({ icon, label, on, disabled, onClick, children, title, btnRef, className }) => (
    <button type="button" ref={btnRef} className={`de-btn${on ? ' on' : ''}${className ? ` ${className}` : ''}`}
            title={title || label} aria-label={label} aria-pressed={on === undefined ? undefined : !!on}
            disabled={disabled} onMouseDown={(e) => e.preventDefault()} onClick={onClick}>
      {icon && <Icon name={icon} size={16}/>}
      {children}
    </button>
  );

  const NEEDS_FORMATTED = 'Save as a formatted document to use fonts';

  const Toolbar = ({ editor, env, fonts, registry }) => {
    const [, setTick] = React.useState(0);
    const [pop, setPop] = React.useState(null); // {kind, anchor}
    const [linkDraft, setLinkDraft] = React.useState('');
    const fileRef = React.useRef(null);
    React.useEffect(() => {
      let raf = 0;
      const bump = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => setTick((t) => t + 1)); };
      editor.on('transaction', bump);
      editor.on('selectionUpdate', bump);
      return () => { cancelAnimationFrame(raf); editor.off('transaction', bump); editor.off('selectionUpdate', bump); };
    }, [editor]);
    const md = env.markdown;
    const c = () => editor.chain().focus();
    const active = (n, a) => editor.isActive(n, a);
    const close = React.useCallback(() => setPop(null), []);
    const open = (kind) => (e) => {
      const anchor = e.currentTarget;
      setPop((p) => (p && p.kind === kind ? null : { kind, anchor }));
      if (kind === 'link') setLinkDraft(editor.getAttributes('link').href || '');
    };

    const block = active('heading', { level: 1 }) ? 'h1' : active('heading', { level: 2 }) ? 'h2'
      : active('heading', { level: 3 }) ? 'h3' : active('heading', { level: 4 }) ? 'h4' : 'p';
    const setBlock = (v) => {
      if (v === 'p') c().setParagraph().run();
      else c().setHeading({ level: Number(v.slice(1)) }).run();
    };
    const ts = editor.getAttributes('textStyle');
    const curFamily = ts.fontFamily || '';
    const curFont = fonts.find((f) => fontCss(f) === curFamily || f.family === firstFamily(curFamily));
    const setFont = (family) => {
      if (!family) { c().unsetFontFamily().run(); return; }
      const f = fonts.find((x) => x.family === family) || (registry ? { family, category: 'sans' } : null);
      if (!f) return;
      ensureFont(registry, f.family);
      c().setFontFamily(fontCss(f)).run();
    };
    const curSize = (() => {
      const m = /^([\d.]+)pt$/.exec(ts.fontSize || '');
      if (m) return Math.round(parseFloat(m[1]));
      const px = /^([\d.]+)px$/.exec(ts.fontSize || '');
      return px ? Math.round(parseFloat(px[1]) * 0.75) : 12;
    })();
    const setSize = (n) => {
      const v = Math.max(6, Math.min(400, n));
      if (v === 12) c().unsetFontSize().run(); else c().setFontSize(`${v}pt`).run();
    };
    const stepSize = (dir) => {
      const i = FONT_SIZES.findIndex((s) => s >= curSize);
      const next = dir > 0 ? (FONT_SIZES[(FONT_SIZES[i] === curSize ? i + 1 : i)] || curSize + 2)
        : (FONT_SIZES[i - 1] || Math.max(6, curSize - 1));
      setSize(next);
    };
    const align = ['left', 'center', 'right', 'justify'].find((a) => editor.isActive({ textAlign: a })) || 'left';
    const colour = ts.color || '#000000';
    const hl = editor.getAttributes('highlight').color || null;
    const inTable = active('table');
    const canUndo = editor.can().undo ? editor.can().undo() : true;
    const canRedo = editor.can().redo ? editor.can().redo() : true;

    const applyLink = () => {
      let href = linkDraft.trim();
      if (!href) { c().extendMarkRange('link').unsetLink().run(); close(); return; }
      if (!/^[a-z][a-z0-9+.-]*:/i.test(href) && !href.startsWith('#')) {
        href = /^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(href) ? `mailto:${href}` : `https://${href}`;
      }
      if (!/^(https?:|mailto:|tel:|#)/i.test(href)) { env.notice && env.notice("That address can't be a link here."); return; }
      if (editor.state.selection.empty && !active('link')) {
        c().insertContent({ type: 'text', text: href, marks: [{ type: 'link', attrs: { href } }] }).run();
      } else {
        c().extendMarkRange('link').setLink({ href }).run();
      }
      close();
    };

    const pickFiles = (e) => {
      const files = Array.from(e.target.files || []);
      e.target.value = '';
      if (files.length) env.insertPictures(files, null);
    };

    const sw = (list, onPick, current) => (
      <div className="de-swatches" role="listbox">
        {list.map((col) => (
          <button key={col} type="button" className="de-swatch" title={col} aria-label={col}
                  aria-selected={current && current.toLowerCase() === col}
                  style={{ background: col, outline: current && current.toLowerCase() === col ? '2px solid var(--brand)' : undefined }}
                  onMouseDown={(e) => e.preventDefault()} onClick={() => { onPick(col); close(); }}/>
        ))}
      </div>
    );

    return (
      <div className="de-toolbar" role="toolbar" aria-label="Formatting">
        <TB icon="undo-2" label="Undo" title="Undo (Ctrl+Z)" disabled={!canUndo} onClick={() => c().undo().run()}/>
        <TB icon="redo-2" label="Redo" title="Redo (Ctrl+Y)" disabled={!canRedo} onClick={() => c().redo().run()}/>
        <span className="de-sep"/>
        <select className="de-select" aria-label="Text style" value={block} onChange={(e) => setBlock(e.target.value)}>
          <option value="p">Normal text</option>
          <option value="h1">Title</option>
          <option value="h2">Heading</option>
          <option value="h3">Subheading</option>
          <option value="h4">Small heading</option>
        </select>
        {window.DomovoiFontPicker ? (
          // builder C's picker: every family previewed in its own face, the
          // families this document names but the house lacks listed first
          <window.DomovoiFontPicker value={curFont ? curFont.family : firstFamily(curFamily)}
                                   onChange={(family) => setFont(family)} disabled={md} disabledNote={NEEDS_FORMATTED}
                                   documentFamilies={env.documentFamilies ? env.documentFamilies() : []}/>
        ) : (
        <select className="de-select" aria-label="Font" disabled={md} title={md ? NEEDS_FORMATTED : 'Font'}
                value={curFont ? curFont.family : ''} onChange={(e) => setFont(e.target.value)}>
          <option value="">Default font</option>
          {fonts.map((f) => <option key={f.family} value={f.family}>{f.label}</option>)}
          {curFamily && !curFont && <option value={firstFamily(curFamily)} disabled>{firstFamily(curFamily)}</option>}
        </select>
        )}
        <TB icon="minus" label="Smaller text" disabled={md} title={md ? NEEDS_FORMATTED : 'Smaller'} onClick={() => stepSize(-1)}/>
        <select className="de-select" aria-label="Text size" disabled={md} title={md ? NEEDS_FORMATTED : 'Text size'}
                style={{ width: 62 }} value={String(curSize)} onChange={(e) => setSize(Number(e.target.value))}>
          {(FONT_SIZES.includes(curSize) ? FONT_SIZES : FONT_SIZES.concat([curSize]).sort((a, b) => a - b))
            .map((s) => <option key={s} value={String(s)}>{s}</option>)}
        </select>
        <TB icon="plus" label="Larger text" disabled={md} title={md ? NEEDS_FORMATTED : 'Larger'} onClick={() => stepSize(1)}/>
        <span className="de-sep"/>
        <TB icon="bold" label="Bold" title="Bold (Ctrl+B)" on={active('bold')} onClick={() => c().toggleBold().run()}/>
        <TB icon="italic" label="Italic" title="Italic (Ctrl+I)" on={active('italic')} onClick={() => c().toggleItalic().run()}/>
        <TB icon="underline" label="Underline" title="Underline (Ctrl+U)" on={active('underline')} onClick={() => c().toggleUnderline().run()}/>
        <TB icon="strikethrough" label="Strikethrough" on={active('strike')} onClick={() => c().toggleStrike().run()}/>
        <TB label="Text colour" className="de-colour-btn" disabled={md} title={md ? NEEDS_FORMATTED : 'Text colour'}
            onClick={open('colour')}>
          <Icon name="baseline" size={16}/><span className="de-swatch-under" style={{ background: colour }}/>
        </TB>
        <TB label="Highlight" className="de-colour-btn" disabled={md} title={md ? NEEDS_FORMATTED : 'Highlight'}
            on={!!active('highlight')} onClick={open('highlight')}>
          <Icon name="highlighter" size={16}/><span className="de-swatch-under" style={{ background: hl || 'transparent' }}/>
        </TB>
        <TB icon="link" label="Link" title="Link (Ctrl+K)" on={active('link')} onClick={open('link')}/>
        <span className="de-sep"/>
        {['left', 'center', 'right', 'justify'].map((a) => (
          <TB key={a} icon={`align-${a}`} label={`Align ${a === 'center' ? 'centre' : a}`}
              title={md ? NEEDS_FORMATTED : `Align ${a === 'center' ? 'centre' : a}`} disabled={md}
              on={!md && align === a} onClick={() => c().setTextAlign(a).run()}/>
        ))}
        <span className="de-sep"/>
        <TB icon="list" label="Bulleted list" on={active('bulletList')} onClick={() => c().toggleBulletList().run()}/>
        <TB icon="list-ordered" label="Numbered list" on={active('orderedList')} onClick={() => c().toggleOrderedList().run()}/>
        <TB icon="list-checks" label="Checklist" on={active('taskList')} onClick={() => c().toggleTaskList().run()}/>
        <TB icon="text-quote" label="Quote" on={active('blockquote')} onClick={() => c().toggleBlockquote().run()}/>
        <TB icon="code" label="Code" title="Code (inline, or a block when nothing is selected)"
            on={active('code') || active('codeBlock')}
            onClick={() => (editor.state.selection.empty ? c().toggleCodeBlock().run() : c().toggleCode().run())}/>
        <span className="de-sep"/>
        <TB icon="table" label="Table" on={inTable} onClick={open('table')}/>
        <TB icon="image-plus" label="Insert photo" title="Insert photo" onClick={() => fileRef.current && fileRef.current.click()}/>
        <input ref={fileRef} type="file" accept="image/*" multiple hidden onChange={pickFiles}/>
        <TB icon="remove-formatting" label="Clear formatting" onClick={() => c().unsetAllMarks().clearNodes().run()}/>

        {pop && pop.kind === 'colour' && (
          <Popover anchor={pop.anchor} onClose={close} label="Text colour">
            {sw(TEXT_COLOURS, (col) => c().setColor(col).run(), ts.color)}
            <div style={{ marginTop: 8 }}>
              <Button icon="eraser" onClick={() => { c().unsetColor().run(); close(); }}>Default colour</Button>
            </div>
          </Popover>
        )}
        {pop && pop.kind === 'highlight' && (
          <Popover anchor={pop.anchor} onClose={close} label="Highlight">
            {sw(HIGHLIGHTS, (col) => c().setHighlight({ color: col }).run(), hl)}
            <div style={{ marginTop: 8 }}>
              <Button icon="eraser" onClick={() => { c().unsetHighlight().run(); close(); }}>No highlight</Button>
            </div>
          </Popover>
        )}
        {pop && pop.kind === 'link' && (
          <Popover anchor={pop.anchor} onClose={close} label="Link">
            <form onSubmit={(e) => { e.preventDefault(); applyLink(); }} style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              <input type="url" inputMode="url" autoFocus placeholder="Paste or type a web address"
                     aria-label="Web address" value={linkDraft} onChange={(e) => setLinkDraft(e.target.value)}/>
              <Button variant="primary" type="submit">Apply</Button>
              {active('link') && <Button icon="unlink" type="button"
                                         onClick={() => { c().extendMarkRange('link').unsetLink().run(); close(); }}>Remove</Button>}
            </form>
          </Popover>
        )}
        {pop && pop.kind === 'table' && (
          <Popover anchor={pop.anchor} onClose={close} label="Table">
            <div className="de-menu">
              {!inTable && <TB icon="table" label="Insert a table (3 × 3)"
                               onClick={() => { c().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(); close(); }}>Insert a table</TB>}
              {inTable && [
                ['between-horizontal-start', 'Add row above', () => c().addRowBefore().run()],
                ['between-horizontal-start', 'Add row below', () => c().addRowAfter().run()],
                ['between-vertical-start', 'Add column left', () => c().addColumnBefore().run()],
                ['between-vertical-start', 'Add column right', () => c().addColumnAfter().run()],
                ['minus', 'Delete row', () => c().deleteRow().run()],
                ['minus', 'Delete column', () => c().deleteColumn().run()],
                ['table', 'Header row on/off', () => c().toggleHeaderRow().run()],
                ['table', 'Merge or split cells', () => c().mergeOrSplit().run()],
                ['trash-2', 'Delete table', () => c().deleteTable().run()],
              ].map(([icon, label, run]) => (
                <TB key={label} icon={icon} label={label} onClick={() => { run(); close(); }}>{label}</TB>
              ))}
            </div>
          </Popover>
        )}
      </div>
    );
  };

  /* ───────────────────────── the shell create() mounts ───────────────────────── */
  const Shell = ({ opts, bridge }) => {
    const mountRef = React.useRef(null);
    const [editor, setEditor] = React.useState(null);
    const [error, setError] = React.useState(null);
    const [registry, setRegistry] = React.useState(opts.fonts || window.DomovoiFonts || null);
    const [fonts, setFonts] = React.useState(BUILTIN_FONTS);
    const [editable, setEditable] = React.useState(false);
    const markdown = opts.format === 'md';
    const coll = opts.collab || null;
    // Editable unless the caller said read-only, and, when co-editing, only
    // once the shared document holds its content (collab.ready) and while
    // this session may write (collab.readonly is a view-only session).
    const canEdit = () => !bridge.readOnly && !(coll && (coll.readonly || coll.ready === false));

    React.useEffect(() => { bridge.setFonts = (r) => setRegistry(r || null); }, []);
    React.useEffect(() => {
      let live = true;
      activeRegistry = registry;
      const refresh = () => fontsFrom(registry).then((list) => { if (live) setFonts(list); });
      refresh();
      // fonts.jsx tells its listeners when a family is added for the house
      let off = null;
      if (registry && typeof registry.onChange === 'function') {
        try { off = registry.onChange(() => refresh()); } catch (e) { off = null; }
      }
      return () => { live = false; if (typeof off === 'function') off(); };
    }, [registry]);

    React.useEffect(() => {
      let ed = null;
      let dead = false;
      (async () => {
        try {
          const T = await load();
          if (dead) return;
          let initialHtml = '';
          if (opts.format === 'md') initialHtml = sanitizeDoc(markdownToHtml(T, opts.content));
          else if (opts.format === 'docx' && opts.content instanceof ArrayBuffer) initialHtml = await docxToHtml(opts.content);
          else initialHtml = sanitizeDoc(opts.content);
          if (dead) return;
          const env = { markdown, initialHtml, notice: opts.onNotice || null, editable: canEdit() };
          env.insertPictures = (files, at) => bridge.insertPictures(files, at);
          ed = buildEditor(T, mountRef.current, opts, env);
          bridge.serializer = buildMarkdownSerializer(T);
          bridge.T = T;
          ed.on('update', ({ transaction }) => {
            if (transaction && (transaction.getMeta('paste') || transaction.getMeta('uiEvent') === 'paste'
                || transaction.getMeta('uiEvent') === 'drop')) {
              embedOutsidePictures(ed, env.notice);
            }
            // Only a real edit is a change (Yjs updates from others count:
            // they change the document too, and B decides who saves).
            if (transaction && !transaction.docChanged) return;
            if (opts.onChange) {
              opts.onChange({
                get html() { return bridge.api.getHTML(); },
                get markdown() { return bridge.api.getMarkdown(); },
                editor: bridge.api,
              });
            }
          });
          // Fonts the document already uses: ask the registry for each.
          const used = new Set();
          ed.state.doc.descendants((n) => n.marks.forEach((m) => {
            if (m.type.name === 'textStyle' && m.attrs.fontFamily) used.add(firstFamily(m.attrs.fontFamily));
          }));
          used.forEach((f) => ensureFont(registry, f));
          embedOutsidePictures(ed, env.notice);
          bridge.editor = ed;
          // (Tiptap's setEditable emits an "update" unless told not to;
          // opening a document must never look like an edit, or it would
          // be saved unread.)
          bridge.applyEditable = () => {
            if (!ed || ed.isDestroyed) return;
            const want = canEdit();
            if (ed.isEditable !== want) ed.setEditable(want, false);
            setEditable(want);
          };
          bridge.applyEditable();
          if (coll) {
            if (coll.whenReady && coll.whenReady.then) coll.whenReady.then(() => bridge.applyEditable());
            if (typeof coll.on === 'function') {
              ['hello', 'status', 'ready'].forEach((evt) => coll.on(evt, () => bridge.applyEditable()));
            }
          }
          setEditor(ed);
          bridge.resolve();
        } catch (e) {
          if (!dead) { setError(e.message || String(e)); bridge.reject(e); }
        }
      })();
      return () => { dead = true; if (ed) ed.destroy(); bridge.editor = null; };
    }, []);

    return (
      <div className="de-shell">
        {editor && editable && <Toolbar editor={editor} env={{ markdown, insertPictures: (f, at) => bridge.insertPictures(f, at), notice: opts.onNotice,
                                                              documentFamilies: () => documentFamilies(editor) }}
                                              fonts={fonts} registry={registry}/>}
        {markdown && editable && editor && (
          <div className="de-note">
            <Icon name="info" size={14}/>
            <span>This is a Markdown file. Fonts, colours, sizes and alignment can't be kept in it: save as a formatted document to use fonts.</span>
            {opts.onSaveAsFormatted && <Button icon="file-text" onClick={opts.onSaveAsFormatted}>Save as formatted document</Button>}
          </div>
        )}
        {error && <div style={{ padding: 20, color: 'var(--err-ink)', fontSize: 13 }}>The editor couldn't start: {error}</div>}
        {!editor && !error && <div style={{ padding: 20, color: 'var(--fg-faint)', fontSize: 13 }}>Opening…</div>}
        <div className="de-scroll" onMouseDown={(e) => {
          // a press on the grey around the page puts the cursor in the page
          if (editor && e.target === e.currentTarget) { e.preventDefault(); editor.commands.focus('end'); }
        }}>
          <div className="de-paper" style={{ display: editor ? undefined : 'none' }}>
            <div ref={mountRef}/>
          </div>
        </div>
      </div>
    );
  };

  /* Word → the editor's HTML (mammoth, in this browser). Pictures come
   * through the same downscaler as pasted ones. */
  const docxToHtml = async (arrayBuffer) => {
    const mammoth = await loadMammoth();
    const result = await mammoth.convertToHtml({ arrayBuffer }, {
      styleMap: ['u => u', 'strike => s', "p[style-name='Title'] => h1:fresh", "p[style-name='Subtitle'] => h2:fresh",
        "p[style-name='Quote'] => blockquote > p:fresh", "p[style-name='Intense Quote'] => blockquote > p:fresh"],
      convertImage: mammoth.images.imgElement(async (image) => {
        const b64 = await image.read('base64');
        const raw = `data:${image.contentType};base64,${b64}`;
        try {
          return { src: (await pictureFromBlob(await dataUrlToBlob(raw))).src };
        } catch (e) {
          return { src: RASTER.test(raw) ? raw : '' };
        }
      }),
    });
    return sanitizeDoc(unwrapLonePictures(result.value));
  };

  const create = (el, options) => {
    const opts = Object.assign({ format: 'html', content: '' }, options || {});
    injectCss();
    let resolve; let reject;
    const ready = new Promise((res, rej) => { resolve = res; reject = rej; });
    ready.catch(() => {});
    const bridge = { editor: null, serializer: null, readOnly: !!opts.readOnly,
                     resolve: () => resolve(api), reject: (e) => reject(e) };
    const initial = typeof opts.content === 'string' ? opts.content : '';
    const api = {
      format: opts.format,
      ready,
      get tiptap() { return bridge.editor; },
      getHTML: () => (bridge.editor ? bridge.editor.getHTML() : initial),
      getJSON: () => (bridge.editor ? bridge.editor.getJSON() : null),
      getMarkdown: () => {
        if (!bridge.editor || !bridge.serializer) return opts.format === 'md' ? initial : '';
        return bridge.serializer.serialize(bridge.editor.state.doc).replace(/\n{3,}/g, '\n\n').trimEnd() + '\n';
      },
      setContent: (html) => { if (bridge.editor) bridge.editor.commands.setContent(sanitizeDoc(html), true); },
      isEmpty: () => (bridge.editor ? bridge.editor.isEmpty : !initial),
      focus: () => { if (bridge.editor) bridge.editor.commands.focus(); },
      setFonts: (registry) => { if (bridge.setFonts) bridge.setFonts(registry); },
      setReadOnly: (v) => { bridge.readOnly = !!v; if (bridge.applyEditable) bridge.applyEditable(); },
      destroy: () => { try { root.unmount(); } catch (e) { /* already gone */ } },
    };
    bridge.api = api;
    bridge.insertPictures = (files, at) => {
      if (!bridge.editor) return;
      // the same path the editor's own paste/drop use
      const ed = bridge.editor;
      (async () => {
        for (const f of files) {
          try {
            const pic = await pictureFromBlob(f);
            const node = { type: 'figure', attrs: { src: pic.src, alt: (f.name || '').replace(/\.[a-z0-9]+$/i, '').slice(0, 120) } };
            // a selected picture is not replaced by the next one: it goes after it
            const sel = ed.state.selection;
            const where = at != null ? at : (sel.node ? sel.to : null);
            if (where != null) ed.chain().focus().insertContentAt(where, node).run();
            else ed.chain().focus().insertContent(node).run();
          } catch (e) {
            if (opts.onNotice) opts.onNotice(e.message || "That picture couldn't be added.");
          }
        }
      })();
    };
    const root = ReactDOM.createRoot(el);
    root.render(<Shell opts={opts} bridge={bridge}/>);
    return api;
  };

  /* The page CSS a printed / "Save as PDF" document gets: the same block
   * the saved file carries (domovoi/richdoc/fileformat.py STYLE; a test
   * keeps the two the same). */
  const PRINT_CSS = `:root { color-scheme: light; }
html { background: #f3f3f1; }
body { margin: 0; padding: 32px 16px; font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; color: #1d1d1f; }
.doc { box-sizing: border-box; max-width: 816px; margin: 0 auto; padding: 72px 80px; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.12); min-height: 1056px; overflow-wrap: break-word; }
.doc > :first-child { margin-top: 0; }
h1 { font-size: 2em; line-height: 1.25; margin: .8em 0 .4em; }
h2 { font-size: 1.5em; line-height: 1.3; margin: .9em 0 .4em; }
h3 { font-size: 1.25em; margin: 1em 0 .4em; }
h4, h5, h6 { font-size: 1em; margin: 1em 0 .4em; }
p { margin: 0 0 .7em; }
a { color: #1a5fb4; }
blockquote { margin: 1em 0; padding: .2em 0 .2em 1em; border-left: 3px solid #c8c8c8; color: #555; }
pre { background: #f5f5f4; border-radius: 6px; padding: 12px 14px; overflow-x: auto; font: 14px/1.5 ui-monospace, "JetBrains Mono", Consolas, monospace; }
code { font-family: ui-monospace, "JetBrains Mono", Consolas, monospace; font-size: .92em; background: #f1f1ef; border-radius: 4px; padding: .1em .3em; }
pre code { background: none; padding: 0; font-size: inherit; }
hr { border: 0; border-top: 1px solid #d0d0d0; margin: 1.5em 0; }
mark { background: #fff3a3; color: inherit; padding: 0 .1em; }
ul, ol { padding-left: 1.6em; margin: 0 0 .7em; }
li > p { margin: 0 0 .2em; }
ul[data-type="taskList"] { list-style: none; padding-left: .2em; }
ul[data-type="taskList"] li { display: flex; gap: .5em; align-items: flex-start; }
ul[data-type="taskList"] li > label { flex: 0 0 auto; margin-top: .2em; }
ul[data-type="taskList"] li > div { flex: 1 1 auto; }
ul[data-type="taskList"] li[data-checked="true"] > div { color: #777; text-decoration: line-through; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; table-layout: fixed; }
td, th { border: 1px solid #c8c8c8; padding: 6px 8px; vertical-align: top; }
th { background: #f3f3f1; font-weight: 600; text-align: left; }
td > p, th > p { margin: 0; }
figure { margin: 1em auto; max-width: 100%; }
figure img { display: block; width: 100%; height: auto; }
figure[data-align="left"] { float: left; margin: .3em 1.2em .8em 0; }
figure[data-align="right"] { float: right; margin: .3em 0 .8em 1.2em; }
figure[data-align="center"] { margin-left: auto; margin-right: auto; }
figcaption { font-size: .88em; color: #666; text-align: center; margin-top: .4em; }
img { max-width: 100%; height: auto; }
.doc::after { content: ""; display: block; clear: both; }
@media (max-width: 700px) { body { padding: 0; background: #fff; } .doc { padding: 24px 16px; box-shadow: none; min-height: 0; }
  figure[data-align="left"], figure[data-align="right"] { float: none; margin: 1em auto; } }
@media print { html, body { background: #fff; padding: 0; } .doc { box-shadow: none; padding: 0; max-width: none; min-height: 0; } }
@page { margin: 2cm; }`;

  const escapeHtml = (s) => String(s || '').replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[ch]);

  /* The font faces this page has loaded (bundled and fetched fonts), so a
   * printed document uses the same type it shows on screen. */
  const fontFaceCss = () => {
    const out = [];
    Array.from(document.styleSheets).forEach((sheet) => {
      let rules = null;
      try { rules = sheet.cssRules; } catch (e) { rules = null; }
      if (!rules) return;
      Array.from(rules).forEach((r) => {
        if (r.type === 5 /* CSSFontFaceRule */) out.push(r.cssText.replace(/url\((['"]?)(?!data:|https?:|\/)/g, (m, q) => `url(${q}${new URL('.', sheet.href || location.href).href}`));
      });
    });
    return out.join('\n');
  };

  const printablePage = (bodyHtml, title) => `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>${escapeHtml(title)}</title><style>${fontFaceCss()}\n${PRINT_CSS}</style></head>
<body><main class="doc">${bodyHtml}</main></body></html>`;

  /* "PDF": the browser's own print dialog, where "Save as PDF" is one of
   * the printers — the best-looking PDF there is, with the page's fonts and
   * pictures, and nothing to install. The phone app hands the same page to
   * Android's print service (DomovoiApp.print), which has Save as PDF too. */
  const printDocument = (bodyHtml, title) => {
    const page = printablePage(bodyHtml, title);
    if (window.DomovoiApp && typeof window.DomovoiApp.print === 'function') {
      window.DomovoiApp.print(page, title || 'Document');
      return;
    }
    const frame = document.createElement('iframe');
    frame.setAttribute('aria-hidden', 'true');
    frame.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden';
    document.body.appendChild(frame);
    const doc = frame.contentDocument;
    doc.open(); doc.write(page); doc.close();
    const go = () => {
      try { frame.contentWindow.focus(); frame.contentWindow.print(); } finally {
        setTimeout(() => frame.remove(), 60000);
      }
    };
    const imgs = Array.from(doc.images);
    Promise.all(imgs.map((i) => (i.complete ? null : new Promise((r) => { i.onload = r; i.onerror = r; }))))
      .then(() => (doc.fonts && doc.fonts.ready) || null).then(() => setTimeout(go, 50));
  };

  /* Hand a file to the person: the phone app's share sheet when inside the
   * app, a download in a browser. */
  const deliverFile = async (name, mime, blob) => {
    if (window.DomovoiApp && typeof window.DomovoiApp.saveFile === 'function') {
      const b64 = await new Promise((res, rej) => {
        const fr = new FileReader();
        fr.onload = () => res(String(fr.result).split(',')[1] || '');
        fr.onerror = () => rej(fr.error);
        fr.readAsDataURL(blob);
      });
      window.DomovoiApp.saveFile(name, mime, b64);
      return;
    }
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  };

  window.DomovoiDocEditor = {
    create, load, loadYjs, loadMammoth,
    // for the overlay below, the app page and B's co-editing:
    markdownToHtml: (md) => load().then((T) => sanitizeDoc(markdownToHtml(T, md))),
    docxToHtml,
    printDocument,
    printablePage,
    deliverFile,
    PRINT_CSS,
  };

  /* ═════════════════════ the full-screen surface ═════════════════════ */

  const FORMAT_WORDS = { md: 'Markdown', docx: 'Word' };
  const extOf = (p) => { const m = /\.[^./\\]+$/.exec(p || ''); return m ? m[0].toLowerCase() : ''; };
  const baseName = (p) => String(p || '').split('/').pop().replace(/\.[^.]+$/, '');
  const AUTOSAVE_MS = 1200;

  const SaveState = ({ state }) => {
    const map = {
      saved: { icon: 'cloud', text: 'Saved', tone: 'var(--fg-muted)' },
      saving: { icon: 'loader-circle', text: 'Saving…', tone: 'var(--fg-muted)' },
      dirty: { icon: 'pencil', text: 'Unsaved changes', tone: 'var(--warn-ink, var(--fg-muted))' },
      error: { icon: 'cloud-off', text: 'Not saved', tone: 'var(--err-ink)' },
      choose: { icon: 'pencil', text: 'Not saved yet', tone: 'var(--warn-ink, var(--fg-muted))' },
    };
    const s = map[state] || map.saved;
    return (
      <span role="status" aria-live="polite" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: s.tone, whiteSpace: 'nowrap' }}>
        <Icon name={s.icon} size={13}/><span className="de-state-text">{s.text}</span>
      </span>
    );
  };

  const DocEditorOverlay = ({ rel_path, onClose, fire, blockedReason = null, embedded = false, closeRef = null }) => {
    const [rel, setRel] = React.useState(rel_path);
    const [state, setState] = React.useState({ status: 'loading' });
    const [save, setSave] = React.useState('saved');
    const [saveError, setSaveError] = React.useState('');
    const [refused, setRefused] = React.useState(null);
    const [docxChoice, setDocxChoice] = React.useState(null); // null | 'docx'
    const [askDocx, setAskDocx] = React.useState(false);
    const [menu, setMenu] = React.useState(null);
    const [collabOn, setCollabOn] = React.useState(false);
    const [history, setHistory] = React.useState(false);
    const [reload, setReload] = React.useState(0);
    const hostRef = React.useRef(null);
    const edRef = React.useRef(null);
    const collabRef = React.useRef(null);
    const seq = React.useRef({ change: 0, saved: 0, timer: 0, inflight: null });
    const blocked = refused || blockedReason;
    const fmt = state.doc ? state.doc.format : null;
    const notice = React.useCallback((m) => { if (fire) fire(m); }, [fire]);

    // ── load ──
    React.useEffect(() => {
      let dead = false;
      setState({ status: 'loading' });
      setSave('saved');
      (async () => {
        try {
          const doc = await apiFetch(`/api/documents/doc/${encodeURIComponent(rel)}`);
          let content = doc.format === 'md' ? doc.markdown : (doc.html || '');
          if (doc.format === 'docx') {
            const r = await fetch(withDeviceToken(`${API_BASE}/api/documents/raw/${encodeURIComponent(rel)}`),
              { credentials: 'include', cache: 'no-store', headers: apiHeaders() });
            if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
            content = await r.arrayBuffer();
          }
          let collab = null;
          if (window.DomovoiCollab && typeof window.DomovoiCollab.open === 'function' && doc.format !== 'docx') {
            try {
              await loadYjs();
              collab = await window.DomovoiCollab.open(rel, { format: doc.format });
            } catch (e) {
              collab = null;
              notice("Live co-editing isn't available right now; you're editing on your own.");
            }
          }
          let viewOnly = false;
          if (collab && collab.readonly && collab.ready === false) {
            // A view-only session on a document nobody who can write has
            // open: the shared copy is empty, so show the file itself.
            try { collab.close(); } catch (e) { /* gone */ }
            collab = null;
            viewOnly = true;
          }
          if (dead) { if (collab && collab.close) collab.close(); return; }
          if (collab && typeof collab.on === 'function') {
            // The file was changed outside the editor and the shared copy
            // was rebuilt from it: open it again.
            collab.on('reset', () => { notice('This document was changed elsewhere; it has been reopened.'); setReload((n) => n + 1); });
          }
          collabRef.current = collab;
          setCollabOn(!!collab);
          setState({ status: 'ready', doc, content, viewOnly });
        } catch (e) {
          if (!dead) setState({ status: 'error', message: apiErrorTextSafe(e) });
        }
      })();
      return () => {
        dead = true;
        const c = collabRef.current;
        collabRef.current = null;
        if (c && typeof c.close === 'function') { try { c.close(); } catch (e) { /* gone */ } }
      };
    }, [rel, reload]);

    // ── save ──
    // With co-editing, the co-editing client decides when the file is
    // written (whoever edited last, once typing stops, and on close) and
    // calls this editor's saver; without it, this overlay autosaves.
    const coSaves = () => !!(collabRef.current && typeof collabRef.current.setSaver === 'function');
    const clientSaves = () => !coSaves();
    const doSave = React.useCallback(async (opts) => {
      const ed = edRef.current;
      const d = state.doc;
      if (!ed || !d) return false;
      if (d.format === 'docx' && docxChoice !== 'docx' && !(opts && opts.asWord)) { setAskDocx(true); setSave('choose'); return false; }
      if (blocked) return false;
      const s = seq.current;
      const target = s.change;
      if (s.inflight) { await s.inflight; if (s.saved >= target) return true; }
      setSave('saving');
      const body = { html: ed.getHTML() };
      if (d.format === 'md') body.markdown = ed.getMarkdown();
      s.inflight = (async () => {
        try {
          await apiFetch(`/api/documents/doc/${encodeURIComponent(rel)}`, { method: 'PUT', body: JSON.stringify(body) });
          s.saved = Math.max(s.saved, target);
          setSaveError('');
          setRefused(null);
          setSave(s.saved >= s.change ? 'saved' : 'dirty');
          return true;
        } catch (e) {
          const why = deviceBlockReason(e);
          if (why) { setRefused(why); setSave('error'); return false; }
          const msg = mutationErrorText(e);
          setSaveError(msg || '');
          setSave('error');
          return false;
        } finally { s.inflight = null; }
      })();
      return s.inflight;
    }, [state.doc, rel, docxChoice, blocked]);
    const saveRef = React.useRef(doSave);
    saveRef.current = doSave;

    const onChange = React.useCallback(() => {
      const s = seq.current;
      s.change += 1;
      setSave((cur) => (cur === 'choose' ? 'choose' : 'dirty'));
      clearTimeout(s.timer);
      const d = state.doc;
      const auto = d && (d.format !== 'docx' || docxChoice === 'docx') && clientSaves();
      if (auto) s.timer = setTimeout(() => saveRef.current(), AUTOSAVE_MS);
    }, [state.doc, docxChoice]);
    const onChangeRef = React.useRef(onChange);
    onChangeRef.current = onChange;

    const saveAs = React.useCallback(async (format) => {
      const ed = edRef.current;
      if (!ed) return;
      setSave('saving');
      try {
        const body = { from_path: rel, format, html: ed.getHTML() };
        if (format === 'md') body.markdown = ed.getMarkdown();
        const row = await apiFetch('/api/documents/doc-save-as', { method: 'POST', body: JSON.stringify(body) });
        seq.current.saved = seq.current.change;
        notice(format === 'html' ? `Saved as a formatted document: ${baseName(row.rel_path)}` : `Saved as ${row.rel_path}`);
        setAskDocx(false);
        if (format === 'html') { setDocxChoice(null); setRel(row.rel_path); }
        else setSave('saved');
      } catch (e) {
        setSave('error');
        setSaveError(mutationErrorText(e) || '');
      }
    }, [rel, notice]);

    // ── mount the editor ──
    React.useEffect(() => {
      if (state.status !== 'ready' || !hostRef.current) return undefined;
      const d = state.doc;
      const ed = window.DomovoiDocEditor.create(hostRef.current, {
        content: state.content,
        format: d.format,
        collab: collabRef.current,
        fonts: window.DomovoiFonts || null,
        readOnly: !!state.viewOnly,
        onChange: () => onChangeRef.current(),
        onNotice: notice,
        onSaveAsFormatted: d.format === 'md' ? () => saveAs('html') : undefined,
      });
      edRef.current = ed;
      seq.current = { change: 0, saved: 0, timer: 0, inflight: null };
      const collab = collabRef.current;
      if (collab && typeof collab.setSaver === 'function') {
        collab.setSaver(async ({ headers }) => {
          const body = { html: ed.getHTML() };
          if (d.format === 'md') body.markdown = ed.getMarkdown();
          try {
            await apiFetch(`/api/documents/doc/${encodeURIComponent(rel)}`, {
              method: 'PUT', headers: headers || {}, body: JSON.stringify(body), noPrompt: true });
            seq.current.saved = seq.current.change;
            setSaveError('');
            return true;
          } catch (e) {
            const why = deviceBlockReason(e);
            if (why) setRefused(why); else setSaveError(mutationErrorText(e) || '');
            throw e;
          }
        });
      }
      ed.ready.then(() => ed.focus()).catch(() => {});
      return () => { clearTimeout(seq.current.timer); ed.destroy(); edRef.current = null; };
    }, [state.status, state.doc, state.content]);

    // ── keyboard, leaving ──
    React.useEffect(() => {
      const key = (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); clearTimeout(seq.current.timer); saveRef.current(); }
      };
      const before = (e) => {
        if (seq.current.change > seq.current.saved) { e.preventDefault(); e.returnValue = ''; }
      };
      const hidden = () => {
        if (document.visibilityState === 'hidden' && seq.current.change > seq.current.saved) saveRef.current();
      };
      window.addEventListener('keydown', key);
      window.addEventListener('beforeunload', before);
      document.addEventListener('visibilitychange', hidden);
      return () => { window.removeEventListener('keydown', key); window.removeEventListener('beforeunload', before);
        document.removeEventListener('visibilitychange', hidden); };
    }, []);

    const requestClose = async () => {
      const s = seq.current;
      clearTimeout(s.timer);
      const collab = collabRef.current;
      if (collab && typeof collab.close === 'function') {
        // co-editing saves on the way out (if this client is behind)
        collabRef.current = null;
        try { await collab.close(); } catch (e) { /* the shared copy keeps the edits */ }
        onClose();
        return;
      }
      if (s.change > s.saved && state.doc && clientSaves()) {
        if (state.doc.format === 'docx' && docxChoice !== 'docx') { setAskDocx(true); return; }
        const ok = await saveRef.current();
        if (!ok && !window.confirm("Your latest changes couldn't be saved. Close anyway and lose them?")) return;
      }
      onClose();
    };
    // The phone app's Back button closes through here too (doc_page.jsx).
    if (closeRef) closeRef.current = requestClose;

    // ── download / share ──
    const title = state.doc ? state.doc.name : baseName(rel);
    const download = async (kind) => {
      setMenu(null);
      const ed = edRef.current;
      if (!ed) return;
      try {
        if (kind === 'pdf') { printDocument(ed.getHTML(), title); return; }
        if (kind === 'md') { await deliverFile(`${title}.md`, 'text/markdown', new Blob([ed.getMarkdown()], { type: 'text/markdown' })); return; }
        const r = await apiFetchRaw('/api/documents/convert/docx', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ html: ed.getHTML(), title }),
        });
        await deliverFile(`${title}.docx`, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', await r.blob());
      } catch (e) {
        const msg = mutationErrorText(e, 'Download', { kept: false });
        if (msg) notice(msg);
      }
    };

    const fmtWord = fmt ? FORMAT_WORDS[fmt] : null;
    const shell = embedded
      ? { position: 'fixed', inset: 0, zIndex: 80, background: 'var(--bg)', display: 'flex', flexDirection: 'column' }
      : { position: 'fixed', inset: 0, zIndex: 80, background: 'var(--bg)', display: 'flex', flexDirection: 'column' };
    return (
      <div style={shell} className="de-overlay">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', borderBottom: '1px solid var(--border)',
                      background: 'var(--card)', minWidth: 0 }}>
          <IconButton name="arrow-left" title="Close" aria-label="Close" onClick={requestClose}/>
          <Icon name="file-text" size={18} style={{ color: 'var(--brand)' }}/>
          <div style={{ minWidth: 0, flex: '0 1 auto', display: 'flex', flexDirection: 'column' }}>
            <strong style={{ fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={rel}>{title}</strong>
            <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              {fmtWord && <span style={{ fontSize: 11, color: 'var(--fg-faint)' }}>{fmtWord} file</span>}
              {state.status === 'ready' && !collabOn && !state.viewOnly && <SaveState state={blocked ? 'error' : save}/>}
              {state.viewOnly && <span style={{ fontSize: 11, color: 'var(--fg-faint)' }}>View only</span>}
              {collabOn && window.CollabPresence && <window.CollabPresence collab={collabRef.current}/>}
            </span>
          </div>
          <span style={{ flex: 1 }}/>
          {state.status === 'ready' && collabOn && window.CollabHistory && (
            <Button icon="history" onClick={() => setHistory(true)} title="Version history">
              <span className="de-hide-narrow">History</span>
            </Button>
          )}
          {state.status === 'ready' && fmt === 'docx' && (
            <Button variant="primary" icon="save" disabled={!!blocked || save === 'saved' || save === 'saving'}
                    onClick={() => { clearTimeout(seq.current.timer); doSave(); }}>Save</Button>
          )}
          {state.status === 'ready' && (
            <div style={{ position: 'relative' }}>
              <Button icon="share-2" aria-haspopup="menu" aria-expanded={!!menu}
                      onClick={(e) => setMenu(menu ? null : e.currentTarget)}>
                <span className="de-hide-narrow">Download</span>
              </Button>
              {menu && (
                <Popover anchor={menu} onClose={() => setMenu(null)} label="Download or share">
                  <div className="de-menu" role="menu">
                    <TB icon="printer" label="PDF (print or save as PDF)" onClick={() => download('pdf')}>PDF</TB>
                    <TB icon="file-text" label="Word (.docx)" onClick={() => download('docx')}>Word (.docx)</TB>
                    <TB icon="file-type" label="Markdown (.md)" onClick={() => download('md')}>Markdown (.md)</TB>
                  </div>
                </Popover>
              )}
            </div>
          )}
          {!embedded && window.PrintFileButton && state.status === 'ready' && (
            <window.PrintFileButton libraryId="core:documents" path={rel} name={`${title}${extOf(rel)}`}
                                    title={save === 'saved' ? 'print on a printer' : 'prints the last saved version'}/>
          )}
        </div>

        <WriteBlockedNotice reason={blocked}>
          {seq.current.change > seq.current.saved ? ' Your changes are still here — copy them somewhere safe before closing.' : ''}
        </WriteBlockedNotice>
        {save === 'error' && saveError && !blocked && (
          <div role="alert" style={{ padding: '6px 14px', fontSize: 12, background: 'var(--err-soft)', color: 'var(--err-ink)',
                                     borderBottom: '1px solid var(--border)' }}>
            {saveError} <Button onClick={() => saveRef.current()}>Try again</Button>
          </div>
        )}

        <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          {state.status === 'loading' && <div style={{ padding: 20, color: 'var(--fg-faint)', fontSize: 13 }}>Opening…</div>}
          {state.status === 'error' && (
            <div style={{ padding: 20, color: 'var(--err-ink)', fontSize: 13 }}>Couldn’t open this document: {state.message}</div>
          )}
          {state.status === 'ready' && <div ref={hostRef} style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}/>}
        </div>

        {history && collabOn && window.CollabHistory && (
          <window.CollabHistory path={rel} collab={collabRef.current} fire={fire} onClose={() => setHistory(false)}
                                renderPreview={(el, ydoc) => {
                                  const pv = window.DomovoiDocEditor.create(el, { format: fmt, collab: { ydoc }, readOnly: true });
                                  return () => pv.destroy();
                                }}/>
        )}
        {askDocx && (
          <div role="dialog" aria-modal="true" aria-label="How to save this Word file"
               style={{ position: 'fixed', inset: 0, zIndex: 125, background: 'rgba(0,0,0,.45)', display: 'flex',
                        alignItems: 'center', justifyContent: 'center', padding: 16 }}>
            <div style={{ background: 'var(--card)', color: 'var(--fg)', borderRadius: 'var(--r-lg, 12px)', padding: 20, maxWidth: 440,
                          boxShadow: 'var(--shadow-md)', display: 'flex', flexDirection: 'column', gap: 12 }}>
              <strong style={{ fontSize: 16 }}>Save this Word file</strong>
              <div style={{ fontSize: 13, lineHeight: 1.5, color: 'var(--fg-muted)' }}>
                <p style={{ margin: '0 0 8px' }}><b style={{ color: 'var(--fg)' }}>Keep as Word (.docx)</b>: it stays a Word file.
                  Complex formatting from Word (columns, text boxes, page layout) may shift.</p>
                <p style={{ margin: 0 }}><b style={{ color: 'var(--fg)' }}>Save as Document</b>: a formatted copy beside it that keeps
                  everything you see here. The Word file is left as it was.</p>
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                <Button onClick={() => { setAskDocx(false); if (save === 'choose') setSave('dirty'); }}>Cancel</Button>
                <Button onClick={() => { setDocxChoice('docx'); setAskDocx(false); setTimeout(() => saveRef.current({ asWord: true }), 0); }}>Keep as Word</Button>
                <Button variant="primary" onClick={() => saveAs('html')}>Save as Document</Button>
              </div>
            </div>
          </div>
        )}
        <style>{'@media (max-width: 560px) { .de-hide-narrow { display: none !important; } .de-overlay .de-state-text { display: none; } }'}</style>
      </div>
    );
  };

  const apiErrorTextSafe = (e) => {
    try { return typeof apiErrorText === 'function' ? apiErrorText(e, 160) : String(e.message || e); }
    catch (x) { return String((e && e.message) || e); }
  };

  window.DocEditor = DocEditorOverlay;
})();
