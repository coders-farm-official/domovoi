"""mac/installer/pins.conf against the pins it must agree with.

The Mac side runs the same Ollama release as the Linux side (mac-installer
PLAN 4.3: "Mac and Linux keep one Ollama version, bumped together"), and
"use the Ollama already on this Mac" accepts the oldest version the core
runs on. The Swift tests (mac/tests) hold the same lines on the Mac; this
one runs on every pytest, so a bump on one side alone fails here.

DB-free.
"""

from __future__ import annotations

import re
from pathlib import Path

from domovoi.clients import ollama

REPO_ROOT = Path(__file__).resolve().parents[2]
PINS = REPO_ROOT / "mac" / "installer" / "pins.conf"


def _pins() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in PINS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        assert key not in out, f"{key} given twice in pins.conf"
        out[key] = value
    return out


def test_the_mac_ollama_is_the_linux_one():
    doc = (REPO_ROOT / "docs" / "LINUX_HOST.md").read_text(encoding="utf-8")
    linux = re.search(r"^OLLAMA_VERSION=([0-9.]+)$", doc, re.M).group(1)
    pins = _pins()
    assert pins["OLLAMA_VERSION"] == linux
    assert f"/v{linux}/ollama-darwin.tgz" in pins["OLLAMA_URL"]


def test_the_mac_minimum_is_the_cores():
    got = tuple(int(x) for x in _pins()["OLLAMA_MIN_VERSION"].split("."))
    assert got == ollama.MIN_OLLAMA_VERSION


def test_every_download_is_https_and_pinned():
    pins = _pins()
    for name in ("LIMA", "OLLAMA", "UBUNTU_IMAGE"):
        assert pins[f"{name}_URL"].startswith("https://"), name
        assert re.fullmatch(r"[0-9a-f]{64}", pins[f"{name}_SHA256"]), name
    assert f"/v{pins['LIMA_VERSION']}/" in pins["LIMA_URL"]


def test_the_speech_service_pins_are_whole_and_by_commit():
    """The Mac's speech service (mac-installer PHASE8A-STT.md): a whisper.cpp
    tag with its full commit (build.sh checks the clone against it), and a
    model by Hugging Face commit and SHA-256, labelled with the model name
    the core reports."""
    pins = _pins()
    if "WHISPERCPP_VERSION" not in pins:
        return                                  # a package without the service: nothing to hold
    assert re.fullmatch(r"\d+\.\d+\.\d+", pins["WHISPERCPP_VERSION"])
    assert re.fullmatch(r"[0-9a-f]{40}", pins["WHISPERCPP_COMMIT"])
    assert re.fullmatch(r"https://huggingface\.co/[^/]+/[^/]+/resolve/[0-9a-f]{40}/ggml-[A-Za-z0-9._-]+\.bin",
                        pins["STT_MODEL_URL"])
    assert re.fullmatch(r"[0-9a-f]{64}", pins["STT_MODEL_SHA256"])
    assert int(pins["STT_MODEL_BYTES"]) > 0
    assert f"ggml-{pins['STT_MODEL_NAME']}.bin" in pins["STT_MODEL_URL"]
    # The core's short window is for English-only models; the label must say which.
    from domovoi.config import Settings

    assert pins["STT_MODEL_NAME"] == Settings.model_fields["whisper_remote_model"].default
