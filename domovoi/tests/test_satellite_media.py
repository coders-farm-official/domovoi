"""Satellite media prep — overlay editors (idempotent), payload assembly
(with the caches faked), rendered-script sanity, and the jobs API lifecycle
with the builder monkeypatched. No Docker, no network, no hardware."""

from __future__ import annotations

import asyncio
import json
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import web.backend.api.satellite_media as media_api
from domovoi.db.session import engine
from domovoi.satellite_media import builder, cache, overlay, payload
from domovoi.tests.conftest import requires_db
from web.backend.main import app


# ─── overlay editors ──────────────────────────────────────────────────────


def test_config_txt_edit_is_idempotent() -> None:
    base = "# stock config\ndtparam=audio=on\n"
    once = overlay.edit_config_txt(base)
    assert "dtoverlay=dwc2,dr_mode=peripheral" in once
    assert overlay.edit_config_txt(once) == once


def test_cmdline_edit_single_line_and_idempotent() -> None:
    base = "console=serial0,115200 root=PARTUUID=xxx rootwait\n"
    once = overlay.edit_cmdline_txt(base)
    assert once.count("\n") == 1                       # single kernel line
    assert "modules-load=dwc2" in once
    assert "systemd.run=/boot/firmware/domovoi/firstrun.sh" in once
    assert overlay.edit_cmdline_txt(once) == once


def test_rendered_scripts_have_no_placeholders_or_crlf() -> None:
    firstrun = overlay.render_firstrun("domovoi", "respeaker_2mic_hat_v2", "voice")
    stage2 = overlay.render_stage2("domovoi")
    for script in (firstrun, stage2):
        assert "\r" not in script
        assert "@SAT_USER@" not in script and "@USER@" not in script.split("sed ")[0]
        assert script.startswith("#!/bin/bash")


