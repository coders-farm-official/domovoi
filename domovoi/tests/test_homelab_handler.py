from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from domovoi.handlers.homelab import (
    HomelabHandler,
    _GpuSnapshot,
    _STATUS_RE,
    _format_gpu_summary,
    _format_models_summary,
    _format_rooms_summary,
)
from domovoi.models import Context


# ─── Regex tests ────────────────────────────────────────────────────────────

def test_status_regex_matches_common_phrasings() -> None:
    for s in (
        "what's domovoi doing",
        "what is domovoi doing",
        "what's my domovoi doing",  # possessive — natural Whisper transcript
        "what is my domovoi doing",
        "what's domovoi up to",
        "what's my domovoi up to",
        "what's the server doing",
        "what's my server doing",
        "how's domovoi",
        "how is domovoi",
        "how's domovoi doing",
        "how's my domovoi",
        "is domovoi busy",
        "is my domovoi busy",
        "domovoi status",
        "my domovoi status",
        "system status",
        "homelab status",
        "gpu status",
    ):
        assert _STATUS_RE.match(s), f"expected match: {s!r}"


def test_status_regex_negative_cases() -> None:
    """Things that look kinda like status queries but shouldn't hijack the
    handler. 'who's domovoi' for instance is closer to a who-is question
    that should fall through to QA."""
    for s in (
        "play domovoi by lana del rey",   # song name, not status
        "set a timer for domovoi",
        "tell domovoi a joke",
        "who's domovoi",
    ):
        assert not _STATUS_RE.match(s), f"unexpected match: {s!r}"


# ─── Formatting helpers ─────────────────────────────────────────────────────

def test_format_gpu_summary_empty() -> None:
    assert _format_gpu_summary([]) == "No GPUs detected"


def test_format_gpu_summary_single() -> None:
    snapshot = _GpuSnapshot(
        name="RTX 4070 Ti SUPER",
        util_pct=42,
        mem_used_mb=8192,
        mem_total_mb=16384,
        temp_c=65,
    )
    out = _format_gpu_summary([snapshot])
    assert "GPU 42% load" in out
    assert "8.0 of 16.0" in out
    assert "65 degrees" in out


def test_format_gpu_summary_multi_uses_average_and_hottest() -> None:
    snapshots = [
        _GpuSnapshot(name="A", util_pct=20, mem_used_mb=1024, mem_total_mb=16384, temp_c=50),
        _GpuSnapshot(name="B", util_pct=80, mem_used_mb=2048, mem_total_mb=16384, temp_c=70),
    ]
    out = _format_gpu_summary(snapshots)
    assert "2 GPUs averaging 50% load" in out
    # Combined VRAM (3 GiB used / 32 GiB total).
    assert "3.0 of 32.0" in out
    # Hottest reported, not average.
    assert "70 degrees" in out


def test_format_models_summary_variants() -> None:
    assert _format_models_summary([]) == "no models loaded"
    assert _format_models_summary(["llama3.2:3b"]) == "llama3.2:3b loaded"
    assert (
        _format_models_summary(["llama3.2:3b", "qwen2.5:14b"])
        == "llama3.2:3b and qwen2.5:14b loaded"
    )
    assert _format_models_summary(["a", "b", "c"]) == "3 models loaded"


def test_format_rooms_summary_variants() -> None:
    assert _format_rooms_summary([]) == "no rooms connected"
    assert _format_rooms_summary(["kitchen"]) == "the kitchen room is connected"
    assert _format_rooms_summary(["kitchen", "garage"]) == "2 rooms connected"


# ─── Behavior (mocked subprocess + httpx) ──────────────────────────────────

@pytest.mark.asyncio
async def test_status_response_with_all_sources_available() -> None:
    snapshots = [
        _GpuSnapshot(
            name="RTX 4070 Ti SUPER",
            util_pct=10,
            mem_used_mb=4096,
            mem_total_mb=16384,
            temp_c=55,
        ),
    ]

    from domovoi.clients import mpd as mpd_module

    mpd_module._room_ports = {"kitchen": (6600, 8001), "garage": (6601, 8002)}
    try:
        with patch(
            "domovoi.handlers.homelab._query_gpus",
            new=AsyncMock(return_value=snapshots),
        ), patch(
            "domovoi.handlers.homelab._query_loaded_models",
            new=AsyncMock(return_value=["llama3.2:3b", "qwen2.5:14b"]),
        ):
            handler = HomelabHandler()
            ctx = Context(session_id=None, room_id="kitchen", online=True)
            m = _STATUS_RE.match("what's domovoi doing")
            assert m
            response = await handler._status_from_match(m, ctx, None)
    finally:
        mpd_module._room_ports = {}

    assert response.matched_handler == "homelab"
    assert "GPU 10% load" in response.text
    assert "llama3.2:3b" in response.text.lower()
    # Two rooms registered.
    assert "2 rooms" in response.text.lower()


