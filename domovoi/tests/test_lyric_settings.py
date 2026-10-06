"""Lyric search's settings and its place in the core (lyrics-build
contract [S3]–[S5], [Q2], [Q4], §14 main.py).

* ``lyrics_search_enabled`` (on) and ``lyrics_index_interval_sec`` (10 s);
* the one editable field, "Find songs by their words", in the Library
  group right after "Ask 'did you mean…?'", hot, not internet-gated; the
  interval is env-tunable only;
* main.py registers the index worker right after the memory extractor and
  puts its status in the core snapshot as ``lyrics_index`` — read from the
  source, so this never needs the whole app (and never skips).
"""

from __future__ import annotations

import ast
from pathlib import Path

import domovoi
from domovoi.config import Settings
from domovoi.config_schema import EDITABLE_FIELDS
from domovoi.workers.lyrics_index import LyricsIndexer, lyrics_index_status

MAIN = Path(domovoi.__file__).parent / "main.py"


def test_the_defaults() -> None:
    s = Settings()
    assert s.lyrics_search_enabled is True
    assert s.lyrics_index_interval_sec == 10.0


def test_find_songs_by_their_words_sits_right_after_did_you_mean() -> None:
    names = [f.name for f in EDITABLE_FIELDS]
    i = names.index("lyrics_search_enabled")
    assert names[i - 1] == "music_choice_enabled"
    spec = EDITABLE_FIELDS[i]
    assert (spec.label, spec.group, spec.type, spec.tier, spec.section) == (
        "Find songs by their words", "Library", "bool", "hot", "common")
    assert spec.needs_internet is False                    # it never leaves the house
    assert spec.help.startswith("Play or name a song from words in its lyrics:")
    assert spec.help.endswith("Applies immediately.")


def test_the_interval_is_env_tunable_only() -> None:
    assert "lyrics_index_interval_sec" not in {f.name for f in EDITABLE_FIELDS}


def test_the_worker_reads_its_interval_live() -> None:
    w = LyricsIndexer()
    assert w.interval_setting == "lyrics_index_interval_sec"
    assert hasattr(Settings(), w.interval_setting)
    assert w.enabled_setting is None           # runs while search is off: switching on is instant


def _main_tree() -> ast.Module:
    return ast.parse(MAIN.read_text(encoding="utf-8"))


def _func(name: str) -> ast.AST:
    return next(n for n in ast.walk(_main_tree())
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def test_main_registers_the_index_worker_after_the_memory_extractor() -> None:
    fn = _func("lifespan")
    added = [
        n.args[0].func.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "add_worker"
        and n.args and isinstance(n.args[0], ast.Call) and isinstance(n.args[0].func, ast.Name)
    ]
    assert "LyricsIndexer" in added
    assert added.index("LyricsIndexer") == added.index("MemoryExtractor") + 1


def test_the_core_snapshot_carries_the_index_status() -> None:
    fn = _func("admin_snapshot")
    ret = next(n for n in ast.walk(fn) if isinstance(n, ast.Return))
    assert isinstance(ret.value, ast.Dict)
    keys = [k.value for k in ret.value.keys if isinstance(k, ast.Constant)]
    assert "lyrics_index" in keys
    value = ret.value.values[keys.index("lyrics_index")]
    assert isinstance(value, ast.Call) and value.func.id == "lyrics_index_status"


def test_the_status_is_in_memory_and_says_whether_search_is_on(monkeypatch) -> None:
    from domovoi.config import settings

    st = lyrics_index_status()
    assert set(st) == {"state", "pending", "indexed", "last_tick_at", "last_error", "search_enabled"}
    monkeypatch.setattr(settings, "lyrics_search_enabled", False)
    assert lyrics_index_status()["search_enabled"] is False
