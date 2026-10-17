"""Contrast audit of a running dashboard, every page and tab, both themes.

    python scripts/dev/contrast_audit.py http://127.0.0.1:6390 OUTDIR \
        [--token HOUSEHOLD_TOKEN] [--password ADMIN_PASSWORD] \
        [--theme dark|light|both] [--phone] [--only home,settings] \
        [--channel msedge|chrome] [--clips 6]

Drives a headless Chromium (Playwright; the installed Edge or Chrome via
--channel, so no browser download) through every page the dashboard knows
(the core pages plus each installed plugin's pages), clicks through each
page's tab strip, and opens the shell's own dialogs (sign-in, server
picker, the phone player sheet where a phone bar is on screen). On each
view it runs scripts/dev/contrast_audit.js, which measures WCAG contrast
of text (4.5:1, large text 3:1), placeholders (4.5:1), icons (3:1) and
disabled controls (3:1) against the colour actually painted behind them.

Writes OUTDIR/report.json (every view's failures), one outlined screenshot
per view that failed (OUTDIR/<theme>-<viewport>/<view>.png, failures
numbered as in the report) and close-ups of the first --clips failures of
each view (…/clips/). Prints a summary grouped by element and exits 1 when
anything failed, so the playbook can run it against a throwaway: point it
at a dashboard with the plugins you care about installed. Never point it at
someone's live install with --password: signing in is real.

Pass --token for a household-paired browser (most pages need it) and
--password to sign in as admin (Settings tabs, the Plugins page).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
AUDIT_JS = HERE / "contrast_audit.js"
SETTLE = 1.6   # seconds for a view's first fetches + lucide's icon pass

VIEWPORTS = {
    "desktop": {"viewport": {"width": 1280, "height": 860}},
    "phone": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True},
}

# Dialogs, drawers and specimens worth a look in every theme:
# (name, route to open it from, JS that opens it -> truthy when it did).
# Escape closes the shell's own dialogs; a specimen (the shell's shared
# components, mounted on their own over the page, with states no fresh
# install shows: toasts, every pill tone, disabled buttons, the delete
# confirmation) is unmounted after its audit.
MOUNT = """(build) => {
  const old = document.getElementById('contrast-specimen');
  if (old) old.remove();
  const host = document.createElement('div');
  host.id = 'contrast-specimen';
  document.body.appendChild(host);
  const h = React.createElement;
  ReactDOM.createRoot(host).render(build(h));
  return true;
}"""
SPECIMENS = {
    "specimen~primitives": """(h) => h('div', { style: { position: 'fixed', inset: 0, zIndex: 120, overflow: 'auto',
                                   background: 'var(--canvas)', padding: 24 } },
      h('div', { className: 'card', style: { padding: 16, display: 'flex', flexWrap: 'wrap', gap: 10 } },
        ...['ok', 'warn', 'err', 'idle'].map((t) => h(Pill, { key: t, tone: t }, t + ' pill')),
        h(Pill, { tone: 'ok', live: true }, 'live pill'),
        h(Button, { variant: 'primary', icon: 'check' }, 'primary'),
        h(Button, { icon: 'x' }, 'secondary'),
        h(Button, { variant: 'ghost', icon: 'play' }, 'ghost'),
        h(Button, { variant: 'primary', icon: 'check', disabled: true }, 'primary disabled'),
        h(Button, { icon: 'x', disabled: true }, 'secondary disabled'),
        h(Button, { variant: 'ghost', icon: 'play', disabled: true }, 'ghost disabled'),
        h('span', { className: 'mono', style: { color: 'var(--fg-faint)' } }, 'faint · 2m ago'),
        h('span', { style: { color: 'var(--fg-subtle)' } }, 'subtle hint'),
        h('a', { href: '#', style: { color: 'var(--brand-ink)' } }, 'a link'),
        h('span', { style: { color: 'var(--err-ink)' } }, 'save failed'),
        h('span', { style: { color: 'var(--warn-ink)' } }, 'unsaved'),
        h('span', { style: { color: 'var(--ok-ink)' } }, 'saved'),
        h('input', { placeholder: 'placeholder text',
                     style: { padding: 6, background: 'var(--card)', border: '1px solid var(--border)',
                              borderRadius: 'var(--r-sm)' } })),
      h('div', { className: 'card', style: { marginTop: 16 } },
        h(Empty, { title: 'nothing here yet', sub: 'an empty state line' })))""",
    "specimen~toasts": """(h) => {
      const T = () => {
        const [fire, node] = useToast();
        React.useEffect(() => {
          fire('saved');
          fire('save failed: the disk is full', { stay: true, detail: 'the server answered 507',
                                                 actions: [{ label: 'retry', onClick() {} }] });
        }, []);
        return node || null;
      };
      return h(T);
    }""",
    "specimen~delete-confirm": """(h) => h(DeleteConfirmDialog, { title: 'Delete kitchen.mp3?', onCancel() {}, onConfirm() {} })""",
    "specimen~delete-busy": """(h) => h(DeleteConfirmDialog, { title: 'Delete kitchen.mp3?', busy: true, onCancel() {}, onConfirm() {} })""",
    "specimen~trust-server": """(h) => h('div', { className: 'card', style: { position: 'fixed', top: 80, left: 300, width: 420,
                                                    zIndex: 120, padding: 16 } },
      h(TrustServerPrompt, { url: 'http://192.168.1.20:6369', name: 'test', onCancel() {}, onConfirm() {} }))""",
}
DIALOGS = [
    ("sign-in", "home", "() => { try { Auth.openModal(); return true; } catch (e) { return false; } }"),
    ("server-picker", "home", "() => { const b = [...document.querySelectorAll('header.topbar button')]"
                               ".find((x) => x.querySelector('.lucide-server')); if (b) b.click(); return !!b; }"),
    ("calendar-new-event", "calendar", "() => { const b = [...document.querySelectorAll('.main button')]"
                                       ".find((x) => x.querySelector('.lucide-plus')); if (b) b.click(); return !!b; }"),
    *[(name, "home", f"() => ({MOUNT})({js})") for name, js in SPECIMENS.items()],
]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "view"


def run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    themes = ["dark", "light"] if args.theme == "both" else [args.theme]
    viewports = ["desktop", "phone"] if args.phone else ["desktop"]
    only = set(args.only.split(",")) if args.only else None
    report: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=args.channel, headless=not args.headed,
                                     args=["--hide-scrollbars"])
        try:
            for theme in themes:
                for vp in viewports:
                    report += audit_context(browser, args, theme, vp, out, only)
        finally:
            browser.close()   # every tab this run opened goes with it

    (out / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return summarise(report)


def audit_context(browser, args, theme: str, vp: str, out: Path, only) -> list[dict]:
    ctx = browser.new_context(color_scheme=theme, **VIEWPORTS[vp])
    init = f"try {{ localStorage.setItem('domovoi-theme', '{theme}'); }} catch (e) {{}}"
    if args.token:
        init += f"; try {{ localStorage.setItem('domovoi-device-token', {json.dumps(args.token)}); }} catch (e) {{}}"
    ctx.add_init_script(init)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    results: list[dict] = []
    folder = out / f"{theme}-{vp}"
    try:
        page.goto(f"{args.base}/#home")
        page.wait_for_selector(".app", timeout=45000)
        time.sleep(SETTLE)
        if args.password:
            ok = page.evaluate("async (pw) => { try { await Auth.login(pw); return true; }"
                               " catch (e) { return String(e); } }", args.password)
            if ok is not True:
                print(f"[{theme}/{vp}] admin sign-in failed: {ok}", file=sys.stderr)
            time.sleep(0.6)
        page.add_script_tag(path=str(AUDIT_JS))
        routes = page.evaluate("""() => {
            const core = Object.keys((window.DomovoiCore && window.DomovoiCore.pages) || {});
            const m = window.DomovoiPluginManifest || { plugins: [] };
            const plug = [];
            (m.plugins || []).forEach((p) => (p.pages || []).forEach((pg) => plug.push(pg.route)));
            return [...core, ...plug];
        }""")
        for route in routes:
            if only and route not in only:
                continue
            go(page, route)
            results.append(audit_view(page, args, folder, f"{route}", theme, vp))
            tabs = page.evaluate("() => [...document.querySelectorAll('.main .tabs-strip')]"
                                 ".map((s) => s.querySelectorAll('button').length)")
            for s, count in enumerate(tabs[:2]):
                for i in range(count):
                    label = page.evaluate(
                        "([s, i]) => { const b = document.querySelectorAll('.main .tabs-strip')[s]"
                        "?.querySelectorAll('button')[i]; if (!b) return null; b.click();"
                        " return (b.getAttribute('aria-label') || b.textContent || '').trim(); }", [s, i])
                    if label is None:
                        break
                    time.sleep(SETTLE)
                    results.append(audit_view(page, args, folder, f"{route}~{label}", theme, vp))
        for name, route, js in DIALOGS:
            if only and name not in only:
                continue
            go(page, route)
            try:
                opened = page.evaluate(js)
            except Exception as e:  # noqa: BLE001 - a dialog that can't open is reported, not fatal
                print(f"[{theme}/{vp}] {name}: {str(e).splitlines()[0]}", file=sys.stderr)
                opened = False
            if not opened:
                print(f"[{theme}/{vp}] {name}: nothing to open here", file=sys.stderr)
                continue
            time.sleep(0.9)
            results.append(audit_view(page, args, folder, f"{'' if '~' in name else 'dialog~'}{name}",
                                      theme, vp))
            page.evaluate("() => { const n = document.getElementById('contrast-specimen');"
                          " if (n) n.remove(); }")
            page.keyboard.press("Escape")
            time.sleep(0.4)
    finally:
        ctx.close()
    if errors:
        print(f"[{theme}/{vp}] page errors: {errors[:5]}", file=sys.stderr)
    return results


def go(page, route: str) -> None:
    page.evaluate("(r) => { window.location.hash = r; }", route)
    time.sleep(SETTLE)
    page.evaluate("() => { const m = document.querySelector('.main'); if (m) m.scrollTop = 0; }")


def snap(page, path: Path, **kw) -> bool:
    """A screenshot that can't stop the run (a page that never settles)."""
    try:
        page.screenshot(path=str(path), timeout=15000, animations="disabled", **kw)
        return True
    except Exception as e:  # noqa: BLE001 - report and carry on
        print(f"  screenshot {path.name} failed: {str(e).splitlines()[0]}", file=sys.stderr)
        return False


