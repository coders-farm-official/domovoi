"""Existing installs move off the Piper voices Domovoi no longer offers
(domovoi/voice_migration.py): the configured voice, and the model files;
and once from Norman, the house voice until 2026-10-12, to Kristin."""

from __future__ import annotations

import pytest

from domovoi import voice_migration
from domovoi.clients.tts import (
    DEFAULT_PIPER_VOICE,
    PREVIOUS_DEFAULT_PIPER_VOICE,
    RETIRED_PIPER_VOICES,
    SHIPPED_VOICES,
)
from domovoi.config import settings


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch):
    for k in [k for k in __import__("os").environ if k.upper() == "TTS_PIPER_VOICE"]:
        monkeypatch.delenv(k)


def test_the_default_is_kristin():
    """The owner's pick, 2026-10-12 (Norman before)."""
    assert DEFAULT_PIPER_VOICE == "en_US-kristin-medium"
    assert PREVIOUS_DEFAULT_PIPER_VOICE == "en_US-norman-medium"
    assert type(settings).model_fields["tts_piper_voice"].default == DEFAULT_PIPER_VOICE


def test_a_retired_voice_in_the_env_file_moves_to_the_default(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("# mine\nTTS_ENGINE=piper\nTTS_PIPER_VOICE=en_US-lessac-medium\nBOT_NAME=Domovoi\n",
                   encoding="utf-8")
    monkeypatch.setattr(settings, "tts_piper_voice", "en_US-lessac-medium")
    assert voice_migration.migrate_configured_voice(env) == "en_US-lessac-medium"
    assert settings.tts_piper_voice == DEFAULT_PIPER_VOICE
    assert env.read_text(encoding="utf-8") == (
        "# mine\nTTS_ENGINE=piper\nTTS_PIPER_VOICE=en_US-kristin-medium\nBOT_NAME=Domovoi\n")


@pytest.mark.parametrize("voice", [v.model_ref for v in SHIPPED_VOICES] + ["my-own-upload", "/srv/v/mine.onnx"])
def test_a_voice_still_offered_or_the_owners_own_is_kept(monkeypatch, tmp_path, voice):
    env = tmp_path / ".env"
    env.write_text(f"TTS_PIPER_VOICE={voice}\n", encoding="utf-8")
    monkeypatch.setattr(settings, "tts_piper_voice", voice)
    assert voice_migration.migrate_configured_voice(env) is None
    assert settings.tts_piper_voice == voice
    assert env.read_text(encoding="utf-8") == f"TTS_PIPER_VOICE={voice}\n"


def test_a_retired_voice_from_the_code_default_writes_nothing(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("TTS_ENGINE=piper\n", encoding="utf-8")
    monkeypatch.setattr(settings, "tts_piper_voice", "en_US-amy-medium")
    assert voice_migration.migrate_configured_voice(env) == "en_US-amy-medium"
    assert settings.tts_piper_voice == DEFAULT_PIPER_VOICE
    assert env.read_text(encoding="utf-8") == "TTS_ENGINE=piper\n"


def test_an_environment_variable_is_not_rewritten_but_the_voice_still_moves(monkeypatch, tmp_path, caplog):
    env = tmp_path / ".env"
    env.write_text("TTS_PIPER_VOICE=en_US-ryan-high\n", encoding="utf-8")
    monkeypatch.setenv("TTS_PIPER_VOICE", "en_US-ryan-high")
    monkeypatch.setattr(settings, "tts_piper_voice", "en_US-ryan-high")
    assert voice_migration.migrate_configured_voice(env) == "en_US-ryan-high"
    assert settings.tts_piper_voice == DEFAULT_PIPER_VOICE
    assert env.read_text(encoding="utf-8") == "TTS_PIPER_VOICE=en_US-ryan-high\n"
    assert any("set in the environment" in r.getMessage() for r in caplog.records)


def test_only_the_retired_voice_files_in_the_voices_folder_go(tmp_path):
    vdir = tmp_path / "piper_voices"
    vdir.mkdir()
    keep = ["en_US-norman-medium.onnx", "en_US-norman-medium.onnx.json", "mine.onnx", "mine.onnx.json",
            "en_US-lessac-medium.onnx.bak", "notes.txt"]
    gone = ["en_US-lessac-medium.onnx", "en_US-lessac-medium.onnx.json", "en_US-ryan-high.onnx"]
    for n in keep + gone:
        (vdir / n).write_bytes(b"x")
    outside = tmp_path / "en_US-amy-medium.onnx"
    outside.write_bytes(b"x")
    sub = vdir / "sub"
    sub.mkdir()
    (sub / "en_US-amy-medium.onnx").write_bytes(b"x")

    removed = voice_migration.remove_retired_voice_files(vdir)

    assert sorted(removed) == sorted(gone)
    assert sorted(p.name for p in vdir.iterdir() if p.is_file()) == sorted(keep)
    assert outside.exists() and (sub / "en_US-amy-medium.onnx").exists()
    assert voice_migration.remove_retired_voice_files(vdir) == []
    assert voice_migration.remove_retired_voice_files(tmp_path / "missing") == []


def test_every_retired_voice_was_a_catalog_voice_and_none_is_shipped():
    assert not RETIRED_PIPER_VOICES & {v.model_ref for v in SHIPPED_VOICES}


# ─── Review fixes (2026-10-12): never leave the box without a neural voice ──


def _retired_and_kept(vdir):
    vdir.mkdir(parents=True, exist_ok=True)
    for n in ("en_US-lessac-medium.onnx", "en_US-lessac-medium.onnx.json"):
        (vdir / n).write_bytes(b"old voice")


def test_old_files_stay_until_a_shipped_voice_is_on_disk(tmp_path):
    vdir = tmp_path / "piper_voices"
    _retired_and_kept(vdir)
    (vdir / "en_US-norman-medium.onnx").write_bytes(b"half")      # no config yet: not a voice
    assert voice_migration.remove_retired_voice_files(vdir) == []
    assert (vdir / "en_US-lessac-medium.onnx").exists()
    (vdir / "en_US-norman-medium.onnx.json").write_text("{}", encoding="utf-8")
    assert sorted(voice_migration.remove_retired_voice_files(vdir)) == [
        "en_US-lessac-medium.onnx", "en_US-lessac-medium.onnx.json"]


def test_a_failed_download_of_the_new_voice_leaves_the_old_file(monkeypatch, tmp_path):
    """Boot order on a box whose link is down: the setting moves, the new
    voice's fetch fails, and the old voice file is still there afterwards."""
    from domovoi import egress
    from domovoi.clients import tts

    vdir = tmp_path / "piper_voices"
    _retired_and_kept(vdir)
    monkeypatch.setattr(settings, "voice_models_dir", str(vdir))
    monkeypatch.setattr(settings, "tts_piper_voice", "en_US-lessac-medium")
    monkeypatch.setattr(tts, "_piper_voice_cache", {})

    def boom():
        raise OSError("Hugging Face is down")

    monkeypatch.setattr(tts, "_piper_http_client", boom)
    voice_migration.migrate_configured_voice(tmp_path / "no.env")
    with egress.override_policy("always"):
        with pytest.raises(Exception):
            tts._piper_voice_path(settings.tts_piper_voice)       # the fetch fails
    assert voice_migration.remove_retired_voice_files() == []
    assert (vdir / "en_US-lessac-medium.onnx").read_bytes() == b"old voice"
    assert not any(vdir.glob(f"{DEFAULT_PIPER_VOICE}*"))


def test_a_link_is_never_followed_or_removed(monkeypatch, tmp_path):
    vdir = tmp_path / "piper_voices"
    _retired_and_kept(vdir)
    for n in ("en_US-norman-medium.onnx", "en_US-norman-medium.onnx.json"):
        (vdir / n).write_bytes(b"x")
    link = vdir / "en_US-lessac-medium.onnx"
    real_is_symlink = type(link).is_symlink
    monkeypatch.setattr(type(link), "is_symlink", lambda self: self.name == link.name or real_is_symlink(self))
    removed = voice_migration.remove_retired_voice_files(vdir)
    assert removed == ["en_US-lessac-medium.onnx.json"]
    assert link.exists()


def test_a_file_that_resolves_outside_the_folder_is_left_alone(monkeypatch, tmp_path):
    vdir = tmp_path / "piper_voices"
    _retired_and_kept(vdir)
    for n in ("en_US-norman-medium.onnx", "en_US-norman-medium.onnx.json"):
        (vdir / n).write_bytes(b"x")
    escapee = (vdir / "en_US-lessac-medium.onnx").resolve()
    outside = tmp_path / "elsewhere" / "en_US-lessac-medium.onnx"
    real_resolve = type(escapee).resolve
    monkeypatch.setattr(type(escapee), "resolve",
                        lambda self, *a, **k: outside if self == escapee else real_resolve(self, *a, **k))
    removed = voice_migration.remove_retired_voice_files(vdir)
    assert removed == ["en_US-lessac-medium.onnx.json"]
    assert (vdir / "en_US-lessac-medium.onnx").exists()


@pytest.mark.asyncio
async def test_the_boot_runs_the_migration_and_the_file_removal(monkeypatch):
    from domovoi.tests.conftest import _DB_OK

    if not _DB_OK:
        pytest.skip("the core's lifespan needs Postgres")
    from domovoi import main

    calls: list[str] = []
    monkeypatch.setattr(voice_migration, "migrate_configured_voice", lambda *a, **k: calls.append("migrate"))
    monkeypatch.setattr(voice_migration, "remove_retired_voice_files", lambda *a, **k: calls.append("remove") or [])
    async with main.app.router.lifespan_context(main.app):
        pass
    assert calls == ["migrate", "remove"]


# ─── Norman to Kristin, once (owner, 2026-10-12) ────────────────────────


class _Repo:
    """What move_default_voice asks of the registry, in memory."""

    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]

    async def all(self):
        return [dict(r) for r in self.rows]

    async def get_default(self):
        return next((dict(r) for r in self.rows if r["is_default"]), None)

    async def create(self, *, name, engine, model_ref, is_default=False):
        rid = max((r["id"] for r in self.rows), default=0) + 1
        self.rows.append({"id": rid, "name": name, "engine": engine, "model_ref": model_ref,
                          "is_default": is_default})
        return rid

    async def set_default(self, vid):
        for r in self.rows:
            r["is_default"] = r["id"] == vid
        return True


NORMAN = {"id": 1, "name": "Norman", "engine": "piper", "model_ref": "en_US-norman-medium", "is_default": True}
KRISTIN = {"id": 2, "name": "Kristin", "engine": "piper", "model_ref": "en_US-kristin-medium", "is_default": False}


@pytest.fixture
def move(monkeypatch, tmp_path):
    """A voices folder (Kristin on disk only when a test puts her there), the
    marker in a temporary config dir, and an .env file."""
    from domovoi import admin_auth
    from domovoi.clients import tts

    vdir = tmp_path / "piper_voices"
    vdir.mkdir()
    monkeypatch.setattr(settings, "voice_models_dir", str(vdir))
    monkeypatch.setattr(tts, "_piper_voice_cache", {})
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path / "home" / ".domovoi")
    env = tmp_path / ".env"
    env.write_text("TTS_ENGINE=piper\nTTS_PIPER_VOICE=en_US-norman-medium\n", encoding="utf-8")
    monkeypatch.setattr(settings, "tts_piper_voice", "en_US-norman-medium")

    class _M:
        def kristin_on_disk(self):
            for n in ("en_US-kristin-medium.onnx", "en_US-kristin-medium.onnx.json"):
                (vdir / n).write_bytes(b"x")

    m = _M()
    m.env = env
    m.marker = tmp_path / "home" / ".domovoi" / voice_migration.DEFAULT_MOVE_MARKER
    return m


