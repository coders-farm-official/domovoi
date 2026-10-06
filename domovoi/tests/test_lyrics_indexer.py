"""[X1]: the library indexer ignores ``.lrc`` files — a sidecar (any case)
and the temporary files the ``.lrc`` writer uses never become library
tracks (contract §5.5). ``library_indexer.py`` itself is unchanged; this
pins the behaviour the lyrics build relies on.

The first test is DB-free (a fake session records what would be
inserted); the second runs the real insert (``requires_db``).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from domovoi.workers import library_indexer

LRC = "[00:01.00]the lantern hums beside the river door\n"


def _music(tmp_path):
    root = tmp_path / "Music"
    root.mkdir()
    (root / "Lantern Song.mp3").write_bytes(b"")
    (root / "Lantern Song.lrc").write_text(LRC, encoding="utf-8")
    (root / "Glass Harbor.LRC").write_text(LRC, encoding="utf-8")
    (root / ".Lantern Song.lrc.domovoi-0a1b2c3d.tmp").write_text(LRC, encoding="utf-8")
    return root


class _Session:
    def __init__(self) -> None:
        self.inserted: list[str] = []

    async def execute(self, statement, params=None):
        if params and "fp" in params:
            self.inserted.append(params["fp"])
        return SimpleNamespace(rowcount=1)

    async def commit(self) -> None:
        pass


async def test_a_lrc_beside_an_mp3_inserts_one_row(tmp_path, monkeypatch) -> None:
    root = _music(tmp_path)
    session = _Session()

    @asynccontextmanager
    async def scope():
        yield session

    monkeypatch.setattr(library_indexer, "session_scope", scope)
    monkeypatch.setattr(library_indexer.settings, "music_dir", str(root))
    counts = await library_indexer.index_music_dir()
    assert counts["scanned"] == 1 and counts["inserted"] == 1
    assert [p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in session.inserted] == ["Lantern Song.mp3"]
    # the bounded upload path refuses one too
    session.inserted.clear()
    counts = await library_indexer.index_paths([root / "Lantern Song.lrc"])
    assert counts["errors"] == 1 and session.inserted == []
    assert ".lrc" not in library_indexer._AUDIO_EXTENSIONS


@requires_db
async def test_the_real_index_has_one_row(db_session, tmp_path, monkeypatch) -> None:
    root = _music(tmp_path)
    monkeypatch.setattr(library_indexer.settings, "music_dir", str(root))
    counts = await library_indexer.index_music_dir()
    assert counts["inserted"] == 1
    rows = (await db_session.execute(text("SELECT file_path FROM library_tracks"))).all()
    assert len(rows) == 1 and rows[0][0].endswith("Lantern Song.mp3")