def audit_view(page, args, folder: Path, view: str, theme: str, vp: str) -> dict:
    res = page.evaluate("(o) => window.__domovoiContrastAudit(o)",
                        {"text": args.text, "ui": args.ui})
    res.update(view=view, viewport=vp)
    fails = res["failures"]
    if args.shoot_all:
        (folder / "all").mkdir(parents=True, exist_ok=True)
        snap(page, folder / "all" / f"{slug(view)}.png")
    if fails:
        folder.mkdir(parents=True, exist_ok=True)
        name = slug(view)
        page.evaluate("() => window.__domovoiContrastMark()")
        if snap(page, folder / f"{name}.png"):
            res["screenshot"] = str(folder / f"{name}.png")
        page.evaluate("() => window.__domovoiContrastUnmark()")
        clips = folder / "clips"
        clips.mkdir(exist_ok=True)
        size = page.viewport_size
        for i, f in enumerate(fails[: args.clips]):
            r = page.evaluate("(i) => window.__domovoiContrastFocus(i)", i)
            if not r or r["w"] < 1:
                continue
            pad = 14
            x = max(0, r["x"] - pad)
            y = max(0, r["y"] - pad)
            w = min(size["width"] - x, r["w"] + 2 * pad)
            h = min(size["height"] - y, r["h"] + 2 * pad)
            if w < 4 or h < 4:
                continue
            p = clips / f"{name}-{i + 1}.png"
            if snap(page, p, clip={"x": x, "y": y, "width": w, "height": h}):
                f["clip"] = str(p)
    print(f"  {theme:5} {vp:7} {view:40} checked {res['checked']:4}  failed {len(fails):3}"
          f"  unknown {len(res['unknown'])}", flush=True)
    return res


