"""Where this server keeps what a kit copies, per host kind.

On a Linux server and in the Windows installer's WSL distro, Ollama, its
models and the Hugging Face cache are in the same Linux system as the
core. On a Mac the core runs in a Lima VM while Ollama and whisper.cpp's
model are on the Mac itself (design section 1, "Where models live"). The
kit builder then reads them through a **read-only Lima mount** of the
Mac's folders, or, when there is none, downloads them again by pin.

The seam (no Mac is needed to test the rest): the Mac installer's VM
template mounts the Mac's Ollama models folder and its STT model folder
read-only and names the mount points in the environment the core runs
with (the Mac kit, P4, writes them):

* ``DOMOVOI_KIT_OLLAMA_MODELS``: an Ollama models folder (``manifests/``
  and ``blobs/``) to read instead of the local ones. Any host may set it.
* ``DOMOVOI_KIT_MAC_STT``: the Mac's ``ggml-small.en.bin``.

A path that isn't readable is treated as absent: the builder downloads
the file instead, and the card says how much. Never written to: the
builder only ever opens these files for reading.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Where Ollama keeps its models: the service account provision.sh makes
#: (/usr/share/ollama), then a user install.
LINUX_OLLAMA_DIRS = (
    "/usr/share/ollama/.ollama/models",
    "~/.ollama/models",
)


def _readable_dir(p: Path) -> bool:
    try:
        return p.is_dir() and os.access(p, os.R_OK | os.X_OK) and (p / "manifests").is_dir()
    except OSError:
        return False


def ollama_models_dirs() -> list[Path]:
    """Ollama models folders this process can read, best first."""
    out: list[Path] = []
    env = (os.environ.get("DOMOVOI_KIT_OLLAMA_MODELS") or "").strip()
    cands = ([env] if env else []) + [os.environ.get("OLLAMA_MODELS") or ""] + list(LINUX_OLLAMA_DIRS)
    for c in cands:
        if not c:
            continue
        p = Path(c).expanduser()
        if _readable_dir(p) and p not in out:
            out.append(p)
    return out


def mac_stt_file() -> Path | None:
    """The Mac's whisper.cpp model through the read-only mount, if any."""
    env = (os.environ.get("DOMOVOI_KIT_MAC_STT") or "").strip()
    if not env:
        return None
    p = Path(env)
    try:
        return p if p.is_file() and os.access(p, os.R_OK) else None
    except OSError:
        return None


def kit_root() -> Path:
    """``~/.domovoi/offline_kit``: the builds and the download cache."""
    return Path("~/.domovoi/offline_kit").expanduser()


def cache_dir(*parts: str) -> Path:
    p = kit_root().joinpath("cache", *parts)
    p.mkdir(parents=True, exist_ok=True)
    return p
