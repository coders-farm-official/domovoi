/* Markdown for assistant chat replies.
 *
 * The reply is model output — DATA, like a note (sanitize_html.js). So it
 * goes through the vendored `marked` (GitHub-flavoured: tables, fenced
 * code, strikethrough) with three changes, then through the same
 * allowlist sanitiser the document preview uses:
 *
 *  - raw HTML in the reply is shown as the literal text it is, never
 *    parsed as markup (a model echoing "<b>" means the characters);
 *  - images are not rendered yet: an image becomes its alt text;
 *  - links open in a new tab, so following one never unloads the chat.
 *
 * Single newlines are kept as line breaks (`breaks`), which is how a
 * chat model's plain-text lines read. Half-streamed input is fine: marked
 * renders whatever has arrived, and the next delta re-renders it.
 *
 * Its own Marked instance, so none of this leaks into the document
 * editor's use of the global `marked`. String in / string out with no
 * DOM, so domovoi/tests/test_web_chat_markdown.py runs the real thing.
 */

const ChatMarkdown = (() => {
  const esc = (s) => String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  let md = null;
  const instance = () => {
    if (md) return md;
    const lib = window.marked || (typeof marked !== 'undefined' ? marked : null);
    if (!lib || !lib.Marked) return null;
    md = new lib.Marked({ gfm: true, breaks: true, async: false });
    md.use({
      renderer: {
        // marked v12 renderer signatures (positional arguments).
        html(html) { return esc(html); },
        image(href, title, text) { return esc(text || ''); },
      },
    });
    return md;
  };

  const render = (text) => {
    const m = instance();
    const sanitize = window.sanitizeHtml;
    // Without the parser or the sanitiser, show the text as text.
    if (!m || typeof sanitize !== 'function') {
      return `<p>${esc(text).replace(/\n/g, '<br/>')}</p>`;
    }
    const html = m.parse(String(text || '')).replace(/<a /g, '<a target="_blank" ');
    return sanitize(html);
  };

  return { render };
})();

window.chatMarkdownHtml = ChatMarkdown.render;
