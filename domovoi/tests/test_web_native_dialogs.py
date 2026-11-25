"""The dashboard asks in its own dialog, never the browser's (1.4.1).

An embedded browser dismisses window.confirm unseen, so "Update Domovoi"
did nothing. Every confirm and prompt goes through askConfirm/askPrompt
(components.jsx), and the Version card re-reads the server when its boot
changes.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "web/static"
NATIVE = re.compile(r"(?<![\w.])(?:window\.)?(?:confirm|alert|prompt)\(")


def _src(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_settings_update_and_channel_flows_use_no_native_dialogs() -> None:
    src = _src("settings.jsx")
    assert not NATIVE.search(src)
    assert "await askConfirm(" in src


def test_no_page_uses_a_native_dialog() -> None:
    bad = []
    for f in STATIC.glob("*.jsx"):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if NATIVE.search(line) and not line.lstrip().startswith(("//", "*", "/*")):
                bad.append(f"{f.name}:{i}")
    assert bad == []


def test_the_in_page_dialogs_are_shared() -> None:
    src = _src("components.jsx")
    assert "const askConfirm" in src and "const askPrompt" in src
    assert "askConfirm, askPrompt" in src      # exported on window


def test_version_card_rereads_after_the_core_restarts() -> None:
    src = _src("settings.jsx")
    assert "const bootKey" in src
    assert "core.started_at" in src
    # a changed boot drops the old check and asks again
    assert "if (!internetOff) check({ quiet: true });" in src
    # while the core is away, the card keeps asking
    assert "if (!coreDown) return undefined;" in src
