"""The lyrics store's settings and wiring (contract §8 [S1], [S2], [S6],
[S9], §14): the four Settings fields, the two PROFILE_DEFAULTS rows, the
two Library FieldSpecs (right after "Look up other names on
MusicBrainz"), ``.env.example`` leaving every value commented, and the two
workers plus the ``"lyrics"`` snapshot key in ``domovoi/main.py``.

The settings of lyric search (``lyrics_search_enabled``,
``lyrics_index_interval_sec``) belong to the query builder and are not
asserted here. DB-free except the last test (the real core lifespan).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domovoi.config import PROFILE_BY_NAME, Settings, settings
from domovoi.config_schema import EDITABLE_FIELDS as FIELD_SPECS
from domovoi.tests.conftest import requires_db

REPO = Path(__file__).resolve().parents[2]


def test_the_defaults() -> None:
    f = Settings.model_fields
    assert f["lyrics_lrclib_enabled"].default is False        # opt-in; off while unanswered
    assert f["lyrics_write_lrc"].default is True
    assert f["lyrics_scan_interval_sec"].default == 300.0
    assert f["lyrics_fetch_interval_sec"].default == 5.0


def test_the_settings_block_follows_the_alias_fetch_interval() -> None:
    lines = (REPO / "domovoi" / "config.py").read_text(encoding="utf-8").splitlines()
    i = lines.index("    music_alias_fetch_interval_sec: float = 30.0")
    assert lines[i + 1].startswith("    # ─── Lyrics (V021;")
    block = lines[i + 1:i + 20]
    assert "    lyrics_lrclib_enabled: bool = False" in block
    assert "    lyrics_fetch_interval_sec: float = 5.0" in block


def test_the_two_profile_rows() -> None:
    for name, label in (("lyrics_lrclib_enabled", "Synced lyrics (LRCLIB)"),
                        ("lyrics_write_lrc", "Save lyrics as .lrc files")):
        pd = PROFILE_BY_NAME[name]
        assert (pd.label, pd.always, pd.sometimes, pd.never, pd.applies, pd.requires) == (
            label, True, True, False, "live", "")
    text = (REPO / "domovoi" / "config.py").read_text(encoding="utf-8")
    assert "LRCLIB synced lyrics: not built yet" not in text


@pytest.mark.parametrize("answer, value", [("always", True), ("sometimes", True), ("never", False)])
def test_the_answer_sets_both_and_a_hand_set_value_wins(answer, value) -> None:
    s = Settings(_env_file=None, internet_access=answer)
    assert s.lyrics_lrclib_enabled is value and s.lyrics_write_lrc is value
    pinned = Settings(_env_file=None, internet_access=answer, lyrics_lrclib_enabled=not value)
    assert pinned.lyrics_lrclib_enabled is (not value)


def test_the_two_fieldspecs_sit_right_after_the_musicbrainz_one() -> None:
    names = [f.name for f in FIELD_SPECS]
    i = names.index("music_alias_fetch_enabled")
    assert names[i + 1:i + 3] == ["lyrics_lrclib_enabled", "lyrics_write_lrc"]
    assert names[i + 3] == "music_match_play_threshold"
    specs = {f.name: f for f in FIELD_SPECS}
    lrclib, write = specs["lyrics_lrclib_enabled"], specs["lyrics_write_lrc"]
    assert (lrclib.label, lrclib.group, lrclib.type, lrclib.tier, lrclib.section) == (
        "Synced lyrics from LRCLIB", "Library", "bool", "hot", "common")
    assert (write.label, write.group, write.type, write.tier, write.section) == (
        "Save lyrics as .lrc files", "Library", "bool", "hot", "common")
    assert lrclib.needs_internet and write.needs_internet
    assert "lrclib.net" in lrclib.help and "title, artist, album" in lrclib.help
    assert "never changes or deletes a .lrc you made" in write.help
    # the cadences are env-tunable only ([S4])
    assert "lyrics_scan_interval_sec" not in specs and "lyrics_fetch_interval_sec" not in specs


def test_env_example_documents_every_value_commented() -> None:
    lines = (REPO / "domovoi" / ".env.example").read_text(encoding="utf-8").splitlines()
    i = lines.index("# MUSIC_ALIAS_FETCH_INTERVAL_SEC=30")
    assert lines[i + 1].startswith("# ─── Lyrics ")
    for line in ("# LYRICS_LRCLIB_ENABLED=false", "# LYRICS_WRITE_LRC=true",
                 "# LYRICS_SCAN_INTERVAL_SEC=300", "# LYRICS_FETCH_INTERVAL_SEC=5"):
        assert line in lines, line
    assert not [l for l in lines if l.strip().startswith("LYRICS_")]     # nothing pinned


def test_main_registers_the_workers_after_the_alias_fetch_and_reports_status() -> None:
    src = (REPO / "domovoi" / "main.py").read_text(encoding="utf-8")
    alias = src.index('    WORKERS.add_worker(LibraryAliasFetcher(), owner="core")')
    scan = src.index('    WORKERS.add_worker(LyricsScanner(), owner="core")')
    fetch = src.index('    WORKERS.add_worker(LyricsFetcher(), owner="core")')
    assert alias < scan < fetch
    assert '        "lyrics": lyrics_status(),' in src
    assert "    from domovoi.lyrics.status import lyrics_status" in src


def test_the_worker_classes_declare_their_shape() -> None:
    from domovoi.workers.lyrics_fetch import LyricsFetcher
    from domovoi.workers.lyrics_scan import LyricsScanner

    for cls, name, interval in ((LyricsScanner, "lyrics_scan", "lyrics_scan_interval_sec"),
                                (LyricsFetcher, "lyrics_fetch", "lyrics_fetch_interval_sec")):
        assert cls.name == name and cls.interval_setting == interval
        assert cls.enabled_setting is None and cls.stub_suppressed is True
        assert cls.requires_online is False
        assert hasattr(settings, interval)


@requires_db
async def test_the_core_snapshot_and_worker_order(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from domovoi.main import app
    from domovoi.plugins_runtime.workers import WORKERS

    monkeypatch.setattr(settings, "lyrics_lrclib_enabled", False)
    async with app.router.lifespan_context(app):
        names = [w["name"] for w in WORKERS.status("core")["workers"]]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/v1/admin/snapshot")
    assert r.status_code == 200, r.text
    lyrics = r.json()["lyrics"]
    assert set(lyrics) == {"scan", "fetch"}
    assert lyrics["fetch"]["enabled"] is False and lyrics["fetch"]["state"] == "off"
    i = names.index("library_alias_fetch")
    assert names[i + 1:i + 3] == ["lyrics_scan", "lyrics_fetch"]