def summarise(report: list[dict]) -> int:
    groups: dict[tuple, list] = defaultdict(list)
    for r in report:
        for f in r["failures"]:
            key = (f["theme"], f["kind"], re.sub(r"\d+", "#", f["path"]), f["fg"], f["bg"])
            groups[key].append((r["view"], f))
    total = sum(len(r["failures"]) for r in report)
    print(f"\n{total} failures in {sum(1 for r in report if r['failures'])} of {len(report)} views; "
          f"{len(groups)} distinct (theme, kind, element, colours)")
    for (theme, kind, path, fg, bg), hits in sorted(groups.items(), key=lambda kv: kv[1][0][1]["ratio"]):
        f = hits[0][1]
        views = sorted({v for v, _ in hits})
        print(f"  {theme:5} {kind:11} {f['ratio']:5.2f}<{f['need']}  {fg} on {bg}  {path}"
              f"  \"{f.get('text', '')[:40]}\"  ({len(hits)}x: {', '.join(views[:4])}"
              f"{'…' if len(views) > 4 else ''})")
    return 1 if total else 0


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # cp1252 consoles
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", help="dashboard URL, e.g. http://127.0.0.1:6390")
    ap.add_argument("out", help="folder for report.json and screenshots")
    ap.add_argument("--token", help="household token to pair the browser with")
    ap.add_argument("--password", help="admin password to sign in with (throwaways only)")
    ap.add_argument("--theme", choices=["dark", "light", "both"], default="both")
    ap.add_argument("--phone", action="store_true", help="also audit at phone width")
    ap.add_argument("--only", help="comma-separated routes / dialog names")
    ap.add_argument("--channel", default="msedge", help="installed browser channel (msedge, chrome)")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shoot-all", action="store_true",
                    help="also screenshot every view, failing or not (…/all/), for before/after records")
    ap.add_argument("--clips", type=int, default=6, help="close-ups per failing view")
    ap.add_argument("--text", type=float, default=4.5, help="text minimum (default 4.5)")
    ap.add_argument("--ui", type=float, default=3.0, help="icon / UI minimum (default 3)")
    ap.add_argument("--summary-only", action="store_true",
                    help="re-print the summary of OUTDIR/report.json without a browser")
    a = ap.parse_args()
    if a.summary_only:
        raise SystemExit(summarise(json.loads((Path(a.out) / "report.json").read_text(encoding="utf-8"))))
    a.base = a.base.rstrip("/")
    raise SystemExit(run(a))


if __name__ == "__main__":
    main()
