"""The user manual's content file: in sync with its source, every link
real, every "try saying" phrase routed where the manual says.

``web/static/manual-content.json`` is what the dashboard (#manual) and the
Android app render; ``scripts/manual/build_manual.py`` makes it from
``scripts/manual/manual.md``. A manual that names a Settings tab that
moved, links a doc heading that was renamed, or offers a phrase another
handler now takes is wrong in front of a household, and nothing else
would notice. So:

* the file is exactly what the generator makes from the source;
* it parses, every piece has a page, nothing links a missing piece;
* every route exists: dashboard pages (index.html's core routes),
  Settings tabs (settings.jsx SETTINGS_TABS), Music tabs, and the app's
  screens and Settings tabs (Routes.kt, SettingsScreen.kt);
* every "More detail" doc exists, and so does its heading;
* every fast-path phrase reaches its handler through the router's own
  decision (``plan_route``: no model, no database). LLM-routed phrases
  are marked in the file and only checked for not being taken by another
  handler's fast path; a Q&A phrase must reach no fast path at all;
  pending phrases (behaviour another branch is building) must not route
  yet, and fail here once they do, so their mark comes off.

Pure file reads and the router's pure planner: no DB, never skips.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from domovoi.handlers import HANDLER_BY_NAME
from domovoi.router import plan_route

REPO = Path(__file__).resolve().parents[2]
CONTENT = REPO / "web" / "static" / "manual-content.json"
STATIC = REPO / "web" / "static"
ANDROID = REPO / "android" / "app" / "src" / "main" / "java" / "com" / "domovoi" / "app"


def _generator():
    spec = importlib.util.spec_from_file_location("build_manual", REPO / "scripts" / "manual" / "build_manual.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GEN = _generator()
DOC = json.loads(CONTENT.read_text(encoding="utf-8"))


def _pages() -> list[dict]:
    return [DOC["start"], *DOC["pieces"]]


def _phrases() -> list[dict]:
    return [p for pg in _pages() for b in pg["blocks"] if b["type"] == "say" for p in b["phrases"]]


def _all_text() -> str:
    return json.dumps(DOC, ensure_ascii=False)


# ── the file and its source ───────────────────────────────────────────────


def test_the_content_file_is_what_the_source_makes() -> None:
    made = GEN.render(GEN.build())
    assert CONTENT.read_bytes() == made, (
        "web/static/manual-content.json is out of date: run python scripts/manual/build_manual.py"
    )


def test_the_generator_refuses_a_phrase_with_no_routing_claim(tmp_path) -> None:
    src = (REPO / "scripts" / "manual" / "manual.md").read_text(encoding="utf-8")
    bad = tmp_path / "manual.md"
    bad.write_text(src.replace('- "never mind"', '- "never mind"\n- "open the pod bay doors"'), encoding="utf-8")
    with pytest.raises(GEN.SourceError, match="open the pod bay doors"):
        GEN.build(bad)


def test_the_generator_refuses_an_unlinked_settings_path(tmp_path) -> None:
    src = (REPO / "scripts" / "manual" / "manual.md").read_text(encoding="utf-8")
    bad = tmp_path / "manual.md"
    bad.write_text(src.replace("Wait for the greeting", "See Settings → Voices. Wait for the greeting"),
                   encoding="utf-8")
    with pytest.raises(GEN.SourceError, match="outside a link"):
        GEN.build(bad)


# ── shape ─────────────────────────────────────────────────────────────────


def test_it_parses_and_every_piece_has_a_page() -> None:
    assert DOC["schema"] == "domovoi-manual/2"
    pieces = {p["id"]: p for p in DOC["pieces"]}
    assert len(pieces) == len(DOC["pieces"]), "duplicate piece ids"
    topics = {t["id"]: t for t in DOC["topics"]}
    assert DOC["centre"]["opens"] in topics and topics[DOC["centre"]["opens"]].get("centre")
    for t in DOC["topics"]:
        assert 1 <= len(t["pieces"]) <= 7, t["id"]
        for pid in t["pieces"]:
            assert pid in pieces, f"{t['id']} lists a missing piece {pid}"
            assert pieces[pid]["topic"] == t["id"]
    for p in DOC["pieces"]:
        assert p["title"] and p["subtitle"] and p["keywords"], p["id"]
        assert p["blocks"], f"{p['id']} has no page"
        if "alias_of" in p:
            assert p["alias_of"] in pieces
    assert {p["id"] for p in DOC["pieces"]} == {pid for t in DOC["topics"] for pid in t["pieces"]}


def test_nothing_links_a_missing_piece() -> None:
    ids = {p["id"] for p in DOC["pieces"]}
    linked = set(re.findall(r"\]\(node:([a-z0-9-]+)\)", _all_text()))
    assert linked and linked <= ids, sorted(linked - ids)


def test_the_checklist_ticks_from_known_signals() -> None:
    known = {"admin_claimed", "internet_answered", "rooms", "room_heard", "people", "music", "phone_app"}
    cl = DOC["start"]["checklist"]
    assert len(cl) == 7
    for item in cl:
        assert item["done_when"] in known
        assert set(item["platforms"]) <= {"dashboard", "app"}
    assert any(b["type"] == "checklist" for b in DOC["start"]["blocks"])


def test_the_live_slots_are_known() -> None:
    slots = {b["slot"] for pg in _pages() for b in pg["blocks"] if b["type"] == "live"}
    assert slots == {"things-to-say", "rooms", "plugins", "wake-word"}


def test_the_wake_word_is_hey_jarvis_out_of_the_box() -> None:
    """A v1.0.0 room answers to hey_jarvis (owner, 2026-10-17): the manual
    must name that, never a word a fresh install doesn't answer to."""
    ww = DOC["wake_word"]
    assert ww["default"] == "hey_jarvis" and ww["names"]["hey_jarvis"] == "Hey Jarvis"
    assert "Domovoi" not in json.dumps(ww)
    assert "{wake}" in json.dumps(DOC["start"])


