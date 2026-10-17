/* Contrast audit for the Domovoi dashboard (WCAG 2.x ratios), in the page.
 *
 * Load it into a dashboard tab (Playwright's add_script_tag, the browser's
 * devtools console, or scripts/dev/contrast_audit.py, which drives every
 * page in both themes) and call:
 *
 *     window.__domovoiContrastAudit()            // -> { failures, checked, unknown }
 *     window.__domovoiContrastAudit({ text: 4.5, ui: 3 })
 *     window.__domovoiContrastMark()             // outline the last audit's failures
 *     window.__domovoiContrastFocus(i)           // scroll failure i into view, its rect
 *     window.__domovoiContrastUnmark()
 *
 * What it measures, against the colour actually painted behind the element
 * (its own background, then each ancestor's, composited until one is opaque):
 *   - text: every element with its own visible text. 4.5:1, or 3:1 for large
 *     text (24px, or 18.66px bold) and for disabled controls (the owner's
 *     floor: a dimmed control stays readable).
 *   - placeholder: an empty input's placeholder text. 4.5:1.
 *   - icon: inline SVG glyphs (lucide), CSS-mask icons (the colour is the
 *     element's background, i.e. currentColor) and small <img> icons (the
 *     mean colour of their opaque pixels). 3:1.
 * Opacity on the element or any ancestor fades that whole group (its
 * background too) into what is behind it, so a "dimmed" title or a
 * disabled button is measured as it is seen.
 * Anything with a background image or gradient behind it can't be judged
 * from styles alone and is counted under `unknown`, not failed.
 */
