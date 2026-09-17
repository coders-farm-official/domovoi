"""F-V001 regression: bootstrap must import torch BEFORE preloading the pip
NVIDIA DLLs on Windows.

The pip `nvidia-cudnn-cu12` wheel is unpinned and routinely newer than the
cuDNN torch bundles in `torch/lib`. Whichever cuDNN lands in the process first
wins, so preloading ours first makes the later `import torch` (ctranslate2 does
it on the first Whisper load) fail with WinError 127 and takes core startup
down with it.

DB-free on purpose: this is pure process/import-order behaviour, and the fake
finder below means the host's real torch is never imported.
"""

from __future__ import annotations

import ctypes
import importlib.util
import os
import site
import sys
from pathlib import Path

from domovoi import bootstrap

FAKE_TORCH = """import os

with open(os.environ["DOMOVOI_F_V001_ORDER"], "a", encoding="utf-8") as fh:
    print("torch", file=fh)
"""


class _TorchFinder:
    """Meta-path finder that owns the name `torch` for the duration of a test.

    With a path it serves the recording stub; without one it behaves like a
    host that has no torch installed.
    """

    def __init__(self, fake_path: Path | None) -> None:
        self.fake_path = fake_path

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "torch" and not fullname.startswith("torch."):
            return None
        if self.fake_path is None:
            raise ModuleNotFoundError("No module named 'torch'", name="torch")
        return importlib.util.spec_from_file_location(fullname, str(self.fake_path))


def _run_bootstrap(tmp_path: Path, monkeypatch, *, with_torch: bool) -> list[str]:
    """Drive register_nvidia_dlls() against a fake win32 + fake site-packages.

    Returns the recorded load order, e.g. ``["torch", "cudnn64_9.dll"]``.
    """
    order_file = tmp_path / "order.txt"
    monkeypatch.setenv("DOMOVOI_F_V001_ORDER", str(order_file))

    # Fake site-packages carrying the pip NVIDIA wheel layout.
    site_packages = tmp_path / "site-packages"
    cudnn_bin = site_packages / "nvidia" / "cudnn" / "bin"
    cudnn_bin.mkdir(parents=True)
    (cudnn_bin / "cudnn64_9.dll").write_bytes(b"not a real dll")
    monkeypatch.setattr(site, "getsitepackages", lambda: [str(site_packages)])

    fake_torch = None
    if with_torch:
        fake_torch = tmp_path / "fake_torch.py"
        fake_torch.write_text(FAKE_TORCH, encoding="utf-8")
    monkeypatch.setattr(sys, "meta_path", [_TorchFinder(fake_torch), *sys.meta_path])

    def fake_windll(path, *a, **kw):
        with open(order_file, "a", encoding="utf-8") as fh:
            print(os.path.basename(str(path)), file=fh)
        return object()

    monkeypatch.setattr(ctypes, "WinDLL", fake_windll, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))

    saved_torch = sys.modules.pop("torch", None)
    bootstrap.register_nvidia_dlls.cache_clear()
    try:
        bootstrap.register_nvidia_dlls()
    finally:
        sys.modules.pop("torch", None)
        if saved_torch is not None:
            sys.modules["torch"] = saved_torch
        bootstrap.register_nvidia_dlls.cache_clear()

    if not order_file.exists():
        return []
    return order_file.read_text(encoding="utf-8").split()


def test_torch_is_imported_before_nvidia_dll_preload(tmp_path, monkeypatch):
    order = _run_bootstrap(tmp_path, monkeypatch, with_torch=True)

    assert "torch" in order, f"bootstrap never imported torch (F-V001); order={order}"
    assert "cudnn64_9.dll" in order, "bootstrap stopped preloading the pip cuDNN"
    assert order.index("torch") < order.index("cudnn64_9.dll"), (
        f"torch must bind its bundled cuDNN first; got load order {order}"
    )


def test_missing_torch_still_preloads(tmp_path, monkeypatch):
    """No torch installed is not an error - the preload must still happen."""
    order = _run_bootstrap(tmp_path, monkeypatch, with_torch=False)

    assert order == ["cudnn64_9.dll"]


def test_preimport_torch_reports_missing(monkeypatch):
    monkeypatch.setattr(sys, "meta_path", [_TorchFinder(None), *sys.meta_path])
    saved_torch = sys.modules.pop("torch", None)
    try:
        assert bootstrap.preimport_torch() is False
    finally:
        if saved_torch is not None:
            sys.modules["torch"] = saved_torch