def test_the_wake_word_page_explains_training_your_own() -> None:
    page = next(p for p in DOC["pieces"] if p["id"] == "wake-word")
    text = json.dumps(page, ensure_ascii=False)
    for needed in ("route:settings/wake-words", "22 GB", "32 GB", "20 minutes", "over an hour",
                   "15 or more clips", "**Train**", "**Push to room**", "household only",
                   "doc:docs/LICENSES.md#the-wake-word-trainer"):
        assert needed in text, needed
    start = json.dumps(DOC["start"], ensure_ascii=False)
    assert "node:wake-word" in start, "Start here points at training your own word"


# ── routes ────────────────────────────────────────────────────────────────


def _core_pages() -> set[str]:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    block = re.search(r"window\.DomovoiCore\.pages = \{(.*?)\n    \};", html, re.S).group(1)
    return set(re.findall(r"^\s+(\w+): \(\) => window\.", block, re.M))


def _settings_slugs() -> set[str]:
    src = (STATIC / "settings.jsx").read_text(encoding="utf-8")
    block = re.search(r"const SETTINGS_TABS = \[(.*?)\];", src, re.S).group(1)
    return {slug or tid for tid, slug in re.findall(r"\{ id: '([\w-]+)'(?:, slug: '([\w-]+)')?", block)}


def _music_tabs() -> set[str]:
    src = (STATIC / "music.jsx").read_text(encoding="utf-8")
    block = re.search(r"const MUSIC_PAGE_TABS = \[(.*?)\];", src, re.S).group(1)
    return set(re.findall(r"id: '([\w-]+)'", block))


def _app_screens() -> set[str]:
    src = (ANDROID / "ui" / "shell" / "Routes.kt").read_text(encoding="utf-8")
    block = re.search(r"enum class Route\(.*?\) \{(.*?)\n\}", src, re.S).group(1)
    return set(re.findall(r"^\s+(\w+)\(\"", block, re.M))


