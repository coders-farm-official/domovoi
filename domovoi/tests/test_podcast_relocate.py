"""Podcast episodes moved into the Windows Podcasts folder (2026-11-11;
domovoi/podcast_relocate.py).

provision.sh copies the episodes from the core's default folder into the
mounted /srv/media/podcasts and only then points PODCASTS_DIR there. The
core, at its next start, points each row's absolute file_path at the copy
(copying one that finished downloading after provision.sh's copy), and
removes an old copy only when the new folder has the same bytes and no row
names it any more. Only rows under the old DEFAULT folder are touched.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi import podcast_relocate as pr
from domovoi.tests.conftest import requires_db


@pytest.fixture
def folders(tmp_path, monkeypatch):
    old = tmp_path / "home" / ".domovoi" / "podcasts"
    base = tmp_path / "srv" / "media"
    new = base / "podcasts"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    monkeypatch.setattr(pr, "MOUNT_BASE", base.as_posix())
    monkeypatch.setattr(pr, "_is_mount", lambda p: Path(p) == new)
    return old, new


def test_only_where_the_episodes_moved(folders, tmp_path, monkeypatch) -> None:
    old, new = folders
    assert pr._dirs(str(new), str(old)) == (old, new)
    # A podcasts_dir that is no media mount (a Linux server's own choice).
    assert pr._dirs(str(tmp_path / "pods"), str(old)) is None
    # Still the default folder: nothing moved.
    assert pr._dirs(str(old), str(old)) is None
    # The Windows folder not connected: the empty mount point is not it.
    monkeypatch.setattr(pr, "_is_mount", lambda p: False)
    assert pr._dirs(str(new), str(old)) is None


async def _episode(session, sub_id: int, idx: int, fp: str | None, status: str = "downloaded") -> int:
    row = (await session.execute(text(
        """INSERT INTO podcast_episodes (subscription_id, guid, title, published_at, download_status, file_path)
           VALUES (:s, :g, :t, :p, :st, :fp) RETURNING id"""),
        {"s": sub_id, "g": f"g{idx}", "t": f"ep{idx}", "p": datetime(2026, 1, idx + 1, tzinfo=timezone.utc),
         "st": status, "fp": fp},
    )).first()
    return int(row[0])


async def _paths(session) -> dict[str, str | None]:
    rows = (await session.execute(text("SELECT title, file_path FROM podcast_episodes ORDER BY id"))).all()
    return {r[0]: r[1] for r in rows}


@requires_db
@pytest.mark.asyncio
async def test_rows_follow_the_copies_and_old_copies_go(db_session, folders, tmp_path) -> None:
    old, new = folders
    sub = int((await db_session.execute(text(
        "INSERT INTO podcast_subscriptions (feed_url, title, keep_n) VALUES ('http://feed/x', 'Show', 5) RETURNING id"
    ))).first()[0])
    # Copied by provision.sh, identical.
    (old / "show").mkdir()
    (new / "show").mkdir()
    (old / "show" / "a-1.mp3").write_bytes(b"episode a")
    (new / "show" / "a-1.mp3").write_bytes(b"episode a")
    # Downloaded into the old folder after provision.sh's copy.
    (old / "show" / "late-2.mp3").write_bytes(b"late episode")
    # A file the household has at that name in the new folder: not the copy.
    (old / "show" / "clash-3.mp3").write_bytes(b"the episode")
    (new / "show" / "clash-3.mp3").write_bytes(b"someone else's file")
    # A row outside the old default folder: never touched, never copied.
    elsewhere = tmp_path / "secret.txt"
    elsewhere.write_bytes(b"not an episode")
    # Identical bytes, but no row: still an old copy with a copy, removed.
    (old / "stray.mp3").write_bytes(b"s")
    (new / "stray.mp3").write_bytes(b"s")
    await _episode(db_session, sub, 0, str(old / "show" / "a-1.mp3"))
    await _episode(db_session, sub, 1, str(old / "show" / "late-2.mp3"))
    await _episode(db_session, sub, 2, str(old / "show" / "clash-3.mp3"))
    await _episode(db_session, sub, 3, str(elsewhere))
    await _episode(db_session, sub, 4, None, status="pending")
    await db_session.commit()

    got = await pr.relocate_episodes(str(new), old_dir=str(old))

    assert got == {"rows": 2, "removed": 3, "kept": 1}
    paths = await _paths(db_session)
    assert paths["ep0"] == str(new / "show" / "a-1.mp3")
    assert paths["ep1"] == str(new / "show" / "late-2.mp3")
    assert (new / "show" / "late-2.mp3").read_bytes() == b"late episode"
    assert paths["ep2"] == str(old / "show" / "clash-3.mp3"), "not its copy: the row stays on the old file"
    assert paths["ep3"] == str(elsewhere) and not (new / "secret.txt").exists()
    assert paths["ep4"] is None
    # Old copies: removed only where identical and unused.
    assert not (old / "show" / "a-1.mp3").exists() and not (old / "show" / "late-2.mp3").exists()
    assert not (old / "stray.mp3").exists()
    assert (old / "show" / "clash-3.mp3").read_bytes() == b"the episode"
    assert (new / "show" / "clash-3.mp3").read_bytes() == b"someone else's file"
    assert not list(new.rglob("*.domovoi-copy"))

    # A second start: nothing more to do, nothing changed.
    again = await pr.relocate_episodes(str(new), old_dir=str(old))
    assert again == {"rows": 0, "removed": 0, "kept": 1}
    assert await _paths(db_session) == paths


@requires_db
@pytest.mark.asyncio
async def test_a_copy_that_differs_is_never_a_reason_to_delete(db_session, folders) -> None:
    old, new = folders
    sub = int((await db_session.execute(text(
        "INSERT INTO podcast_subscriptions (feed_url, title, keep_n) VALUES ('http://feed/y', 'Show', 5) RETURNING id"
    ))).first()[0])
    (old / "a.mp3").write_bytes(b"0123456789")
    (new / "a.mp3").write_bytes(b"0123456780")  # same size, other bytes
    await _episode(db_session, sub, 0, str(old / "a.mp3"))
    await db_session.commit()
    got = await pr.relocate_episodes(str(new), old_dir=str(old))
    assert got == {"rows": 0, "removed": 0, "kept": 1}
    assert (old / "a.mp3").read_bytes() == b"0123456789"
    assert (await _paths(db_session))["ep0"] == str(old / "a.mp3"), "not its copy: the row stays"


@pytest.mark.asyncio
async def test_nothing_happens_on_a_linux_server(tmp_path, monkeypatch) -> None:
    old = tmp_path / "old"
    old.mkdir()
    (old / "a.mp3").write_bytes(b"a")
    assert await pr.relocate_episodes(str(tmp_path / "pods"), old_dir=str(old)) is None
    assert (old / "a.mp3").exists()
