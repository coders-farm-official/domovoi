/* HTML sanitiser for rendered markdown (WEB-3).
 *
 * The document editor previews a note by handing `marked`'s output to
 * `dangerouslySetInnerHTML`. Markdown is allowed to contain raw HTML, so
 * that output can contain anything the note's author typed — including a
 * `<script>` or an `onerror=` handler, which would then run inside the
 * dashboard's own page, next to the in-memory admin token. A note is
 * DATA. This turns it back into data.
 *
 * The approach is allowlist-and-escape, with the escape as the default:
 * the string is scanned for tags, a tag is rebuilt only when its name is
 * on the element list and only from attributes on that element's list,
 * and anything the scanner does not fully understand is emitted as
 * escaped text rather than passed through. A `<script>`, `<style>`,
 * `<iframe>` or `<textarea>` takes its CONTENT with it (they hold raw
 * text, so leaving the body behind would leave the payload behind).
 *
 * URLs in `href` / `src` are entity-decoded before they are judged, so
 * `java&#115;cript:` is refused the same as `javascript:`, and only
 * relative URLs, http(s), mailto (links) and inline raster data images
 * survive.
 *
 * Deliberately string-in / string-out with no DOM: it runs identically in
 * the browser and in the test harness that proves it
 * (domovoi/tests/test_markdown_preview_sanitised.py).
 */