(function () {
  'use strict';
  if (window.__domovoiContrastAudit) return;

  const canvas = document.createElement('canvas');
  canvas.width = 1; canvas.height = 1;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  const colourCache = new Map();

  // Any CSS colour (oklch, color-mix, rgb, a keyword) -> [r, g, b, a] in
  // sRGB 0..255 / 0..1, by painting it: the browser does the conversion.
  const parse = (str) => {
    if (!str || str === 'none' || str === 'transparent') return [0, 0, 0, 0];
    if (colourCache.has(str)) return colourCache.get(str);
    let out = [0, 0, 0, 0];
    if (window.CSS && CSS.supports && CSS.supports('color', str)) {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = str;
      ctx.fillRect(0, 0, 1, 1);
      const d = ctx.getImageData(0, 0, 1, 1).data;
      out = [d[0], d[1], d[2], d[3] / 255];
    }
    colourCache.set(str, out);
    return out;
  };

  const over = (top, under) => {
    const a = top[3];
    return [top[0] * a + under[0] * (1 - a), top[1] * a + under[1] * (1 - a),
            top[2] * a + under[2] * (1 - a), 1];
  };

  const lum = (c) => {
    const ch = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2]);
  };
  const ratio = (a, b) => {
    const la = lum(a); const lb = lum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  };
  const hex = (c) => '#' + c.slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');

  const short = (el) => {
    const parts = [];
    let n = el;
    for (let i = 0; n && n.nodeType === 1 && i < 4; i += 1, n = n.parentElement) {
      let s = n.tagName.toLowerCase();
      if (n.id) { parts.unshift(`${s}#${n.id}`); break; }
      const cls = (typeof n.className === 'string' ? n.className : (n.getAttribute('class') || ''))
        .trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) s += '.' + cls.join('.');
      parts.unshift(s);
    }
    return parts.join(' > ');
  };

  // Porter-Duff "over" for colours that may both be translucent.
  const overA = (top, under) => {
    const a = top[3] + under[3] * (1 - top[3]);
    if (a <= 0) return [0, 0, 0, 0];
    const mix = (i) => (top[i] * top[3] + under[i] * under[3] * (1 - top[3])) / a;
    return [mix(0), mix(1), mix(2), a];
  };

  // The colour stops of a gradient background ('image' for a picture, or
  // a gradient whose colours can't be read; null for none).
  const stopsOf = (img) => {
    if (!img || img === 'none') return null;
    if (/url\(/.test(img) || !/gradient\(/.test(img)) return 'image';
    const m = img.match(/(?:oklch|oklab|rgba?|hsla?|color|lab|lch)\([^()]*\)|#[0-9a-f]{3,8}\b/gi);
    return m && m.length ? m.map(parse) : 'image';
  };

  // What a pixel of `fg` drawn inside `el` looks like on screen, and what
  // the same pixel looks like without it (the background), walking from
  // `el` out to the root: each element paints its background colour, then
  // its gradient, under what is inside it, and its opacity fades that
  // whole group into what is behind it. A gradient gives one answer per
  // colour stop (the caller takes the worst). `own` false skips el's own
  // background (a mask icon's background IS its glyph). `unknown` names
  // the element whose background picture makes any answer a guess.
  const seen = (el, fg, own = true) => {
    let variants = [{ withFg: fg ? fg.slice() : [0, 0, 0, 0], without: [0, 0, 0, 0] }];
    let unknown = null;
    let branched = false;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (n !== el || own) {
        const c = parse(cs.backgroundColor);
        const stops = stopsOf(cs.backgroundImage);
        if (stops === 'image') { if (!unknown) unknown = short(n); }
        let layers = [c];
        if (Array.isArray(stops)) {
          layers = (branched ? stops.slice(0, 1) : stops).map((st) => overA(st, c));
          branched = true;
        }
        const next = [];
        for (const v of variants) {
          for (const l of layers) {
            next.push(l[3] > 0 ? { withFg: overA(v.withFg, l), without: overA(v.without, l) }
                               : { withFg: v.withFg.slice(), without: v.without.slice() });
          }
        }
        variants = next;
      }
      const o = parseFloat(cs.opacity);
      if (!Number.isNaN(o) && o < 1) variants.forEach((v) => { v.withFg[3] *= o; v.without[3] *= o; });
    }
    let page = parse(getComputedStyle(document.documentElement).backgroundColor);
    if (page[3] < 0.999) page = over(page, [255, 255, 255, 1]);
    const pairs = variants.map((v) => ({ fg: over(v.withFg, page), bg: over(v.without, page) }));
    pairs.sort((x, y) => ratio(x.fg, x.bg) - ratio(y.fg, y.bg));
    return { fg: pairs[0].fg, bg: pairs[0].bg, unknown };
  };

  const fade = (el) => {
    let o = 1;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const v = parseFloat(getComputedStyle(n).opacity);
      if (!Number.isNaN(v)) o *= v;
    }
    return o;
  };

  const shown = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return null;
    const cs = getComputedStyle(el);
    if (cs.visibility !== 'visible' || cs.display === 'none') return null;
    return r;
  };

  const isDisabled = (el) => !!el.closest(
    ':disabled, [aria-disabled="true"], fieldset:disabled, [data-disabled="true"]');

  const ownText = (el) => {
    let t = '';
    for (const n of el.childNodes) if (n.nodeType === 3) t += n.textContent;
    return t.replace(/\s+/g, ' ').trim();
  };

  // Mean colour of an <img>'s opaque pixels (same-origin icons only).
  const imgColour = (img) => {
    try {
      const w = Math.max(1, Math.min(48, img.naturalWidth || img.width));
      const h = Math.max(1, Math.min(48, img.naturalHeight || img.height));
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      const x = c.getContext('2d', { willReadFrequently: true });
      x.drawImage(img, 0, 0, w, h);
      const d = x.getImageData(0, 0, w, h).data;
      let r = 0; let g = 0; let b = 0; let a = 0;
      for (let i = 0; i < d.length; i += 4) {
        const al = d[i + 3] / 255;
        if (al < 0.5) continue;      // the glyph, not its anti-aliased edge
        r += d[i] * al; g += d[i + 1] * al; b += d[i + 2] * al; a += al;
      }
      return a ? [r / a, g / a, b / a, 1] : null;
    } catch (e) {
      return null;                   // cross-origin: can't read it
    }
  };

  const svgColour = (svg) => {
    const shapes = [svg, ...svg.querySelectorAll('path, circle, rect, line, polyline, polygon, ellipse')];
    for (const s of shapes) {
      const cs = getComputedStyle(s);
      const st = parse(cs.stroke);
      if (cs.stroke && cs.stroke !== 'none' && st[3] > 0 && s !== svg) return st;
      const fi = parse(cs.fill);
      if (cs.fill && cs.fill !== 'none' && fi[3] > 0 && s !== svg) return fi;
    }
    const cs = getComputedStyle(svg);
    const st = parse(cs.stroke);
    if (cs.stroke !== 'none' && st[3] > 0) return st;
    return parse(cs.color);
  };

  const audit = (opts = {}) => {
    const need = { text: opts.text || 4.5, large: opts.large || 3, ui: opts.ui || 3,
                   disabled: opts.disabled || 3 };
    const root = opts.root || document.body;
    // Logos are exempt from WCAG contrast (1.4.3 / 1.4.11): the brand cat
    // stays brand amber in both themes. Plus this audit's own outlines.
    const exempt = ['[data-contrast-skip]', '.contrast-audit-mark', '.brand-row .glyph',
                    ...(opts.exempt || [])].join(', ');
    const theme = document.documentElement.getAttribute('data-theme') || 'light';
    const failures = [];
    let checked = 0;
    const unknown = [];
    const reported = new Set();
    const els = [];

    const judge = (el, kind, fg, minimum, extra) => {
      const r = shown(el);
      if (!r || !fg) return;
      const o = fade(el);
      if (o < 0.02) return;                       // fully faded = not shown
      const { fg: seenFg, bg, unknown: hidden } = seen(el, fg, kind !== 'mask-icon');
      if (hidden) { unknown.push({ kind, path: short(el), behind: hidden }); return; }
      const value = ratio(seenFg, bg);
      checked += 1;
      if (value + 1e-6 < minimum) {
        const key = `${kind}|${short(el)}|${(extra && extra.text) || ''}`;
        if (reported.has(key)) return;
        reported.add(key);
        failures.push({
          kind, theme, ratio: Math.round(value * 100) / 100, need: minimum,
          fg: hex(seenFg), bg: hex(bg), opacity: Math.round(o * 100) / 100,
          path: short(el), disabled: isDisabled(el),
          rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
          ...(extra || {}),
        });
        els.push(el);
      }
    };

    for (const el of root.querySelectorAll('*')) {
      if (el.closest(exempt)) continue;
      const tag = el.tagName.toLowerCase();
      if (tag === 'script' || tag === 'style' || tag === 'noscript' || tag === 'option') continue;
      const cs = getComputedStyle(el);

      // Text with its own characters.
      const text = ownText(el);
      if (text && !(tag === 'svg' || el.closest('svg'))) {
        const px = parseFloat(cs.fontSize) || 13;
        const bold = (parseInt(cs.fontWeight, 10) || 400) >= 700;
        const large = px >= 24 || (bold && px >= 18.66);
        const disabled = isDisabled(el);
        const minimum = disabled ? need.disabled : (large ? need.large : need.text);
        judge(el, 'text', parse(cs.color), minimum, { text: text.slice(0, 60), px });
      }
      // Form controls show their value as their own text.
      if ((tag === 'input' || tag === 'textarea' || tag === 'select') && el.type !== 'hidden'
          && el.type !== 'checkbox' && el.type !== 'radio' && el.type !== 'range' && el.type !== 'color') {
        const minimum = isDisabled(el) ? need.disabled : need.text;
        const value = tag === 'select' ? (el.selectedOptions[0] && el.selectedOptions[0].textContent) : el.value;
        if (value && String(value).trim()) {
          judge(el, 'field', parse(cs.color), minimum, { text: String(value).trim().slice(0, 60) });
        } else if (el.placeholder) {
          const ph = getComputedStyle(el, '::placeholder');
          judge(el, 'placeholder', parse(ph.color), minimum, { text: el.placeholder.slice(0, 60) });
        }
      }
      // Icons.
      if (tag === 'svg' && !el.parentElement.closest('svg')) {
        const r = el.getBoundingClientRect();
        if (r.width >= 8 && r.width <= 64 && r.height >= 8 && r.height <= 64) {
          judge(el, 'icon', svgColour(el), isDisabled(el) ? need.disabled : need.ui,
                { text: (el.getAttribute('class') || '').replace(/\s+/g, ' ').slice(0, 60) });
        }
      } else if (tag === 'img') {
        const r = el.getBoundingClientRect();
        if (el.complete && r.width >= 8 && r.width <= 40 && r.height <= 40) {
          judge(el, 'img-icon', imgColour(el), isDisabled(el) ? need.disabled : need.ui,
                { text: (el.getAttribute('src') || '').slice(-60) });
        }
      } else {
        const mask = cs.webkitMaskImage || cs.maskImage;
        if (mask && mask !== 'none') {
          judge(el, 'mask-icon', parse(cs.backgroundColor), isDisabled(el) ? need.disabled : need.ui,
                { text: mask.slice(-60) });
        }
      }
    }
    last = els;
    return { theme, route: location.hash, failures, checked, unknown };
  };

  // The elements of the last audit's failures, in the same order.
  let last = [];

  // Outline the last audit's failures (numbered as in its list).
  const mark = () => {
    unmark();
    last.forEach((el, i) => {
      const r = el.getBoundingClientRect();
      if (!r || !r.width) return;
      const d = document.createElement('div');
      d.className = 'contrast-audit-mark';
      d.style.cssText = `position:fixed;left:${r.left}px;top:${r.top}px;`
        + `width:${r.width}px;height:${r.height}px;outline:2px solid #ff00aa;outline-offset:1px;`
        + 'pointer-events:none;z-index:2147483647;';
      const t = document.createElement('span');
      t.textContent = `${i + 1}`;
      t.style.cssText = 'position:absolute;left:-2px;top:-14px;font:10px monospace;'
        + 'background:#ff00aa;color:#fff;padding:0 2px;';
      d.appendChild(t);
      document.body.appendChild(d);
    });
  };
  const unmark = () => document.querySelectorAll('.contrast-audit-mark').forEach((n) => n.remove());

  // Scroll the i-th failure of the last audit into view; its rect after.
  const focus = (i) => {
    const el = last[i];
    if (!el || !el.isConnected) return null;
    el.scrollIntoView({ block: 'center', inline: 'nearest' });
    const r = el.getBoundingClientRect();
    return { x: r.left, y: r.top, w: r.width, h: r.height };
  };

  window.__domovoiContrastAudit = audit;
  window.__domovoiContrastFocus = focus;
  window.__domovoiContrastMark = mark;
  window.__domovoiContrastUnmark = unmark;
  window.__domovoiContrastRatio = (a, b) => ratio(parse(a), parse(b));
}());
