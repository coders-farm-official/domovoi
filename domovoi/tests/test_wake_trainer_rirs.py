"""The wake-word trainer image fetches its room impulse responses without
the ``datasets`` library (4.x and later need torchcodec, which the image
does not have; 3.6.0 carries a path-traversal advisory fixed in 5.0.1).
These tests read the entrypoint's WAV conversion and check that no file the
image is built from brings ``datasets`` back."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

DOCKER_DIR = Path(__file__).resolve().parents[2] / "scripts" / "wake_word" / "docker"


def _entrypoint():
    spec = importlib.util.spec_from_file_location("_wake_entrypoint_rirs", DOCKER_DIR / "entrypoint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_image_does_not_install_datasets():
    for name in ("Dockerfile", "constraints.txt", "requirements.txt"):
        lines = (DOCKER_DIR / name).read_text(encoding="utf-8").splitlines()
        code = [ln.split("#")[0] for ln in lines if not ln.lstrip().startswith("#")]
        assert not any(re.search(r"\bdatasets\b", ln) for ln in code), name
    assert "import datasets" not in (DOCKER_DIR / "entrypoint.py").read_text(encoding="utf-8")


def test_an_impulse_response_becomes_16_bit_samples(tmp_path):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    ep = _entrypoint()

    mono = tmp_path / "mono.wav"
    sf.write(str(mono), np.array([0.0, 0.5, -0.5]), 16000, subtype="PCM_16")
    got = ep.rir_to_int16(mono)
    assert got.dtype == np.int16
    assert got[0] == 0
    assert abs(int(got[1]) - 16383) <= 1 and abs(int(got[2]) + 16383) <= 1

    stereo = tmp_path / "stereo.wav"
    sf.write(str(stereo), np.array([[0.5, 0.5], [0.25, -0.25]]), 16000, subtype="PCM_16")
    mixed = ep.rir_to_int16(stereo)
    assert mixed.shape == (2,)
    assert abs(int(mixed[0]) - 16383) <= 1 and abs(int(mixed[1])) <= 1