const SanitizeHtml = (() => {
  // Elements markdown can legitimately produce, plus the inline markup a
  // note's author might hand-write. Anything absent is dropped.
  const ALLOWED = new Set([
    'p', 'br', 'hr', 'div', 'span',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'strong', 'b', 'em', 'i', 'u', 's', 'del', 'ins', 'mark', 'small',
    'sub', 'sup', 'abbr', 'cite', 'q', 'kbd', 'samp', 'var',
    'ul', 'ol', 'li', 'dl', 'dt', 'dd',
    'blockquote', 'pre', 'code',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
    'a', 'img', 'figure', 'figcaption', 'input',
  ]);

  // Elements whose content is raw text: dropping the tag alone would
  // leave the payload sitting in the output as markup.
  const DROP_WITH_CONTENT = new Set([
    'script', 'style', 'iframe', 'object', 'embed', 'template',
    'textarea', 'title', 'noscript', 'noembed', 'xmp', 'math', 'svg',
  ]);

  // Void elements — written back self-closing, never given an end tag.
  const VOID = new Set(['br', 'hr', 'img', 'input', 'wbr']);

  const GLOBAL_ATTRS = new Set(['class', 'title', 'dir', 'lang', 'id']);
  // A link's `rel` is never the author's: the sanitiser writes its own
  // (renderTag), and an author's `rel="opener"` would otherwise hand the
  // linked page a live window.opener on a target=_blank link (FE-4).
  const PER_ELEMENT_ATTRS = {
    a: new Set(['href', 'target']),
    img: new Set(['src', 'alt', 'width', 'height', 'loading']),
    td: new Set(['colspan', 'rowspan', 'align']),
    th: new Set(['colspan', 'rowspan', 'align', 'scope']),
    col: new Set(['span']),
    ol: new Set(['start', 'type']),
    input: new Set(['type', 'checked', 'disabled']),
  };
  const URL_ATTRS = new Set(['href', 'src']);

  const escapeText = (s) => s
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const escapeAttr = (s) => escapeText(s).replace(/"/g, '&quot;');
  /* Text BETWEEN tags keeps the entities it already carries: marked has
   * escaped the author's "&" to "&amp;" once, and escaping it again put
   * a literal "&amp;" on screen. An entity in a text node only ever
   * decodes to a character — "&lt;script&gt;" reads as those characters
   * and is never parsed as a tag — so leaving one alone is safe. A bare
   * "&", "<" or ">" is still escaped. */
  const ENTITY = /&(?!(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[a-zA-Z][a-zA-Z0-9]{1,31});)/g;
  const escapeTextRun = (s) => s
    .replace(ENTITY, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  const NAMED = {
    amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ',
    tab: '\t', newline: '\n', colon: ':', sol: '/',
  };

  /* Decode enough of the entity syntax to judge a URL the way a browser
   * would: numeric (decimal and hex, terminator optional) and the handful
   * of named entities that can spell a scheme. */
  const decodeEntities = (value) => value.replace(
    /&(#[xX]?[0-9a-fA-F]+|[a-zA-Z]+);?/g,
    (whole, body) => {
      if (body[0] === '#') {
        const hex = body[1] === 'x' || body[1] === 'X';
        const code = parseInt(hex ? body.slice(2) : body.slice(1), hex ? 16 : 10);
        return Number.isFinite(code) && code > 0 && code < 0x110000
          ? String.fromCodePoint(code) : whole;
      }
      const named = NAMED[body.toLowerCase()];
      return named === undefined ? whole : named;
    },
  );

  const DATA_IMAGE = /^data:image\/(png|jpeg|jpg|gif|webp|avif|bmp);base64,[a-z0-9+/=\s]*$/i;
  const SAFE_SCHEME = /^(https?|mailto|tel):/i;
  const HAS_SCHEME = /^[a-z][a-z0-9+.-]*:/i;

  /* A URL is kept when it is relative, or carries a scheme that cannot
   * execute. Control characters and whitespace come out first: a browser
   * ignores them inside a scheme, so `java\nscript:` is `javascript:`. */
  const safeUrl = (raw, attr) => {
    const value = decodeEntities(raw).replace(/[\u0000-\u0020\u007f-\u00a0]/g, '');
    if (!value) return null;
    if (attr === 'src' && DATA_IMAGE.test(value)) return value;
    if (!HAS_SCHEME.test(value)) {
      // Relative, anchor or protocol-relative — no scheme to abuse.
      return value;
    }
    return SAFE_SCHEME.test(value) ? value : null;
  };

  /* Read the attributes of a tag that starts at `i` (just past the tag
   * name). Returns {attrs, end, selfClosing} or null when the tag never
   * closes — quotes are honoured, so a `>` inside a value doesn't end it. */
  const readAttributes = (html, i) => {
    const attrs = [];
    let selfClosing = false;
    while (i < html.length) {
      while (i < html.length && /\s/.test(html[i])) i += 1;
      if (i >= html.length) return null;
      if (html[i] === '>') return { attrs, end: i + 1, selfClosing };
      if (html[i] === '/' && html[i + 1] === '>') {
        return { attrs, end: i + 2, selfClosing: true };
      }
      const nameMatch = /^[^\s/>"'=]+/.exec(html.slice(i));
      if (!nameMatch) return null;
      const name = nameMatch[0];
      i += name.length;
      while (i < html.length && /\s/.test(html[i])) i += 1;
      let value = '';
      if (html[i] === '=') {
        i += 1;
        while (i < html.length && /\s/.test(html[i])) i += 1;
        const quote = html[i];
        if (quote === '"' || quote === "'") {
          const close = html.indexOf(quote, i + 1);
          if (close === -1) return null;
          value = html.slice(i + 1, close);
          i = close + 1;
        } else {
          const unquoted = /^[^\s>]*/.exec(html.slice(i))[0];
          value = unquoted;
          i += unquoted.length;
        }
      }
      attrs.push([name.toLowerCase(), value]);
    }
    return null;
  };

  // What every link carries, written FIRST: when an attribute appears
  // twice the HTML parser keeps the first, so nothing later in the tag —
  // an author's `rel`, should one ever get past the allowlist again — can
  // out-rank it (FE-4).
  const LINK_REL = 'rel="noopener noreferrer nofollow"';

  /* ── The DOCUMENT policy (formatted documents, doc_editor.jsx) ──────
   * What the live editor produces and nothing more: its tags, a few
   * data-* attributes with fixed values, `style` cut down to the
   * properties the editor writes on that element (each value matched
   * whole; anything that could reach outside or hide from this check —
   * url(), expression(), escapes, comments — drops the whole attribute),
   * and pictures only as embedded rasters. The SERVER applies the same
   * allowlist (domovoi/richdoc/sanitize.py) on every load and save; this
   * copy keeps a pasted or opened file from ever reaching the page raw. */
  const DOC_ALLOWED = new Set([
    'p', 'br', 'hr', 'div', 'span', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'strong', 'b', 'em', 'i', 'u', 's', 'del', 'strike', 'mark', 'sub', 'sup', 'code', 'pre',
    'blockquote', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td',
    'colgroup', 'col', 'a', 'img', 'figure', 'figcaption', 'label', 'input',
  ]);
  const DOC_ATTRS = {
    a: new Set(['href', 'target', 'title']),
    img: new Set(['src', 'alt', 'title', 'width', 'height']),
    figure: new Set(['data-align', 'data-width']),
    mark: new Set(['data-color']),
    ul: new Set(['data-type']),
    li: new Set(['data-type', 'data-checked']),
    input: new Set(['type', 'checked', 'disabled']),
    ol: new Set(['start', 'type']),
    td: new Set(['colspan', 'rowspan', 'colwidth']),
    th: new Set(['colspan', 'rowspan', 'colwidth']),
    code: new Set(['class']),
  };
  const DOC_GLOBAL = new Set(['dir']);
  const ALIGN_ONLY = new Set(['text-align']);
  const DOC_STYLE = {
    p: ALIGN_ONLY, h1: ALIGN_ONLY, h2: ALIGN_ONLY, h3: ALIGN_ONLY,
    h4: ALIGN_ONLY, h5: ALIGN_ONLY, h6: ALIGN_ONLY,
    span: new Set(['color', 'background-color', 'font-family', 'font-size']),
    mark: new Set(['background-color', 'color']),
    table: new Set(['width', 'min-width']),
    col: new Set(['width', 'min-width']),
    td: new Set(['background-color']),
    th: new Set(['background-color']),
    figure: new Set(['width']),
  };
  const COLOUR = /^(#[0-9a-f]{3,8}|rgba?\(\s*\d{1,3}%?\s*,\s*\d{1,3}%?\s*,\s*\d{1,3}%?\s*(,\s*(0|1|0?\.\d+|\d{1,3}%)\s*)?\)|[a-z]{3,24})$/i;
  const STYLE_CHECK = {
    color: (v) => COLOUR.test(v),
    'background-color': (v) => COLOUR.test(v),
    'font-family': (v) => /^[A-Za-z0-9 ,'"_-]{1,200}$/.test(v),
    'font-size': (v) => /^\d{1,3}(\.\d{1,2})?(px|pt|em|rem|%)$/i.test(v),
    width: (v) => /^\d{1,5}(\.\d{1,3})?(px|%)$/i.test(v),
    'min-width': (v) => /^\d{1,5}(\.\d{1,3})?(px|%)$/i.test(v),
    'text-align': (v) => ['left', 'center', 'right', 'justify', 'start', 'end'].includes(v.toLowerCase()),
  };
  const CSS_REFUSED = /(url|expression|image|var|attr|calc|env|import)\s*\(|\\|\/\*|[<>@{}]/i;
  const cleanStyle = (name, raw) => {
    const allowed = DOC_STYLE[name];
    const value = decodeEntities(raw || '');
    if (!allowed || !value || CSS_REFUSED.test(value)) return null;
    const kept = [];
    const seenProps = new Set();
    for (const decl of value.split(';')) {
      const at = decl.indexOf(':');
      if (at === -1) continue;
      const prop = decl.slice(0, at).trim().toLowerCase();
      let v = decl.slice(at + 1).trim().replace(/!important$/i, '').trim();
      if (!allowed.has(prop) || seenProps.has(prop) || !v) continue;
      if (!STYLE_CHECK[prop] || !STYLE_CHECK[prop](v)) continue;
      seenProps.add(prop);
      kept.push(`${prop}: ${v}`);
    }
    return kept.length ? kept.join('; ') : null;
  };
  const DOC_VALUE = {
    'data-type': (v, name) => (v === ({ ul: 'taskList', li: 'taskItem' })[name] ? v : null),
    'data-checked': (v) => (v === 'true' || v === 'false' ? v : null),
    'data-align': (v) => (['left', 'center', 'right'].includes(v) ? v : null),
    'data-width': (v) => (/^\d{1,3}$/.test(v) && +v >= 1 && +v <= 100 ? v : null),
    'data-color': (v) => (COLOUR.test(v) ? v : null),
    colwidth: (v) => (/^\d{1,5}(,\d{1,5}){0,50}$/.test(v) ? v : null),
    colspan: (v) => (/^\d{1,3}$/.test(v) ? v : null),
    rowspan: (v) => (/^\d{1,3}$/.test(v) ? v : null),
    width: (v) => (/^\d{1,5}$/.test(v) ? v : null),
    height: (v) => (/^\d{1,5}$/.test(v) ? v : null),
    start: (v) => (/^\d{1,6}$/.test(v) ? v : null),
    type: (v, name) => (name === 'input' ? (v.toLowerCase() === 'checkbox' ? 'checkbox' : null)
      : (['1', 'a', 'A', 'i', 'I'].includes(v) ? v : null)),
    class: (v, name) => (name === 'code' && /^language-[A-Za-z0-9_+-]{1,40}$/.test(v) ? v : null),
    dir: (v) => (['ltr', 'rtl', 'auto'].includes(v.toLowerCase()) ? v.toLowerCase() : null),
  };
  const DOC_RENAME = { b: 'strong', i: 'em', del: 's', strike: 's' };

  const PREVIEW_POLICY = { allowed: ALLOWED, global: GLOBAL_ATTRS, perElement: PER_ELEMENT_ATTRS, doc: false };
  const DOCUMENT_POLICY = { allowed: DOC_ALLOWED, global: DOC_GLOBAL, perElement: DOC_ATTRS, doc: true };

  const renderTag = (name, attrs, policy) => {
    const pol = policy || PREVIEW_POLICY;
    const allowed = pol.perElement[name];
    const parts = [name];
    if (name === 'a') parts.push(LINK_REL);
    const seen = new Set();
    for (const [attr, rawValue] of attrs) {
      let value = rawValue;
      // Every event handler is spelled on*, and nothing else here needs a
      // name that starts that way.
      if (attr.startsWith('on')) continue;
      if (pol.doc && attr === 'style') {
        if (seen.has(attr)) continue;
        const style = cleanStyle(name, value);
        if (style === null) continue;
        seen.add(attr);
        parts.push(`style="${escapeAttr(style)}"`);
        continue;
      }
      if (!pol.global.has(attr) && !(allowed && allowed.has(attr))) continue;
      if (pol.doc && DOC_VALUE[attr]) {
        if (seen.has(attr)) continue;
        if (attr === 'checked' || attr === 'disabled') { seen.add(attr); parts.push(attr); continue; }
        const v = DOC_VALUE[attr](decodeEntities(value).trim(), name);
        if (v === null) continue;
        value = v;
      }
      if (pol.doc && attr === 'src' && !DATA_IMAGE.test(decodeEntities(value).replace(/[\u0000- \u007f- ]/g, ''))) continue;
      // One of each, the first as written — what the browser would keep.
      if (seen.has(attr)) continue;
      seen.add(attr);
      // A link opens here or in a new tab; a named target could steer an
      // existing window.
      if (name === 'a' && attr === 'target' && value.trim().toLowerCase() !== '_blank') continue;
      if (URL_ATTRS.has(attr)) {
        const url = safeUrl(value, attr);
        if (url === null) continue;
        parts.push(`${attr}="${escapeAttr(url)}"`);
        continue;
      }
      parts.push(value === '' ? attr : `${attr}="${escapeAttr(value)}"`);
    }
    const open = parts.join(' ');
    if (pol.doc && name === 'img' && !seen.has('src')) return '';
    if (pol.doc && name === 'input' && !seen.has('type')) return '';
    return (VOID.has(name) || (pol.doc && name === 'col')) ? `<${open}/>` : `<${open}>`;
  };

  /* Skip a raw-text element's content: everything up to its end tag (or
   * the end of the string, if the author never closed it). */
  const skipContent = (html, from, name) => {
    const end = html.toLowerCase().indexOf(`</${name}`, from);
    if (end === -1) return html.length;
    const close = html.indexOf('>', end);
    return close === -1 ? html.length : close + 1;
  };

  // Elements HTML gives no end tag: dropping one never means "skip to its
  // end tag" (there is none, and waiting for it would eat the document).
  const VOID_DROP = new Set(['embed', 'source', 'track', 'param', 'link', 'meta',
    'base', 'area', 'frame', 'keygen']);

  const sanitize = (html, policy) => {
    const pol = policy || PREVIEW_POLICY;
    const isVoid = (n) => VOID.has(n) || (pol.doc && n === 'col');
    const input = String(html == null ? '' : html);
    let out = '';
    let i = 0;
    while (i < input.length) {
      const lt = input.indexOf('<', i);
      if (lt === -1) {
        out += escapeTextRun(input.slice(i));
        break;
      }
      out += escapeTextRun(input.slice(i, lt));

      // Comments, doctypes and processing instructions: dropped whole.
      if (input.startsWith('<!--', lt)) {
        const end = input.indexOf('-->', lt + 4);
        i = end === -1 ? input.length : end + 3;
        continue;
      }
      if (input[lt + 1] === '!' || input[lt + 1] === '?') {
        const end = input.indexOf('>', lt);
        i = end === -1 ? input.length : end + 1;
        continue;
      }

      const closing = input[lt + 1] === '/';
      const nameMatch = /^[a-zA-Z][a-zA-Z0-9]*/.exec(input.slice(lt + (closing ? 2 : 1)));
      if (!nameMatch) {
        // Not a tag at all — a stray "<" in prose. Keep it as text.
        out += '&lt;';
        i = lt + 1;
        continue;
      }
      const tagName = nameMatch[0].toLowerCase();
      const name = (pol.doc && DOC_RENAME[tagName]) || tagName;
      const parsed = readAttributes(input, lt + (closing ? 2 : 1) + tagName.length);
      if (parsed === null) {
        // An unterminated tag: everything after it is suspect, so the
        // rest of the input becomes text rather than markup.
        out += escapeTextRun(input.slice(lt));
        break;
      }

      if (VOID_DROP.has(name)) {
        i = parsed.end;
        continue;
      }
      if (DROP_WITH_CONTENT.has(name)) {
        i = closing ? parsed.end : skipContent(input, parsed.end, name);
        continue;
      }
      if (!pol.allowed.has(name)) {
        i = parsed.end;           // drop the tag, keep the text inside it
        continue;
      }
      if (closing) {
        out += isVoid(name) ? '' : `</${name}>`;
      } else {
        out += renderTag(name, parsed.attrs, pol);
      }
      i = parsed.end;
    }
    return out;
  };

  const sanitizeDocument = (html) => sanitize(html, DOCUMENT_POLICY);
  return { sanitize: (html) => sanitize(html, PREVIEW_POLICY), sanitizeDocument };
})();

window.sanitizeHtml = SanitizeHtml.sanitize;
// The formatted-document allowlist (doc_editor.jsx): what the editor makes.
window.sanitizeDocumentHtml = SanitizeHtml.sanitizeDocument;

/* Node (the test harness) loads this file in a plain vm context, where
 * `module` exists only when it is required as a CommonJS module. */
if (typeof module !== 'undefined' && module.exports) module.exports = SanitizeHtml;