async def test_norman_moves_to_kristin_once_she_is_on_disk(move):
    repo = _Repo([NORMAN, KRISTIN])
    # Not yet on disk: Norman keeps speaking, and nothing is recorded.
    assert await voice_migration.move_default_voice(repo, env_path=move.env) == "waiting"
    assert (await repo.get_default())["name"] == "Norman"
    assert settings.tts_piper_voice == "en_US-norman-medium"
    assert not move.marker.exists()

    move.kristin_on_disk()
    assert await voice_migration.move_default_voice(repo, env_path=move.env) == "moved"
    assert (await repo.get_default())["name"] == "Kristin"
    assert settings.tts_piper_voice == "en_US-kristin-medium"
    assert move.env.read_text(encoding="utf-8") == "TTS_ENGINE=piper\nTTS_PIPER_VOICE=en_US-kristin-medium\n"
    assert move.marker.read_text(encoding="utf-8").startswith("moved ")


async def test_a_household_that_picks_norman_again_keeps_him(move):
    move.kristin_on_disk()
    repo = _Repo([NORMAN, KRISTIN])
    assert await voice_migration.move_default_voice(repo, env_path=move.env) == "moved"
    await repo.set_default(1)                       # Settings -> Voices: Norman again
    assert await voice_migration.move_default_voice(repo, env_path=move.env) == "done"
    assert (await repo.get_default())["name"] == "Norman"


