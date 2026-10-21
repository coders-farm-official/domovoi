"""Kept honest: everything a person can meet has help (part 9, item 8).

A new Settings page, voice feature or dashboard error code without help
text fails here, so the help desk never meets something it can only guess
about:

* every Settings page of the dashboard has a manual route and a manual
  page that links to it;
* every core voice handler has a "try saying" phrase in the manual that
  reaches it;
* every error code the dashboard can be shown has an entry in
  ``domovoi/help_desk/error_codes.py``, pointing at a real manual page;
* every plugin in this repository (Coders Farm's own) ships a help file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC = json.loads((REPO / "web/static/manual-content.json").read_text(encoding="utf-8"))


def _manual_text() -> str:
    return json.dumps(DOC, ensure_ascii=False)


def test_every_settings_page_has_help() -> None:
    src = (REPO / "web/static/settings.jsx").read_text(encoding="utf-8")
    block = re.search(r"const SETTINGS_TABS = \[(.*?)\];", src, re.S).group(1)
    tabs = re.findall(r"\{ id: '([\w-]+)'(?:, slug: '([\w-]+)')?, label: '([^']+)'", block)
    assert len(tabs) >= 10
    linked = set(re.findall(r"route:(settings/[a-z0-9-]+)", _manual_text()))
    missing = []
    for tid, slug, label in tabs:
        route = f"settings/{slug or tid}"
        if route not in DOC["routes"] or route not in linked:
            missing.append(f"{label} ({route})")
    assert not missing, f"Settings pages with no help in the manual: {missing}"


def test_every_voice_handler_has_help() -> None:
    from domovoi.handlers import HANDLERS

    said: set[str] = set()
    for piece in [DOC["start"], *DOC["pieces"]]:
        for block in piece.get("blocks") or []:
            if block.get("type") == "say":
                said |= {p.get("handler") for p in block["phrases"]}
    missing = [h.name for h in HANDLERS if getattr(h, "plugin_slug", None) is None and h.name not in said]
    assert not missing, f"voice handlers with no 'try saying' in the manual: {missing}"


def _scan(pattern: str, *globs: str) -> set[str]:
    out: set[str] = set()
    for g in globs:
        for f in REPO.glob(g):
            if "tests" in f.parts:
                continue
            out |= set(re.findall(pattern, f.read_text(encoding="utf-8", errors="ignore")))
    return out


def dashboard_error_codes() -> set[str]:
    """Every code the dashboard can be shown, from the code that sends them."""
    codes: set[str] = set()
    # The plugin installer's {error: {code}}.
    codes |= _scan(r'InstallError\(\s*"([a-z_]+)"', "domovoi/**/*.py", "web/backend/**/*.py")
    # A chat reply's stats.error_code.
    codes |= _scan(r'(?m)^ERR_[A-Z_]+ = "([a-z_]+)"', "web/backend/api/chat.py")
    # Coded JSON answers: {"detail": ..., "code": "..."}.
    codes |= _scan(r'"code":\s*"([a-z_-]+)"', "web/backend/api/*.py")
    # Picture-upload refusals, words of a music name's refusal.
    from web.backend.api.chat import UPLOAD_REFUSAL_CODES

    codes |= set(UPLOAD_REFUSAL_CODES)
    codes |= _scan(r'code="([a-z_]+)"', "domovoi/db/library_aliases.py")
    # Refusal headers (X-Domovoi-Refusal).
    from domovoi import ai_features, egress

    codes |= {ai_features.REFUSAL_VALUE, egress.REFUSAL_VALUE}
    return codes


def test_the_scan_finds_the_codes_it_should() -> None:
    codes = dashboard_error_codes()
    for known in ("ai-off", "internet-off", "model_missing", "vision_model_missing", "zip_invalid",
                  "unsupported_format", "core_unreachable", "alias_taken", "too_long"):
        assert known in codes


def test_every_dashboard_error_code_has_help() -> None:
    from domovoi.help_desk.error_codes import ERROR_CODES

    missing = sorted(dashboard_error_codes() - set(ERROR_CODES))
    assert not missing, f"error codes with no help entry in domovoi/help_desk/error_codes.py: {missing}"
    pieces = {p["id"] for p in DOC["pieces"]}
    for code, entry in ERROR_CODES.items():
        assert entry.piece in pieces, (code, entry.piece)
        assert entry.title and entry.text.endswith((".", ")")), code


def test_our_own_plugins_ship_a_help_file() -> None:
    from domovoi.plugins_runtime.manifest import parse_manifest_dir

    plugins = [d for d in (REPO / "plugins").iterdir() if (d / "domovoi-plugin.toml").is_file()]
    assert plugins
    for d in plugins:
        m = parse_manifest_dir(d)
        assert m.help, f"{d.name}: Coders Farm's own plugins must declare [plugin] help"
        assert (d / m.help).is_file()
