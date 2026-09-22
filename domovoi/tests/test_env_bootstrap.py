"""``python -m domovoi.env_bootstrap`` — the fresh-install ``.env`` writer (OPS-1).

A fresh checkout gets a ``.env`` whose Postgres credential is random and
consistent between ``DATABASE_URL`` and ``POSTGRES_PASSWORD``; an existing
``.env`` is never touched. All on ``tmp_path`` — the repo's real ``.env``
(the dev install) is never read or written here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from domovoi import env_bootstrap
from domovoi.env_bootstrap import DEFAULT_PASSWORD, ensure_env_file, render_fresh_env

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / "domovoi" / ".env.example"


def _kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


@pytest.fixture
def fresh(tmp_path):
    """A checkout with the real ``.env.example`` and no ``.env``."""
    example = tmp_path / ".env.example"
    example.write_bytes(ENV_EXAMPLE.read_bytes())
    return tmp_path / ".env", example


def _no_volume() -> bool:
    return False


def _volume_present() -> bool:
    return True


# ─── fresh install ────────────────────────────────────────────────────────

def test_fresh_bootstrap_writes_a_random_non_default_postgres_password(fresh) -> None:
    env_path, example = fresh
    result = ensure_env_file(env_path, example, volume_exists=_no_volume)

    assert result.created and result
    assert not result.reused_default
    kv = _kv(env_path.read_text(encoding="utf-8"))
    pw = kv["POSTGRES_PASSWORD"]
    assert pw != DEFAULT_PASSWORD
    assert re.fullmatch(r"[A-Za-z0-9_-]{32}", pw), pw


def test_fresh_bootstrap_keeps_database_url_and_postgres_password_in_step(fresh) -> None:
    """The core connects with DATABASE_URL; compose initialises Postgres with
    POSTGRES_PASSWORD. They must be the same secret or first boot fails."""
    env_path, example = fresh
    ensure_env_file(env_path, example, volume_exists=_no_volume)

    kv = _kv(env_path.read_text(encoding="utf-8"))
    pw = kv["POSTGRES_PASSWORD"]
    assert kv["DATABASE_URL"] == f"postgresql+asyncpg://domovoi:{pw}@localhost:6432/domovoi"
    # The commented TEST_DATABASE_URL example is rewritten too, so
    # uncommenting it later gives a working DSN.
    text = env_path.read_text(encoding="utf-8")
    assert f"# TEST_DATABASE_URL=postgresql+asyncpg://domovoi:{pw}@localhost:6432/domovoi_test" in text
    assert "domovoi:domovoi@" not in text


def test_fresh_bootstrap_documents_rotation(fresh) -> None:
    env_path, example = fresh
    ensure_env_file(env_path, example, volume_exists=_no_volume)
    text = env_path.read_text(encoding="utf-8")
    assert "Rotating the Postgres password" in text
    assert "docs/LINUX_HOST.md" in text


def test_fresh_bootstrap_preserves_every_other_example_line(fresh) -> None:
    """The bootstrap is ``.env.example`` plus a credential — the example's
    defaults (e.g. TTS_ENGINE) arrive untouched, in their original order."""
    env_path, example = fresh
    ensure_env_file(env_path, example, volume_exists=_no_volume)

    written = env_path.read_text(encoding="utf-8").splitlines()
    original = example.read_text(encoding="utf-8").splitlines()
    dsn = re.compile(r"^\s*#?\s*(TEST_)?DATABASE_URL=")
    kept = [l for l in written[: len(original)] if not dsn.match(l)]
    expected = [l for l in original if not dsn.match(l)]
    assert kept == expected
    assert _kv("\n".join(written))["TTS_ENGINE"] == _kv("\n".join(original))["TTS_ENGINE"]


def test_fresh_bootstrap_keeps_the_example_line_endings(fresh) -> None:
    env_path, example = fresh
    ensure_env_file(env_path, example, volume_exists=_no_volume)
    assert env_path.read_bytes().count(b"\r\n") == example.read_bytes().count(b"\r\n")


@pytest.mark.skipif(not hasattr(__import__("os"), "getuid"), reason="POSIX file modes")
def test_fresh_bootstrap_is_owner_only(fresh) -> None:
    env_path, example = fresh
    ensure_env_file(env_path, example, volume_exists=_no_volume)
    assert env_path.stat().st_mode & 0o777 == 0o600


# ─── existing install ─────────────────────────────────────────────────────

def test_existing_env_is_left_byte_for_byte_untouched(fresh) -> None:
    """The dev install and the appliance both have a ``.env`` already; the
    bootstrap must not merge, reorder or re-encode it — and must not add a
    password that the existing volume was never initialised with."""
    env_path, example = fresh
    before = b"# operator notes, CRLF on purpose\r\nDATABASE_URL=postgresql+asyncpg://domovoi:domovoi@localhost:6432/domovoi\r\nTTS_ENGINE=piper\r\n"
    env_path.write_bytes(before)
    mtime = env_path.stat().st_mtime_ns

    result = ensure_env_file(env_path, example, volume_exists=_no_volume)

    assert not result.created and not result
    assert env_path.read_bytes() == before
    assert env_path.stat().st_mtime_ns == mtime
    assert "POSTGRES_PASSWORD" not in before.decode()


def test_existing_env_without_postgres_password_still_matches_compose_fallback(fresh) -> None:
    """What keeps an old install working: its ``.env`` has no
    POSTGRES_PASSWORD, compose falls back to the template default, and the
    DSN in that file carries the same default."""
    env_path, example = fresh
    env_path.write_text(
        "DATABASE_URL=postgresql+asyncpg://domovoi:domovoi@localhost:6432/domovoi\n",
        encoding="utf-8",
    )
    ensure_env_file(env_path, example, volume_exists=_no_volume)
    kv = _kv(env_path.read_text(encoding="utf-8"))
    assert "POSTGRES_PASSWORD" not in kv
    assert f"domovoi:{DEFAULT_PASSWORD}@" in kv["DATABASE_URL"]


def test_missing_env_beside_an_existing_volume_keeps_the_default_credential(fresh) -> None:
    """A re-clone next to a database that was initialised earlier: a random
    password here would not open that volume. Keep the default, say so, and
    point at rotation."""
    env_path, example = fresh
    result = ensure_env_file(env_path, example, volume_exists=_volume_present)

    assert result.created and result.reused_default
    kv = _kv(env_path.read_text(encoding="utf-8"))
    assert kv["POSTGRES_PASSWORD"] == DEFAULT_PASSWORD
    assert f"domovoi:{DEFAULT_PASSWORD}@" in kv["DATABASE_URL"]
    assert "Rotating the Postgres password" in env_path.read_text(encoding="utf-8")


def test_second_run_is_a_no_op(fresh) -> None:
    env_path, example = fresh
    first = ensure_env_file(env_path, example, volume_exists=_no_volume)
    snapshot = env_path.read_bytes()
    second = ensure_env_file(env_path, example, volume_exists=_no_volume)
    assert first.created and not second.created
    assert env_path.read_bytes() == snapshot


# ─── module-level defaults and the CLI ────────────────────────────────────

def test_default_paths_point_at_the_package_env_files() -> None:
    assert env_bootstrap.ENV_FILE == REPO_ROOT / "domovoi" / ".env"
    assert env_bootstrap.ENV_EXAMPLE == ENV_EXAMPLE
    assert ENV_EXAMPLE.is_file()


def test_render_refuses_a_template_without_the_default_credential() -> None:
    with pytest.raises(ValueError):
        render_fresh_env("TTS_ENGINE=piper\n", "x")


def test_cli_reports_and_never_fails(fresh, monkeypatch, capsys) -> None:
    env_path, example = fresh
    monkeypatch.setattr(env_bootstrap, "ENV_FILE", env_path)
    monkeypatch.setattr(env_bootstrap, "ENV_EXAMPLE", example)
    monkeypatch.setattr(env_bootstrap, "_pgdata_volume_exists", _no_volume)

    assert env_bootstrap.main() == 0
    assert "created" in capsys.readouterr().out
    assert env_bootstrap.main() == 0
    assert "left untouched" in capsys.readouterr().out
