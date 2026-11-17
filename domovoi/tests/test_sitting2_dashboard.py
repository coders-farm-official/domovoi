"""Dashboard fixes from the Mac test sitting (2026-11-16), pinned in the
page source (the pages' scripts compile in test_web_script_scope.py).

* S2-6/S2-8: the Models page speaks to households: no "writes config",
  "restart-tier", "VRAM", "Ollama pulls" or a named graphics card, and the
  catalog no longer calls large-v3 "the current default" or pins a card.
* S2-7: the wake-word summary promises training only where the Wake words
  page would (a trainer status without ``requirements.unsupported``).
* S2-14: an offline room shows no song and no counting bar.
* S2-17: Health reads the server rows every ten seconds and on return.
* S2-12: a room missing system packages shows its own note, not the
  network settings' "reboot" text.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "web" / "static"


def _src(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_the_models_page_has_no_developer_words():
    src = _src("models.jsx")
    for jargon in ("Switching writes config", "restart-tier", "resident in VRAM",
                   "Curated Ollama", "Ollama pulls", "nvidia-smi", "the denominator",
                   "openWakeWord models", "Whisper needs a restart", "spills to CPU"):
        assert jargon not in src, jargon
    assert "A change that only works after Domovoi restarts is marked on its row." in src


def test_the_catalog_names_no_card_and_no_wrong_default():
    cat = json.loads((REPO / "web" / "backend" / "model_catalog.json").read_text(encoding="utf-8"))
    text = json.dumps(cat)
    assert "4070" not in text and "NVIDIA" not in text and "the current default" not in text


def test_the_wake_word_summary_follows_the_trainer():
    src = _src("models.jsx")
    assert "useApiObject('/api/wake-words/trainer'" in src
    assert "wakeTrainer.requirements.unsupported" in src
    assert "sub={wakeTrainable" in src
    assert "this server can't train them" in src


def test_an_offline_room_shows_no_running_song():
    src = _src("satellites.jsx")
    assert "const playing = online && np?.state === 'play' && np.song;" in src
    assert "offline · its music isn’t reaching it" in src
    assert "const playing = roomOnline && np?.state === 'play' && np.song;" in src
    # The count never runs past the song's end either.
    assert "Math.min(ran, songDur)" in src


def test_health_reads_the_server_rows_on_its_own():
    src = _src("health.jsx")
    assert "const HEALTH_SERVER_POLL_MS = 10 * 1000;" in src
    assert "setInterval(() => { if (!hidden()) health.refresh(); }, HEALTH_SERVER_POLL_MS)" in src
    assert "addEventListener('visibilitychange', onShow)" in src


def test_a_packages_note_is_shown_as_itself():
    src = _src("satellites.jsx")
    assert "satPackagesNote(s) ? 'system packages missing' : 'reboot needed'" in src
    assert "s.reboot_needed && !satPackagesNote(s)" in src
