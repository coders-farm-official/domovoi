"""Is a Whisper model already on disk? (No network, no heavy imports.)

The Models page greys a Whisper size that isn't downloaded while the box
is set to stay off the internet (``INTERNET_ACCESS=never``): after the
restart it could not load, and the core would fall back to another model.
The web process answers that question itself, so this module imports
neither ``faster_whisper`` (which pulls in CTranslate2) nor
``huggingface_hub``: it mirrors the two facts it needs.

* :data:`WHISPER_REPOS` — faster-whisper's size name → Hugging Face repo
  map (``faster_whisper.utils._MODELS``; a test keeps the copy equal to the
  installed package's).
* :func:`hf_hub_cache_dir` — where ``huggingface_hub`` keeps its cache
  (``HF_HUB_CACHE``, else ``HUGGINGFACE_HUB_CACHE``, else
  ``$HF_HOME/hub``, else ``$XDG_CACHE_HOME/huggingface/hub``, else
  ``~/.cache/huggingface/hub``), read the way ``huggingface_hub.constants``
  reads it.

A model counts as cached when its repo directory holds a snapshot with a
``model.bin``: the normal layout (``refs/main`` plus a snapshot) and a
cache pinned by commit (a snapshot with no ``refs/main``, which
``domovoi/clients/whisper.py`` loads by path).
"""

from __future__ import annotations

import os
from pathlib import Path

# faster-whisper 1.x ``faster_whisper.utils._MODELS``.
WHISPER_REPOS: dict[str, str] = {
    "tiny.en": "Systran/faster-whisper-tiny.en",
    "tiny": "Systran/faster-whisper-tiny",
    "base.en": "Systran/faster-whisper-base.en",
    "base": "Systran/faster-whisper-base",
    "small.en": "Systran/faster-whisper-small.en",
    "small": "Systran/faster-whisper-small",
    "medium.en": "Systran/faster-whisper-medium.en",
    "medium": "Systran/faster-whisper-medium",
    "large-v1": "Systran/faster-whisper-large-v1",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large": "Systran/faster-whisper-large-v3",
    "distil-large-v2": "Systran/faster-distil-whisper-large-v2",
    "distil-medium.en": "Systran/faster-distil-whisper-medium.en",
    "distil-small.en": "Systran/faster-distil-whisper-small.en",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
    "distil-large-v3.5": "distil-whisper/distil-large-v3.5-ct2",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}


def hf_hub_cache_dir() -> Path:
    """The Hugging Face hub cache this process would use."""
    env = os.environ
    home = env.get("HF_HOME") or os.path.join(
        env.get("XDG_CACHE_HOME") or "~/.cache", "huggingface"
    )
    legacy = env.get("HUGGINGFACE_HUB_CACHE") or os.path.join(home, "hub")
    return Path(os.path.expanduser(env.get("HF_HUB_CACHE") or legacy))


def repo_id_for(model: str) -> str | None:
    """The Hugging Face repo of a faster-whisper model name (a size name,
    or an ``owner/name`` repo id); None for anything else."""
    if not isinstance(model, str) or not model.strip():
        return None
    name = model.strip()
    if name in WHISPER_REPOS:
        return WHISPER_REPOS[name]
    if name.count("/") == 1 and not os.path.isabs(name):
        return name
    return None


def repo_dir(model: str) -> Path | None:
    repo = repo_id_for(model)
    if repo is None:
        return None
    return hf_hub_cache_dir() / ("models--" + repo.replace("/", "--"))


def snapshot_dirs(model: str) -> list[Path]:
    """The model's cached snapshots that hold a ``model.bin``."""
    root = repo_dir(model)
    if root is None:
        return []
    snaps = root / "snapshots"
    try:
        return sorted(
            d for d in snaps.iterdir() if d.is_dir() and (d / "model.bin").exists()
        )
    except OSError:
        return []


def whisper_model_cached(model: str) -> bool | None:
    """True when ``model`` is on disk (a directory given by path counts),
    False when it isn't, None when the name isn't one this module knows
    (then nobody should grey anything)."""
    if isinstance(model, str) and model and os.path.isdir(model):
        return True
    if repo_id_for(model) is None:
        return None
    return bool(snapshot_dirs(model))


__all__ = [
    "WHISPER_REPOS",
    "hf_hub_cache_dir",
    "repo_dir",
    "repo_id_for",
    "snapshot_dirs",
    "whisper_model_cached",
]
