"""Source-level regression checks for the no-build dashboard (web/static).

The dashboard is Babel-in-browser JSX with no test runner of its own, so
the findings fixed here are pinned the way test_vendor_excalidraw.py pins
its invariants: read the file, assert the shape. Each check names the
finding and the functional card it came from.

Pure file reads — no DB, no ``requires_db`` — these must never skip.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"


def _src(name: str) -> str:
    """File text with block comments stripped (as dupglobals.js does), so a
    comment explaining a removal cannot satisfy or trip a check."""
    text = (STATIC / name).read_text(encoding="utf-8")
    return re.sub(r"/\*[\s\S]*?\*/", "", text)


def _component(src: str, name: str) -> str:
    """The text of a top-level `const Name = (...) => {` up to the next
    top-level `const`, enough to look inside one component."""
    m = re.search(rf"^const {name} = .*?(?=^const )", src, re.MULTILINE | re.DOTALL)
    assert m, f"{name} not found"
    return m.group(0)


# ── F-007 · SH-10 ─────────────────────────────────────────────────────
# The topbar carried a tab-focusable role="button" reading "search
# anything ⌘K" with no handler, no shortcut and no search behind it. Until
# a command palette exists the shell must not advertise one.

def test_topbar_has_no_inert_search_affordance():
    topbar = _component(_src("components.jsx"), "Topbar")
    assert 'className="cmdk"' not in topbar
    assert "search anything" not in topbar
    assert "⌘K" not in topbar
    # No other focusable non-button decoration crept in either.
    assert 'role="button"' not in topbar


def test_cmdk_styles_went_with_the_control():
    assert ".cmdk" not in _src("styles.css")


# ── F-008 · SH-12 ─────────────────────────────────────────────────────
# Renaming this device PATCHed /api/devices/{id} and updated only the text
# box: the Known-devices table and both access pickers each held their own
# useApiList('/api/devices') that nothing refreshed (no devices channel on
# the state bus). One list, owned by the tab, refreshed after the rename.

def test_devices_tab_shares_one_devices_list():
    src = _src("settings.jsx")
    calls = [m.start() for m in re.finditer(r"useApiList\('/api/devices'\)", src)]
    assert len(calls) == 1, f"expected one /api/devices list, found {len(calls)}"
    panel = _component(src, "DevicesPanel")
    assert "useApiList('/api/devices')" in panel
    for card in ("QueueAccessCard", "FilesAccessCard"):
        body = _component(src, card)
        assert "useApiList('/api/devices')" not in body, f"{card} fetches its own list"
        assert "deviceList" in body, f"{card} does not read the shared list"


def test_rename_refreshes_the_shared_devices_list():
    src = _src("settings.jsx")
    card = _component(src, "ThisDeviceCard")
    rename = card.index("await DeviceIdentity.rename(")
    catch = card.index("} catch (e) {", rename)
    assert "onRenamed()" in card[rename:catch], "rename success path does not refresh"
    panel = _component(src, "DevicesPanel")
    assert re.search(r"<ThisDeviceCard [^>]*onRenamed=\{deviceList\.refresh\}", panel)


# ── F-012 · SET-07 ────────────────────────────────────────────────────
# ConfigPanel never read `error` from useApiObject('/api/config/editable'),
# so a 502 (core down) or a 401 fell through to an empty group list under
# a live "no changes / Save" footer — "this Domovoi has no settings".

def test_config_panel_names_a_failed_load_and_offers_retry():
    src = _src("settings.jsx")
    panel = _component(src, "ConfigPanel")
    assert re.search(r"const \{[^}]*\berror\b[^}]*\} = useApiObject\('/api/config/editable'\)", panel)
    assert "configLoadMessage(error)" in panel
    # The retry goes through the hook's own refresh.
    assert re.search(r"onClick=\{retry\}", panel)
    assert re.search(r"const retry = async \(\) => \{\s*setRetrying\(true\);\s*try \{ await refresh\(\); \}", panel)
    # No Save footer until something loaded.
    assert re.search(r"\{fields\.length > 0 && \(\s*<div[^\n]*\n(?:.*\n)*?.*'Save'", panel)


def test_config_load_message_keeps_login_and_unreachable_apart():
    src = _src("settings.jsx")
    fn = _component(src, "configLoadMessage")
    assert "error.status === 401 || error.status === 403" in fn
    assert "admin login required" in fn
    assert "error.status === 502" in fn
    assert "domovoi unreachable" in fn
    assert "apiErrorText(error)" in fn      # the residual case shows the body