def _app_settings_tabs() -> set[str]:
    src = (ANDROID / "ui" / "screens" / "settings" / "SettingsScreen.kt").read_text(encoding="utf-8")
    block = re.search(r"enum class SettingsTab\(.*?\) \{(.*?)\n\}", src, re.S).group(1)
    return set(re.findall(r"^\s+\w+\(\"([\w-]+)\",", block, re.M))


def test_every_dashboard_route_exists() -> None:
    pages, slugs, music = _core_pages(), _settings_slugs(), _music_tabs()
    assert "manual" in pages
    assert {"about", "voices", "wake-words", "configuration"} <= slugs  # the parse found them
    for rid, r in DOC["routes"].items():
        if r["dashboard"] is None:
            assert rid.startswith("app/"), f"{rid}: only the app's own routes may lack a dashboard page"
            continue
        parts = r["dashboard"].lstrip("#").split("/")
        assert parts[0] in pages, f"{rid}: no dashboard page #{parts[0]}"
        if parts[0] == "settings":
            assert len(parts) == 2 and parts[1] in slugs, f"{rid}: no Settings tab {parts[1:]}"
        elif parts[0] == "music" and len(parts) > 1:
            assert parts[1] in music, f"{rid}: no Music tab {parts[1]}"
        else:
            assert len(parts) == 1, rid


def test_every_app_route_exists() -> None:
    screens, tabs = _app_screens(), _app_settings_tabs()
    assert {"Home", "Settings", "Manual"} <= screens and {"about", "satellites"} <= tabs
    for rid, r in DOC["routes"].items():
        app = r["app"]
        if app.get("dashboard"):
            assert r["dashboard"], f"{rid}: the app opens the dashboard, which has no page for it"
            continue
        assert app["screen"] in screens, f"{rid}: no app screen {app['screen']}"
        if "tab" in app:
            assert app["screen"] == "Settings" and app["tab"] in tabs, f"{rid}: no app Settings tab {app['tab']}"


def test_every_settings_tab_a_link_names_is_one_the_dashboard_has() -> None:
    """The manual's "Settings → X" links say the tab's label: it must be
    that tab's label in settings.jsx (the app's own Settings tabs for app/
    routes)."""
    src = (STATIC / "settings.jsx").read_text(encoding="utf-8")
    block = re.search(r"const SETTINGS_TABS = \[(.*?)\];", src, re.S).group(1)
    label_of = {(slug or tid): label for tid, slug, label in re.findall(
        r"\{ id: '([\w-]+)'(?:, slug: '([\w-]+)')?, label: '([^']+)'", block)}
    app_src = (ANDROID / "ui" / "screens" / "settings" / "SettingsScreen.kt").read_text(encoding="utf-8")
    app_label = dict(re.findall(r"^\s+\w+\(\"([\w-]+)\", \"([^\"]+)\"", app_src, re.M))
    for label, rid in re.findall(r"\[Settings → ([^\]→]+?)(?: →[^\]]*)?\]\(route:([a-z0-9/-]+)\)", _all_text()):
        r = DOC["routes"][rid]
        if rid.startswith("settings/"):
            assert label_of[rid.split("/", 1)[1]] == label, (label, rid)
        elif rid.startswith("app/"):
            assert app_label[r["app"]["tab"]] == label, (label, rid)


# ── doc links ─────────────────────────────────────────────────────────────


def _github_anchors(md: str) -> set[str]:
    """GitHub's heading anchors: lower case, punctuation dropped, spaces
    to hyphens, repeats numbered."""
    seen: dict[str, int] = {}
    out = set()
    in_code = False
    for ln in md.splitlines():
        if ln.startswith("```"):
            in_code = not in_code
        if in_code:
            continue
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", ln)
        if not m:
            continue
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", m.group(1))
        text = re.sub(r"[`*_]", "", text).lower()
        slug = re.sub(r"[^\w\- ]", "", text).replace(" ", "-")
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        out.add(slug if n == 0 else f"{slug}-{n}")
    return out


