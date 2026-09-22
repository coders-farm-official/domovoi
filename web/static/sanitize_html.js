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
  const PER_ELEMENT_ATTRS = {
    a: new Set(['href', 'target', 'rel']),
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

  const renderTag = (name, attrs) => {
    const allowed = PER_ELEMENT_ATTRS[name];
    const parts = [name];
    for (const [attr, value] of attrs) {
      // Every event handler is spelled on*, and nothing else here needs a
      // name that starts that way.
      if (attr.startsWith('on')) continue;
      if (!GLOBAL_ATTRS.has(attr) && !(allowed && allowed.has(attr))) continue;
      if (URL_ATTRS.has(attr)) {
        const url = safeUrl(value, attr);
        if (url === null) continue;
        parts.push(`${attr}="${escapeAttr(url)}"`);
        continue;
      }
      parts.push(value === '' ? attr : `${attr}="${escapeAttr(value)}"`);
    }
    if (name === 'a') parts.push('rel="noopener noreferrer nofollow"');
    const open = parts.join(' ');
    return VOID.has(name) ? `<${open}/>` : `<${open}>`;
  };

  /* Skip a raw-text element's content: everything up to its end tag (or
   * the end of the string, if the author never closed it). */
  const skipContent = (html, from, name) => {
    const end = html.toLowerCase().indexOf(`</${name}`, from);
    if (end === -1) return html.length;
    const close = html.indexOf('>', end);
    return close === -1 ? html.length : close + 1;
  };

  const sanitize = (html) => {
    const input = String(html == null ? '' : html);
    let out = '';
    let i = 0;
    while (i < input.length) {
      const lt = input.indexOf('<', i);
      if (lt === -1) {
        out += escapeText(input.slice(i));
        break;
      }
      out += escapeText(input.slice(i, lt));

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
      const name = nameMatch[0].toLowerCase();
      const parsed = readAttributes(input, lt + (closing ? 2 : 1) + name.length);
      if (parsed === null) {
        // An unterminated tag: everything after it is suspect, so the
        // rest of the input becomes text rather than markup.
        out += escapeText(input.slice(lt));
        break;
      }

      if (DROP_WITH_CONTENT.has(name)) {
        i = closing ? parsed.end : skipContent(input, parsed.end, name);
        continue;
      }
      if (!ALLOWED.has(name)) {
        i = parsed.end;           // drop the tag, keep the text inside it
        continue;
      }
      if (closing) {
        out += VOID.has(name) ? '' : `</${name}>`;
      } else {
        out += renderTag(name, parsed.attrs);
      }
      i = parsed.end;
    }
    return out;
  };

  return { sanitize };
})();

window.sanitizeHtml = SanitizeHtml.sanitize;

/* Node (the test harness) loads this file in a plain vm context, where
 * `module` exists only when it is required as a CommonJS module. */
if (typeof module !== 'undefined' && module.exports) module.exports = SanitizeHtml;
