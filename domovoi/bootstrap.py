"""Startup bootstrap for the core.

This module MUST be imported before anything that transitively loads
`faster-whisper` or `ctranslate2`. The Windows loader ignores
`os.add_dll_directory()` for *dependent* DLLs, so cuBLAS/cuDNN can't find each
other unless we preload the DLLs ourselves via `ctypes.WinDLL`.

Call `register_nvidia_dlls()` once at process start. No-op on non-Windows.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache

# Module flag the plugin loader asserts BEFORE importing any plugin code
# (design §4.1): plugin modules may transitively touch ctranslate2 /
# torch, so a future refactor that reorders startup must fail loudly at
# boot, not silently at the first Whisper call.
dlls_registered: bool = False


def preimport_torch() -> bool:
    """Import `torch` first, when installed, so it binds its own CUDA DLLs.

    Returns True if torch is importable (and now imported). Best effort: a
    missing or broken torch is never a startup error — the core only needs it
    for the optional `voice-profile` extra and for ctranslate2's model spec.
    """
    import importlib
    import importlib.util

    try:
        if importlib.util.find_spec("torch") is None:
            return False
        importlib.import_module("torch")
    except Exception:  # pragma: no cover - depends on the host's torch install
        return False
    return True


@lru_cache(maxsize=1)
def register_nvidia_dlls() -> tuple[str, ...]:
    """Register the NVIDIA pip-wheel DLL directories. Idempotent.

    Returns the list of registered directories (empty on non-Windows or if none
    found, which is fine if the user has CUDA installed system-wide).
    """
    global dlls_registered
    dlls_registered = True

    if sys.platform != "win32":
        return ()

    import ctypes
    import site

    found: list[str] = []
    subdirs = ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_nvrtc/bin")
    for sp in site.getsitepackages():
        for sub in subdirs:
            d = os.path.join(sp, sub)
            if os.path.isdir(d):
                found.append(d)

    if not found:
        return ()

    # Torch bundles its OWN cuBLAS/cuDNN under `torch/lib` and binds them at
    # import time, and whichever cuDNN lands in the process first wins. The
    # pip `nvidia-cudnn-cu12` wheel is routinely NEWER than the one torch
    # ships, so preloading ours first makes a later `import torch` die with
    # `OSError: [WinError 127] ... torch\lib\cudnn_cnn64_9.dll`. ctranslate2
    # imports torch on the first Whisper load, which takes core startup down
    # with it. Import torch BEFORE we register/preload anything: ctranslate2
    # then reuses torch's cuDNN and the preloads below bind torch's copies.
    preimport_torch()

    for d in found:
        try:
            os.add_dll_directory(d)
        except (OSError, AttributeError):
            pass

    os.environ["PATH"] = os.pathsep.join(found) + os.pathsep + os.environ.get("PATH", "")

    # Preload the DLLs so ctranslate2's delay-loaded imports resolve.
    dlls = (
        "cublas64_12.dll",
        "cublasLt64_12.dll",
        "cudnn_ops64_9.dll",
        "cudnn_graph64_9.dll",
        "cudnn64_9.dll",
    )
    for dll in dlls:
        for d in found:
            p = os.path.join(d, dll)
            if os.path.isfile(p):
                try:
                    ctypes.WinDLL(p)
                except OSError:
                    pass
                break

    return tuple(found)
