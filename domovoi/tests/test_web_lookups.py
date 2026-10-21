"""The dashboard's lookup pieces (W1): load order, the Search settings slot,
and the chip's three states, checked in Node with the vendored Babel."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "web" / "static"


def test_the_lookup_scripts_load_before_what_uses_them() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('src="chat_lookups.jsx"') < html.index('src="chat.jsx"')
    assert 'src="search_settings.jsx"' in html
    panel = (STATIC / "search_settings.jsx").read_text(encoding="utf-8")
    assert "window.DomovoiSettingsSections.search = { Panel: SearchSettingsPanel }" in panel


def test_the_brave_terms_are_said_plainly() -> None:
    panel = (STATIC / "search_settings.jsx").read_text(encoding="utf-8")
    for words in ("prepaid credit", "AI answers separately", "share your key", "goes only to Brave",
                  "sent to Coders Farm"):
        assert words in panel


_SCRIPT = r"""
const fs = require('fs');
const Babel = require(process.argv[2]);
const src = fs.readFileSync(process.argv[3], 'utf8');
const out = Babel.transform(src, { presets: ['react'] }).code;
const window = {}; const React = { useState: () => [null, () => {}], useCallback: (f) => f, useEffect: () => {} };
const f = new Function('window', 'React', out + '; return ChatLookups;');
const L = f(window, React);
const t = (info, thread) => L.chip(info, thread).label;
console.log(JSON.stringify([
  t({ internet: 'never', always_allow: false }, { internet_allowed: true }),
  t({ internet: 'always', always_allow: true }, { internet_allowed: false }),
  t({ internet: '', always_allow: false }, { internet_allowed: true }),
  t({ internet: '', always_allow: false }, { internet_allowed: false }),
  L.decided({ stats: { lookup_card: { state: 'pending', query: 'x' } } }, 'chat').stats.lookup_card.state,
]));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_chip_reads_never_always_this_chat_and_off(tmp_path) -> None:
    script = tmp_path / "chip.js"
    script.write_text(_SCRIPT, encoding="utf-8")
    out = subprocess.run(
        ["node", str(script), str(STATIC / "vendor" / "babel" / "babel.min.js"), str(STATIC / "chat_lookups.jsx")],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [
        "internet off", "internet always on", "internet on for this chat", "internet off", "allowed_chat",
    ]