# ─── payload assembly ─────────────────────────────────────────────────────


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A minimal repo tree + isolated cache root."""
    repo = tmp_path / "repo"
    sat = repo / "satellite"
    (sat / "scripts").mkdir(parents=True)
    (sat / "client.py").write_text("# client\n", encoding="utf-8")
    (sat / "requirements.txt").write_text("requests==2.32.0\n", encoding="utf-8")
    (sat / "domovoi-satellite.service").write_text("[Unit]\n", encoding="utf-8")
    (sat / "scripts" / "domovoi-provisioning.service").write_text("[Unit]\n", encoding="utf-8")
    (sat / "scripts" / "domovoi-apply-payload").write_text("#!/bin/sh\n", encoding="utf-8")
    (sat / "secret.env").write_text("nope", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    (tmp_path / "cache" / "wheels" / "3.13").mkdir(parents=True)
    (tmp_path / "cache" / "wheels" / "3.13" / "pip-24.0-py3-none-any.whl").write_bytes(b"whl")
    return repo


@requires_db
def test_assemble_and_finalize(fake_repo, tmp_path, monkeypatch):
    async def _no_plugins():
        return []

    monkeypatch.setattr(payload, "enabled_satellite_plugins", _no_plugins)
    ws = tmp_path / "ws"
    asm = asyncio.run(
        payload.assemble(
            ws, repo_root=fake_repo, python_version="3.13", os_release="trixie",
        )
    )
    pay = asm["dir"]
    assert (pay / "satellite" / "client.py").is_file()
    assert not (pay / "satellite" / "secret.env").exists()     # denylisted
    assert (pay / "wheels" / "pip-24.0-py3-none-any.whl").is_file()
    assert (pay / "system" / "domovoi-apply-payload").is_file()
    assert any("debs" in w for w in asm["warnings"])           # empty bucket flagged

    manifest = json.loads((pay / "manifest.json").read_text(encoding="utf-8"))
    assert "satellite/client.py" in manifest["files"]

    fin = payload.finalize(ws, pay, overlay.render_stage2("domovoi"))
    assert fin["tar"].is_file() and len(fin["sha256"]) == 64
    with tarfile.open(fin["tar"]) as tf:
        names = tf.getnames()
    assert "payload/bootstrap/stage2.sh" in names


@requires_db
def test_builder_writes_overlay_to_fake_boot(fake_repo, tmp_path, monkeypatch):
    async def _no_plugins():
        return []

    monkeypatch.setattr(payload, "enabled_satellite_plugins", _no_plugins)
    monkeypatch.setattr(builder, "BUILDS_ROOT", tmp_path / "builds")

    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "config.txt").write_text("dtparam=audio=on\n", encoding="utf-8")
    (boot / "cmdline.txt").write_text("root=PARTUUID=x rootwait\n", encoding="utf-8")
    (boot / "bcm2710-rpi-zero-2-w.dtb").write_bytes(b"dtb")

    from domovoi.config import settings as core_settings

    monkeypatch.setattr(core_settings, "repo_dir", str(fake_repo))
    result = asyncio.run(
        builder.build(
            board_id="pi02w",
            mic_profile="respeaker_2mic_hat_v2",
            target_kind="drive",
            target_mount=boot,
            job_id="testjob",
            offline=False,          # skip cache fetches entirely
        )
    )
    assert result["ok"] is True
    assert (boot / "domovoi" / "payload.tar.gz").is_file()
    assert (boot / "domovoi" / "firstrun.sh").is_file()
    info = json.loads((boot / "domovoi" / "build-info.json").read_text(encoding="utf-8"))
    assert info["board"] == "pi02w"
    cfg = (boot / "config.txt").read_text(encoding="utf-8")
    assert "dwc2" in cfg
    # Re-running against the same card stays idempotent on the text edits.
    assert overlay.edit_config_txt(cfg) == cfg


def test_builder_refuses_non_boot_target(tmp_path):
    with pytest.raises(ValueError, match="boot partition"):
        asyncio.run(
            builder.build(
                board_id="pi02w",
                mic_profile="none",
                target_kind="drive",
                target_mount=tmp_path,   # empty dir — no config.txt
                job_id="x",
            )
        )


def test_builder_refuses_unsupported_board(tmp_path):
    with pytest.raises(ValueError, match="not supported"):
        asyncio.run(
            builder.build(
                board_id="radxa-zero3w",
                mic_profile="none",
                target_kind="zip",
                target_mount=None,
                job_id="x",
            )
        )


# ─── jobs API lifecycle (builder monkeypatched) ───────────────────────────

pytestmark_db = requires_db


def _truncate_jobs() -> None:
    async def _t():
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE satellite_media_jobs RESTART IDENTITY"))

    asyncio.run(_t())


@requires_db
def test_jobs_api_lifecycle(monkeypatch, tmp_path):
    _truncate_jobs()

    async def fake_build(**kwargs):
        await kwargs["progress"]("assemble", 50, "faking it")
        artifact = tmp_path / "overlay.zip"
        artifact.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        return {"ok": True, "warnings": ["cache bucket 'debs' is empty"],
                "artifact_path": str(artifact), "offline": False}

    monkeypatch.setattr(media_api.builder, "build", fake_build)

    with TestClient(app) as client:
        r = client.get("/api/satellites/media/status")
        assert r.status_code == 200
        body = r.json()
        assert any(b["id"] == "pi02w" and b["supported"] for b in body["boards"])
        assert any(b["id"] == "radxa-zero3w" and not b["supported"] for b in body["boards"])

        r = client.post(
            "/api/satellites/media/prepare",
            json={"board": "pi02w", "mic_profile": "none", "target": {"kind": "zip"}},
        )
        assert r.status_code == 200, r.text
        job_id = r.json()["job"]["id"]
        assert r.json()["attached"] is False

        # The background task completes quickly; poll the jobs list.
        import time as _time

        for _ in range(50):
            jobs = client.get("/api/satellites/media/jobs").json()
            if jobs and jobs[0]["status"] in ("done", "failed"):
                break
            _time.sleep(0.1)
        assert jobs[0]["status"] == "done", jobs[0]
        assert jobs[0]["has_artifact"] is True
        assert "artifact_path" not in jobs[0]          # server-local only

        r = client.get(f"/api/satellites/media/jobs/{job_id}/download")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/zip")

    _truncate_jobs()


@requires_db
def test_prepare_validates_inputs():
    with TestClient(app) as client:
        r = client.post(
            "/api/satellites/media/prepare",
            json={"board": "radxa-zero3w", "mic_profile": "none", "target": {"kind": "zip"}},
        )
        assert r.status_code == 422
        r = client.post(
            "/api/satellites/media/prepare",
            json={"board": "pi02w", "mic_profile": "nope", "target": {"kind": "zip"}},
        )
        assert r.status_code == 422
