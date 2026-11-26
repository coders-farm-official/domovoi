"""The house identity backup and restore (1.5.0, S3-4).

A reinstall used to mint a new server key, and every room set up for the old
one then refused the house. Keeping the key fixes that: a backup from the
dashboard (or ``python -m domovoi.server_identity --write-backup``), given
back to the next install (``install.sh --restore-identity FILE``, Windows
Setup's restore, provision.sh in between).

What these hold it to:

* a backup round-trips: restored, the install signs as the old house, and a
  room pinned to it accepts it;
* a restore validates everything first (format, kind, schema, the key pair,
  a signature) and rejects a bad or tampered file, writing nothing;
* a restore never replaces a DIFFERENT key unless told to, and then retires
  it rather than deleting it;
* the download route: security tier, a password sign-in within 5 minutes,
  the password typed again (behind the login backoff), no-store, the key
  never logged.

The non-DB half runs everywhere; the route half needs the test database.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from domovoi import admin_auth, server_identity
from satellite import server_identity as sat_identity
from satellite.tests.test_server_identity import _health_opener

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _fresh_cache():
    server_identity.reset_cache()
    yield
    server_identity.reset_cache()


def _make(tmp_path: Path, name: str = "server-identity.json") -> server_identity.ServerIdentity:
    return server_identity.load_or_create(tmp_path / name)


def _backup_file(tmp_path: Path, identity, name: str = "backup.json") -> Path:
    p = tmp_path / name
    p.write_bytes(server_identity.backup_bytes(identity))
    return p


# ─── round trip ───────────────────────────────────────────────────────────

def test_backup_and_restore_round_trip_keeps_the_house(tmp_path):
    old_home = tmp_path / "old"
    old = _make(old_home)
    backup = _backup_file(tmp_path, old)
    doc = json.loads(backup.read_text(encoding="utf-8"))
    assert doc["kind"] == server_identity.BACKUP_KIND
    assert doc["fingerprint"] == old.fingerprint
    assert "private key" in doc["note"]

    # A fresh install (the reinstall): restore before anything made a key.
    new_target = tmp_path / "new" / "server-identity.json"
    result = server_identity.restore_identity(backup, new_target)
    assert result["restored"] is True and result["fingerprint"] == old.fingerprint
    restored = server_identity.load_or_create(new_target)
    assert restored.seed == old.seed and restored.fingerprint == old.fingerprint
    if os.name != "nt":
        assert stat.S_IMODE(new_target.stat().st_mode) == 0o600

    # A room pinned to the OLD house accepts the reinstalled one.
    got = sat_identity.verify_server(
        "http://192.168.1.20:6370", expected_fingerprint=old.fingerprint,
        opener=_health_opener(restored.seed, restored.public),
    )
    assert got == old.fingerprint


def test_restoring_the_same_key_again_changes_nothing(tmp_path):
    ident = _make(tmp_path)
    backup = _backup_file(tmp_path, ident)
    before = (tmp_path / "server-identity.json").read_bytes()
    result = server_identity.restore_identity(backup, tmp_path / "server-identity.json")
    assert result["restored"] is False
    assert (tmp_path / "server-identity.json").read_bytes() == before


def test_a_different_key_already_here_is_never_replaced_silently(tmp_path):
    here = _make(tmp_path / "here")
    other = _make(tmp_path / "other")
    backup = _backup_file(tmp_path, other)
    target = tmp_path / "here" / "server-identity.json"
    with pytest.raises(server_identity.IdentityRestoreError) as e:
        server_identity.restore_identity(backup, target)
    assert "--replace-existing" in str(e.value)
    server_identity.reset_cache()
    assert server_identity.load_or_create(target).seed == here.seed

    result = server_identity.restore_identity(backup, target, replace=True, now=1_800_000_000)
    assert result["restored"] and result["replaced"] == here.fingerprint
    retired = Path(result["retired_path"])
    assert retired.exists() and retired.name.startswith("server-identity.json.retired-")
    server_identity.reset_cache()
    assert server_identity.load_or_create(target).seed == other.seed


def test_a_plain_key_file_is_accepted_as_a_backup(tmp_path):
    ident = _make(tmp_path)
    got = server_identity.read_backup(tmp_path / "server-identity.json")
    assert got.seed == ident.seed


# ─── validation rejects a bad file, and writes nothing ────────────────────

def _doc(tmp_path):
    return server_identity.backup_document(_make(tmp_path / "src"))


def _tampered(tmp_path, kind):
    d = _doc(tmp_path)
    other = server_identity.backup_document(_make(tmp_path / "other"))
    if kind == "swapped-public":
        d["public_key"] = other["public_key"]
    elif kind == "swapped-fingerprint":
        d["fingerprint"] = other["fingerprint"]
    elif kind == "short-key":
        d["private_key"] = base64.b64encode(b"x" * 31).decode()
    elif kind == "not-base64":
        d["private_key"] = "!!!"
    elif kind == "wrong-algorithm":
        d["algorithm"] = "rsa"
    elif kind == "unknown-kind":
        d["kind"] = "something-else"
    elif kind == "future-schema":
        d["schema"] = 99
    elif kind == "no-fingerprint":
        del d["fingerprint"]
    elif kind == "satellite-record":
        d = {"kind": "satellite-server-fingerprint", "fingerprint": d["fingerprint"]}
    elif kind == "list":
        d = [d]
    return json.dumps(d).encode()


@pytest.mark.parametrize("kind", [
    "swapped-public", "swapped-fingerprint", "short-key", "not-base64", "wrong-algorithm",
    "unknown-kind", "future-schema", "no-fingerprint", "satellite-record", "list",
])
def test_restore_rejects_a_bad_backup_and_writes_nothing(tmp_path, kind):
    bad = tmp_path / "bad.json"
    bad.write_bytes(_tampered(tmp_path, kind))
    target = tmp_path / "fresh" / "server-identity.json"
    with pytest.raises(server_identity.IdentityRestoreError) as e:
        server_identity.restore_identity(bad, target)
    assert not target.exists()
    # The message never carries the file's key material.
    doc = json.loads(bad.read_text()) if kind != "list" else {}
    if isinstance(doc, dict) and doc.get("private_key"):
        assert doc["private_key"] not in str(e.value)


def test_restore_rejects_garbage_and_huge_files(tmp_path):
    for name, raw in (("garbage", b"\x00\xffnot json"), ("huge", b" " * (server_identity.BACKUP_MAX_BYTES + 1))):
        p = tmp_path / name
        p.write_bytes(raw)
        with pytest.raises(server_identity.IdentityRestoreError):
            server_identity.restore_identity(p, tmp_path / "t" / "server-identity.json")
    with pytest.raises(server_identity.IdentityRestoreError):
        server_identity.restore_identity(tmp_path / "missing.json", tmp_path / "t" / "x.json")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_restore_refuses_a_link(tmp_path):
    ident = _make(tmp_path / "src")
    real = _backup_file(tmp_path, ident)
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(server_identity.IdentityRestoreError):
        server_identity.restore_identity(link, tmp_path / "t" / "server-identity.json")


def test_restore_never_writes_over_a_rooms_record(tmp_path):
    target = tmp_path / "server-identity.json"
    target.write_text(json.dumps({"kind": "satellite-server-fingerprint", "fingerprint": "SHA256:x"}))
    backup = _backup_file(tmp_path, _make(tmp_path / "src"))
    with pytest.raises(server_identity.IdentityRestoreError):
        server_identity.restore_identity(backup, target)
    assert "satellite-server-fingerprint" in target.read_text()


# ─── the CLI provision.sh runs ────────────────────────────────────────────

def _cli(*args, stdin: bytes | None = None):
    return subprocess.run(
        [sys.executable, "-m", "domovoi.server_identity", *args],
        input=stdin, capture_output=True, cwd=REPO_ROOT, timeout=60,
    )


def test_cli_restore_from_stdin_check_and_write(tmp_path):
    src = _make(tmp_path / "src")
    raw = server_identity.backup_bytes(src)
    target = tmp_path / "new" / "server-identity.json"
    r = _cli("--check-backup", "-", stdin=raw)
    assert r.returncode == 0 and src.fingerprint.encode() in r.stdout
    r = _cli("--restore-identity", "-", "--path", str(target), stdin=raw)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith(b"restored")
    # The key never appears in what provision.sh logs.
    assert base64.b64encode(src.seed) not in r.stdout + r.stderr
    r = _cli("--restore-identity", "-", "--path", str(target), stdin=raw)
    assert r.returncode == 0 and r.stdout.startswith(b"unchanged")
    r = _cli("--restore-identity", "-", "--path", str(target), stdin=b"{}")
    assert r.returncode == 1 and b"not restored" in r.stderr
    out = tmp_path / "written.json"
    r = _cli("--write-backup", str(out), "--path", str(target))
    assert r.returncode == 0
    assert server_identity.read_backup(out).fingerprint == src.fingerprint
    r = _cli("--write-backup", str(out), "--path", str(target))
    assert r.returncode == 1          # never over an existing file


def test_provision_and_install_take_the_option():
    prov = (REPO_ROOT / "scripts" / "linux" / "provision.sh").read_text(encoding="utf-8")
    assert "--restore-identity - <\"$RESTORE_IDENTITY\"" in prov
    assert 'if [ "$step" = start ] && [ -n "$RESTORE_IDENTITY" ]; then restore_identity; fi' in prov
    inst = (REPO_ROOT / "scripts" / "linux" / "install.sh").read_text(encoding="utf-8")
    assert " --restore-identity " in inst.split("PASS_VALUE_OPTS=", 1)[1].split("\n", 1)[0]


# ─── the route: tier, freshness, the password again ───────────────────────

from domovoi.tests.auth_testkit import (  # noqa: E402
    STRONG_PW,
    _db,  # noqa: F401 - fixture
    bearer,
    claim_admin,
    core_client,
    web_client,
)
from domovoi.tests.conftest import requires_db  # noqa: E402

RW = {"X-Requested-With": "domovoi-tests"}
CORE = "/v1/admin/server-identity/backup"
WEB = "/api/config/server-identity/backup"


async def _sql(stmt: str, **params):
    from sqlalchemy import text

    from domovoi.db.session import engine

    async with engine.begin() as conn:
        await conn.execute(text(stmt), params)


async def _login() -> str:
    admin_auth.LOGIN_BACKOFF.reset()
    async with web_client() as c:
        r = await c.post("/api/auth/login", json={"password": STRONG_PW, "label": "dashboard"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture
async def admin(_db, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server_identity, "identity_path", lambda: tmp_path / "server-identity.json")
    server_identity.reset_cache()
    async with web_client() as c:
        await claim_admin(c)
    token = await _login()
    yield token
    admin_auth.LOGIN_BACKOFF.reset()


async def _core(token, body):
    headers = dict(RW)
    if token:
        headers.update(bearer(token))
    async with core_client() as c:
        return await c.post(CORE, headers=headers, json=body)


async def _age(token: str, minutes: float) -> None:
    await _sql("UPDATE admin_sessions SET signed_in_at = now() - make_interval(secs => :s) "
               "WHERE token_hash = :h", s=minutes * 60, h=admin_auth.token_sha256(token))


@requires_db
async def test_the_backup_needs_the_password_again_and_a_fresh_sign_in(admin, caplog) -> None:
    assert (await _core(None, {"password": STRONG_PW})).status_code == 401
    r = await _core(admin, {"password": "wrong"})
    assert r.status_code == 403 and "not right" in r.json()["detail"]
    # Charged to the login backoff, like a sign-in.
    assert (await _core(admin, {"password": STRONG_PW})).status_code == 429
    admin_auth.LOGIN_BACKOFF.reset()
    assert (await _core(admin, {})).status_code == 403
    admin_auth.LOGIN_BACKOFF.reset()
    await _age(admin, 6)
    r = await _core(admin, {"password": STRONG_PW})
    assert r.status_code == 403 and "sign in with the password again" in r.json()["detail"]
    await _age(admin, 1)
    with caplog.at_level(logging.DEBUG):
        r = await _core(admin, {"password": STRONG_PW})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    assert "attachment" in r.headers["content-disposition"]
    ident = server_identity.read_backup_bytes(r.content)
    assert ident.fingerprint == server_identity.load_or_create().fingerprint
    # Logged: that it happened and the fingerprint; never the key or password.
    assert ident.fingerprint in caplog.text
    assert base64.b64encode(ident.seed).decode() not in caplog.text
    assert STRONG_PW not in caplog.text


@requires_db
async def test_the_household_token_never_gets_it(admin) -> None:
    from domovoi.tests.auth_testkit import db_device_token

    token = await db_device_token()
    async with core_client() as c:
        r = await c.post(CORE, headers={**RW, "X-Device-Token": token}, json={"password": STRONG_PW})
    assert r.status_code == 401
    async with web_client() as c:
        r = await c.post(WEB, headers={**RW, "X-Device-Token": token}, json={"password": STRONG_PW})
    assert r.status_code in (401, 403)


@requires_db
async def test_a_phone_grant_session_never_gets_it(admin) -> None:
    from sqlalchemy import text

    from domovoi.db.session import session_scope

    async with session_scope() as s:
        grant = await s.execute(text(
            "INSERT INTO admin_phone_grants (grant_hash, device_id, label, expires_at) "
            "VALUES ('g-backup', 'android-9', 'Pixel', now() + interval '1 day') RETURNING id"))
        gid = grant.scalar_one()
        phone = await admin_auth.create_session(s, label="phone: Pixel", phone_grant_id=gid)
    r = await _core(phone, {"password": STRONG_PW})
    assert r.status_code == 403


def test_no_household_export_carries_the_key():
    """Only the security-tier backup route touches the backup; nothing
    else in either app builds one."""
    hits = []
    for base in (REPO_ROOT / "domovoi", REPO_ROOT / "web" / "backend"):
        for p in base.rglob("*.py"):
            if "tests" in p.parts:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            if "backup_bytes(" in text or "backup_document(" in text:
                hits.append(p.relative_to(REPO_ROOT).as_posix())
    assert sorted(hits) == ["domovoi/identity_backup_routes.py", "domovoi/server_identity.py"]