@pytest.mark.asyncio
async def test_status_degrades_gracefully_when_nvidia_smi_missing() -> None:
    """A CPU-only host (or one without nvidia-smi on PATH) should still
    return a coherent answer rather than blowing up."""
    from domovoi.clients import mpd as mpd_module

    mpd_module._room_ports = {"kitchen": (6600, 8001)}
    try:
        with patch(
            "domovoi.handlers.homelab._query_gpus",
            new=AsyncMock(return_value=[]),
        ), patch(
            "domovoi.handlers.homelab._query_loaded_models",
            new=AsyncMock(return_value=[]),
        ):
            handler = HomelabHandler()
            ctx = Context(session_id=None, room_id="kitchen", online=True)
            m = _STATUS_RE.match("system status")
            assert m
            response = await handler._status_from_match(m, ctx, None)
    finally:
        mpd_module._room_ports = {}

    assert "no gpus detected" in response.text.lower()
    assert "no models loaded" in response.text.lower()


# ─── nvidia-smi lookup (WSL) ────────────────────────────────────────────────
# In the Windows installer's WSL distro, nvidia-smi lives in /usr/lib/wsl/lib,
# which WSL puts on PATH for wsl.exe sessions only, never for systemd services
# like domovoi-core.service: the probe has to look there itself.


def test_nvidia_smi_path_prefers_path(monkeypatch) -> None:
    from domovoi.handlers import homelab

    monkeypatch.setattr(homelab.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    assert homelab._nvidia_smi_path() == "/usr/bin/nvidia-smi"


def test_nvidia_smi_path_falls_back_to_wsl_copy(monkeypatch) -> None:
    from domovoi.handlers import homelab

    monkeypatch.setattr(homelab.shutil, "which", lambda name: None)
    monkeypatch.setattr(homelab.os.path, "isfile", lambda p: p == homelab._WSL_NVIDIA_SMI)
    monkeypatch.setattr(homelab.os, "access", lambda p, mode: p == homelab._WSL_NVIDIA_SMI)
    assert homelab._nvidia_smi_path() == "/usr/lib/wsl/lib/nvidia-smi"


def test_nvidia_smi_path_none_without_either(monkeypatch) -> None:
    from domovoi.handlers import homelab

    monkeypatch.setattr(homelab.shutil, "which", lambda name: None)
    monkeypatch.setattr(homelab.os.path, "isfile", lambda p: False)
    assert homelab._nvidia_smi_path() is None


@pytest.mark.asyncio
async def test_query_gpus_runs_the_wsl_copy_when_not_on_path(monkeypatch) -> None:
    """The systemd-PATH case: nothing on PATH, WSL's copy present → it is
    the one run, and its CSV becomes the snapshots."""
    from domovoi.handlers import homelab

    monkeypatch.setattr(homelab, "_nvidia_smi_path", lambda: homelab._WSL_NVIDIA_SMI)
    seen: list[str] = []

    class _Proc:
        async def communicate(self):
            return (
                b"NVIDIA GeForce RTX 4070 Ti SUPER, 0, 471, 16376, 55\n"
                b"NVIDIA GeForce RTX 4070 Ti SUPER, 2, 905, 16376, 53\n",
                b"",
            )

    async def _exec(exe, *args, **kwargs):
        seen.append(exe)
        return _Proc()

    monkeypatch.setattr(homelab.asyncio, "create_subprocess_exec", _exec)
    gpus = await homelab._query_gpus()
    assert seen == ["/usr/lib/wsl/lib/nvidia-smi"]
    assert [g.name for g in gpus] == ["NVIDIA GeForce RTX 4070 Ti SUPER"] * 2
    assert gpus[1].mem_used_mb == 905 and gpus[1].mem_total_mb == 16376


@pytest.mark.asyncio
async def test_query_gpus_empty_without_nvidia_smi(monkeypatch) -> None:
    from domovoi.handlers import homelab

    monkeypatch.setattr(homelab, "_nvidia_smi_path", lambda: None)

    async def _exec(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("nvidia-smi must not be started")

    monkeypatch.setattr(homelab.asyncio, "create_subprocess_exec", _exec)
    assert await homelab._query_gpus() == []
