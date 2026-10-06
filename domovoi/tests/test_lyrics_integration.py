"""The lyrics build after its four parts were merged (lyrics-build CONTRACT
§16): the checks no single builder could make on its own branch.

* Settings → Configuration → Library lists the three lyrics switches in
  their places among the music ones (B2's "Find songs by their words",
  B1's two LRCLIB rows).
* ``domovoi/main.py``'s worker-order comment names the workers in the
  order they are really registered (B1 and B2 each added theirs).
* The Android models read every field the web's lyrics routes send, under
  the same JSON names (B3 serves, B4 reads).

DB-free. Every lyric in this file is invented.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

from domovoi.config_schema import EDITABLE_FIELDS

REPO = Path(__file__).resolve().parents[2]
MAIN = REPO / "domovoi" / "main.py"
LYRICS_KT = (REPO / "android" / "app" / "src" / "main" / "java" / "com" / "domovoi"
             / "app" / "player" / "Lyrics.kt")


def test_the_library_group_lists_the_music_switches_in_order() -> None:
    labels = [f.label for f in EDITABLE_FIELDS if f.group == "Library"]
    wanted = [
        "Match names by sound",
        "Ask 'did you mean…?'",
        "Find songs by their words",
        "Look up other names on MusicBrainz",
        "Synced lyrics from LRCLIB",
        "Save lyrics as .lrc files",
    ]
    start = labels.index(wanted[0])
    assert labels[start:start + len(wanted)] == wanted


def test_only_the_lrclib_rows_need_the_internet() -> None:
    by_name = {f.name: f for f in EDITABLE_FIELDS}
    assert by_name["lyrics_search_enabled"].needs_internet is False
    assert by_name["lyrics_lrclib_enabled"].needs_internet is True
    assert by_name["lyrics_write_lrc"].needs_internet is True


def _lifespan_source() -> str:
    src = MAIN.read_text(encoding="utf-8")
    start = src.index("async def lifespan(")
    end = src.index("# ── Boot-time startup hooks", start)
    return src[start:end]


def test_the_worker_order_comment_matches_the_registrations() -> None:
    body = _lifespan_source()
    comment = re.search(
        r"# Registration order is the canonical start order.*?\n((?:\s*#.*\n)+?)\s*#\s*\n",
        body, re.S,
    )
    assert comment, "the worker-order comment is gone"
    said = re.findall(r"[a-z_]+", comment.group(1).replace("#", " "))
    # Every class registered in the lifespan, in order, and the module it
    # comes from (module-level or local imports, both in main.py).
    src = MAIN.read_text(encoding="utf-8")
    modules = dict(
        (cls, mod) for mod, cls in
        re.findall(r"from (domovoi\.workers\.\w+) import (\w+)", src)
    )
    registered = re.findall(r"WORKERS\.add_worker\((\w+)\(", body)
    names = [getattr(importlib.import_module(modules[cls]), cls).name for cls in registered]
    assert said == names
    assert names[-3:] == ["library_alias_fetch", "lyrics_scan", "lyrics_fetch"]
    assert names[names.index("memory_extractor") + 1] == "lyrics_index"


def _kotlin_fields(cls: str) -> set[str]:
    """The JSON names of a Kotlin @Serializable data class's constructor
    properties (``@SerialName`` when given, else the property name)."""
    src = LYRICS_KT.read_text(encoding="utf-8")
    m = re.search(r"data class " + cls + r"\(", src)
    assert m, f"no data class {cls} in Lyrics.kt"
    depth, i = 1, m.end()
    while depth:                      # the constructor's own closing paren
        depth += {"(": 1, ")": -1}.get(src[i], 0)
        i += 1
    params = src[m.end():i - 1]
    return {
        prop.group(2) or prop.group(3)
        for prop in re.finditer(
            r'(@Transient\s+)?(?:@SerialName\("([^"]+)"\)\s*)?va[lr]\s+(\w+)', params)
        if not prop.group(1)          # @Transient: never in the JSON
    }


def test_the_android_models_read_what_the_web_sends() -> None:
    from web.backend.schemas import LyricLine, LyricsDoc, RoomLyrics

    line = _kotlin_fields("LyricLine")
    assert line == set(LyricLine.model_fields)
    doc = _kotlin_fields("LyricsDoc")
    # The app has no use for updated_at; every other field is read.
    assert doc == set(LyricsDoc.model_fields) - {"updated_at"}
    room = _kotlin_fields("RoomLyrics")
    # read_at is informational (clients anchor on their own receipt time).
    assert room == set(RoomLyrics.model_fields) - {"read_at"}
