"""The trainer wrapper's GPU choice must hold inside WSL2.

``scripts/wake_word/train_wake_word.py`` picks the least-used card and used
to pass only ``--gpus device=N``. Under WSL2 (Docker Desktop, or Docker
Engine in a distro with the NVIDIA Container Toolkit) that flag does not
isolate a card: the container sees every GPU through /dev/dxg and torch
takes cuda:0, so a run "on GPU 1" landed on GPU 0 next to whatever was
already there (measured 2026-10-13 on two RTX 4070 Ti SUPER). The wrapper
now also sets CUDA_VISIBLE_DEVICES to the chosen card's UUID, and finds
WSL's nvidia-smi at /usr/lib/wsl/lib when a systemd unit's PATH lacks it.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

WRAPPER = Path(__file__).resolve().parents[2] / "scripts" / "wake_word" / "train_wake_word.py"


def _wrapper():
    spec = importlib.util.spec_from_file_location("_wake_wrapper", WRAPPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tw = _wrapper()

SMI_TWO_CARDS = (
    "0, GPU-00000000-1111-4222-8333-444444444444, 9656\n"
    "1, GPU-55555555-6666-4777-8888-999999999999, 948\n"
)


def _fake_smi(monkeypatch, stdout, *, on_path=True, wsl_file=False):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(tw.shutil, "which", lambda name: "/usr/bin/nvidia-smi" if on_path else None)
    monkeypatch.setattr(tw.os.path, "isfile", lambda p: wsl_file and p == tw.WSL_NVIDIA_SMI)
    monkeypatch.setattr(tw.os, "access", lambda p, mode: wsl_file and p == tw.WSL_NVIDIA_SMI)
    monkeypatch.setattr(tw.subprocess, "run", run)
    return calls


def test_least_used_card_is_picked_with_its_uuid(monkeypatch):
    _fake_smi(monkeypatch, SMI_TWO_CARDS)
    assert tw.select_gpu() == ("1", 2, "GPU-55555555-6666-4777-8888-999999999999")


def test_single_card_uses_all(monkeypatch):
    _fake_smi(monkeypatch, "0, GPU-00000000-1111-4222-8333-444444444444, 610\n")
    assert tw.select_gpu() == ("all", 1, "GPU-00000000-1111-4222-8333-444444444444")


def test_wsl_nvidia_smi_is_found_off_the_unit_path(monkeypatch):
    """domovoi-core.service's PATH has no /usr/lib/wsl/lib, so on the
    Windows install the pick used to fall through to --gpus all."""
    calls = _fake_smi(monkeypatch, SMI_TWO_CARDS, on_path=False, wsl_file=True)
    assert tw.select_gpu()[0] == "1"
    assert calls[0][0] == tw.WSL_NVIDIA_SMI


def test_no_nvidia_smi_anywhere(monkeypatch):
    _fake_smi(monkeypatch, SMI_TWO_CARDS, on_path=False, wsl_file=False)
    assert tw.select_gpu() == ("", 0, "")


def test_the_chosen_card_reaches_cuda_inside_the_container():
    argv = tw.build_run_argv(
        "/clips", "/models", "hey domovoi", "hey_domovoi",
        ["--gpus", "device=1"], "GPU-55555555-6666-4777-8888-999999999999",
    )
    assert argv[argv.index("--gpus") + 1] == "device=1"
    assert "CUDA_VISIBLE_DEVICES=GPU-55555555-6666-4777-8888-999999999999" in argv
    assert argv[argv.index("CUDA_VISIBLE_DEVICES=GPU-55555555-6666-4777-8888-999999999999") - 1] == "-e"
    # Still before the image name, which stays last.
    assert argv[-1] == tw.IMAGE


def test_cpu_and_all_gpu_runs_set_no_cuda_visible_devices():
    for flag in ([], ["--gpus", "all"]):
        argv = tw.build_run_argv("/clips", "/models", "hey domovoi", "hey_domovoi", flag)
        assert not any(a.startswith("CUDA_VISIBLE_DEVICES=") for a in argv)