def test_every_doc_link_and_heading_exists() -> None:
    targets = set(re.findall(r"\]\(doc:([^)]+)\)", _all_text()))
    targets |= {p["more"]["target"][4:] for p in _pages() if "more" in p}
    assert len(targets) > 30
    for t in sorted(targets):
        path, _, anchor = t.partition("#")
        f = REPO / path
        assert f.is_file(), f"doc link to a missing file: {t}"
        if anchor:
            assert anchor in _github_anchors(f.read_text(encoding="utf-8")), f"doc link to a missing heading: {t}"


# ── phrases ───────────────────────────────────────────────────────────────

PHRASES = [p for p in _phrases() if not p.get("then")]


@pytest.mark.parametrize("p", PHRASES, ids=[p["text"] for p in PHRASES])
def test_the_phrase_reaches_the_handler_the_manual_claims(p) -> None:
    said = p.get("try", p["text"])
    plan = plan_route(said)
    winner = plan.handler.name if plan else None
    if p.get("pending"):
        # Behaviour another branch is building: it must not route yet.
        if winner is not None:
            pytest.fail(f"{said!r} now routes to {winner!r} by its fast path: check the claim "
                        f"({p['handler']!r}) and drop its pending mark in scripts/manual/build_manual.py")
        pytest.xfail(f"waits for {p['pending']}")
    if p.get("plugin"):
        if p["handler"] not in HANDLER_BY_NAME:
            assert winner is None, f"a core fast path ({winner!r}) took the {p['plugin']} plugin's phrase {said!r}"
            pytest.skip(f"the {p['plugin']} plugin's handler; its own tests route it")
    if p["path"] == "fast":
        assert winner == p["handler"], f"{said!r} routes to {winner!r}, the manual says {p['handler']!r}"
    elif p["path"] == "llm":
        # Marked: the tool model picks it. No other handler's fast path may take it.
        assert p["handler"] in HANDLER_BY_NAME
        assert winner in (None, p["handler"]), f"{winner!r}'s fast path takes {said!r}"
    else:
        assert p["path"] == "qa" and p["handler"] is None
        assert winner is None, f"{winner!r}'s fast path takes the question {said!r}"


def test_every_claimed_core_handler_exists() -> None:
    for p in PHRASES:
        if p.get("pending") or p.get("plugin") or p["handler"] is None:
            continue
        assert p["handler"] in HANDLER_BY_NAME, p


def test_the_home_hint_phrase_works_on_a_fresh_install() -> None:
    """Home's first-run hint shows Start here's first phrase (web home.jsx,
    the app's HomeModels.kt). On a fresh install it must work: a core
    handler's fast path that needs no internet and no room or library."""
    first = next(b for b in DOC["start"]["blocks"] if b["type"] == "say")["phrases"][0]
    assert first["path"] == "fast" and not first.get("pending") and not first.get("plugin")
    handler = HANDLER_BY_NAME[first["handler"]]
    assert handler.requires_network == "no"
    assert plan_route(first["text"]).handler.name == first["handler"] == "timer"
    home = (STATIC / "home.jsx").read_text(encoding="utf-8")
    assert f"'{first['text']}'" in home, "home.jsx's fallback is Start here's first phrase"
    models = (ANDROID / "ui" / "screens" / "home" / "HomeModels.kt").read_text(encoding="utf-8")
    assert f'"{first["text"]}"' in models


# ── the feature table's offline fallback ──────────────────────────────────


def test_the_static_label_table_names_every_core_handler() -> None:
    """GET /api/capabilities/manual falls back to the web's static label
    table when the core is down and nothing is cached; a handler missing
    from it vanishes from the manual's feature list (music_alias did)."""
    from domovoi.handlers import HANDLERS
    from web.backend.api.capabilities import CORE_HANDLER_DISPLAY

    table = {h["name"]: (h["label"], h["tone"]) for h in CORE_HANDLER_DISPLAY}
    core = {h.name: (h.display.label, h.display.tone) for h in HANDLERS}
    assert table == core