async def test_kristins_row_is_added_when_missing(move):
    move.kristin_on_disk()
    repo = _Repo([NORMAN])
    assert await voice_migration.move_default_voice(repo, env_path=move.env) == "moved"
    default = await repo.get_default()
    assert (default["name"], default["model_ref"]) == ("Kristin", "en_US-kristin-medium")


async def test_another_default_is_kept_and_marked(move):
    move.kristin_on_disk()
    linda = {"id": 3, "name": "Linda", "engine": "piper", "model_ref": "en_US-ljspeech-medium", "is_default": True}
    repo = _Repo([dict(NORMAN, is_default=False), linda])
    move.env.write_text("TTS_PIPER_VOICE=en_US-ljspeech-medium\n", encoding="utf-8")
    import domovoi.config as config_mod
    config_mod.settings.tts_piper_voice = "en_US-ljspeech-medium"
    assert await voice_migration.move_default_voice(repo, env_path=move.env) == "kept"
    assert (await repo.get_default())["name"] == "Linda"
    assert move.env.read_text(encoding="utf-8") == "TTS_PIPER_VOICE=en_US-ljspeech-medium\n"
    assert move.marker.read_text(encoding="utf-8").startswith("kept ")
    # A later pick of Norman is not moved either.
    await repo.set_default(1)
    assert await voice_migration.move_default_voice(repo, env_path=move.env) == "done"


async def test_an_unseeded_registry_is_left_for_the_next_boot(move):
    move.kristin_on_disk()
    assert await voice_migration.move_default_voice(_Repo([]), env_path=move.env) == "unseeded"
    assert not move.marker.exists()


async def test_the_boot_fetches_kristin_then_moves(move, monkeypatch):
    """run_default_voice_move: Kristin not on disk -> fetched through the
    egress gate (here a stand-in download), then the move."""
    import contextlib

    from domovoi import egress
    from domovoi.clients import tts
    import domovoi.db.repositories as repos
    import domovoi.db.session as session_mod

    repo = _Repo([NORMAN, KRISTIN])
    fetched = []

    def fetch(ref):
        fetched.append(ref)
        move.kristin_on_disk()

    @contextlib.asynccontextmanager
    async def scope():
        yield None

    monkeypatch.setattr(session_mod, "session_scope", scope)
    monkeypatch.setattr(repos, "VoicesRepository", lambda _s: repo)
    monkeypatch.setattr(tts, "_piper_voice_path", fetch)
    monkeypatch.setattr(voice_migration, "move_default_voice",
                        lambda r, env_path=None, _real=voice_migration.move_default_voice: _real(r, env_path=move.env))
    with egress.override_policy("always"):
        assert await voice_migration.run_default_voice_move() == "moved"
    assert fetched == ["en_US-kristin-medium"]
    assert (await repo.get_default())["name"] == "Kristin"


async def test_under_never_kristin_is_not_fetched_and_norman_stays(move, monkeypatch):
    import contextlib

    from domovoi import egress
    from domovoi.clients import tts
    import domovoi.db.repositories as repos
    import domovoi.db.session as session_mod

    repo = _Repo([NORMAN, KRISTIN])

    @contextlib.asynccontextmanager
    async def scope():
        yield None

    monkeypatch.setattr(session_mod, "session_scope", scope)
    monkeypatch.setattr(repos, "VoicesRepository", lambda _s: repo)
    monkeypatch.setattr(tts, "_piper_voice_path", lambda ref: pytest.fail("no fetch under never"))
    monkeypatch.setattr(voice_migration, "move_default_voice",
                        lambda r, env_path=None, _real=voice_migration.move_default_voice: _real(r, env_path=move.env))
    with egress.override_policy("never"):
        assert await voice_migration.run_default_voice_move() == "waiting"
    assert (await repo.get_default())["name"] == "Norman"
    assert not move.marker.exists()


async def test_a_failed_fetch_leaves_norman_for_the_next_boot(move, monkeypatch):
    import contextlib

    from domovoi import egress
    from domovoi.clients import tts
    import domovoi.db.repositories as repos
    import domovoi.db.session as session_mod

    repo = _Repo([NORMAN, KRISTIN])

    @contextlib.asynccontextmanager
    async def scope():
        yield None

    def down(ref):
        raise OSError("Hugging Face is down")

    monkeypatch.setattr(session_mod, "session_scope", scope)
    monkeypatch.setattr(repos, "VoicesRepository", lambda _s: repo)
    monkeypatch.setattr(tts, "_piper_voice_path", down)
    with egress.override_policy("always"):
        assert await voice_migration.run_default_voice_move() == "waiting"
    assert (await repo.get_default())["name"] == "Norman"
    assert not move.marker.exists()
