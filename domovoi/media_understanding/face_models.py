"""The two face models, fetched only when an admin turns faces on.

* **YuNet** (``face_detection_yunet_2023mar.onnx``): finds faces and five
  landmarks. OpenCV Zoo, ``models/face_detection_yunet``: MIT licence
  (Copyright (c) 2020 Shiqi Yu).
* **SFace** (``face_recognition_sface_2021dec.onnx``): one 128-number
  embedding per aligned face. OpenCV Zoo, ``models/face_recognition_sface``:
  Apache License 2.0. Its model card does not name the training data
  (docs/LICENSES.md says so; the owner accepted it 2026-11-22).

Never InsightFace weights (non-commercial research only), never shipped in
the repo or an installer. Each file is fetched from a URL pinned to one
OpenCV Zoo commit and held to a pinned size and SHA-256 (the Git LFS
object id of that file), through the egress gate: under
``INTERNET_ACCESS=never`` nothing is fetched, and faces stay unavailable
(:func:`problem` says why). A file that doesn't match is never moved into
place. A new upstream export means a new pin and a new :data:`MODEL_TAG`
(the embeddings would not compare), never a looser check.

The core's runner calls :func:`download` (in a thread) when the switch is
on and the files aren't there; nothing else does. Both processes may ask
:func:`ready` (the dashboard's status).
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

#: The OpenCV Zoo commit both URLs are pinned to.
ZOO_COMMIT = "48b10fa505aeb51fbd127fc60a5c660040e823b7"
_ZOO = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{ZOO_COMMIT}/models/"


@dataclass(frozen=True)
class Pin:
    role: str           # "detector" | "recognizer"
    file: str
    url: str
    sha256: str
    size: int
    licence: str


DETECTOR = Pin(
    role="detector",
    file="face_detection_yunet_2023mar.onnx",
    url=_ZOO + "face_detection_yunet/face_detection_yunet_2023mar.onnx",
    sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    size=232_589,
    licence="MIT",
)
RECOGNIZER = Pin(
    role="recognizer",
    file="face_recognition_sface_2021dec.onnx",
    url=_ZOO + "face_recognition_sface/face_recognition_sface_2021dec.onnx",
    sha256="0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    size=38_696_353,
    licence="Apache-2.0",
)
PINS: tuple[Pin, ...] = (DETECTOR, RECOGNIZER)

#: The model tag every stored face, group and denylist entry made by these
#: models carries. Embeddings with another tag are never compared with these.
MODEL_TAG = "sface-2021dec"

#: After a failed fetch, the next try waits this long.
RETRY_AFTER_SEC = 600.0


class ModelsUnavailable(RuntimeError):
    """A face model isn't on disk and couldn't be fetched."""


def models_dir() -> Path:
    from domovoi.config import settings

    return Path(settings.face_models_dir)


def path_of(pin: Pin, base: Path | None = None) -> Path:
    return (base or models_dir()) / pin.file


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# (path, size, mtime_ns) of files whose digest matched: hashed once per change.
_verified: set[tuple[str, int, int]] = set()


def on_disk(pin: Pin, base: Path | None = None) -> bool:
    """Whether ``pin``'s file is there with its pinned size and SHA-256."""
    p = path_of(pin, base)
    try:
        st = p.stat()
    except OSError:
        return False
    if st.st_size != pin.size or p.is_symlink():
        return False
    key = (str(p), st.st_size, st.st_mtime_ns)
    if key in _verified:
        return True
    if _sha256_file(p) != pin.sha256:
        return False
    _verified.add(key)
    return True


def ready(base: Path | None = None) -> bool:
    return all(on_disk(p, base) for p in PINS)


_lock = threading.Lock()
_last_failure: tuple[float, str] | None = None


def may_retry() -> bool:
    return _last_failure is None or time.monotonic() - _last_failure[0] >= RETRY_AFTER_SEC


def last_error() -> str | None:
    return _last_failure[1] if _last_failure else None


def _default_fetch(url: str, dest: Path, *, sha256: str, size: int) -> None:
    from domovoi.satellite_media.fetchers import _fetch_pinned

    _fetch_pinned(url, dest, sha256=sha256, size=size)


def download(base: Path | None = None, *,
             fetch: Callable[..., None] | None = None) -> Path:
    """Fetch every missing model into ``base`` (the face-models folder):
    through the egress gate, each moved into place only once its size and
    SHA-256 match its pin, and checked again on disk afterwards. Raises
    ``egress.InternetTurnedOff`` under ``never`` (before any request),
    :class:`ModelsUnavailable` otherwise. Blocking: run it in a thread."""
    global _last_failure
    from domovoi import egress

    base = base or models_dir()
    fetch = fetch or _default_fetch
    with _lock:
        missing = [p for p in PINS if not on_disk(p, base)]
        if not missing:
            return base
        for pin in missing:
            egress.require_destination(pin.url)     # raises (and logs) under never
        base.mkdir(parents=True, exist_ok=True)
        for pin in missing:
            target = path_of(pin, base)
            log.info("faces: downloading the %s model (%.1f MB, one-time)", pin.role, pin.size / 1e6)
            try:
                fetch(pin.url, target, sha256=pin.sha256, size=pin.size)
            except egress.InternetTurnedOff:
                raise
            except Exception as e:  # noqa: BLE001 — said in the status, retried later
                why = f"the {pin.role} model: {str(e) or type(e).__name__}"
                _last_failure = (time.monotonic(), why)
                log.warning("faces: %s", why)
                raise ModelsUnavailable(why) from None
            if not on_disk(pin, base):
                # Whatever the fetcher did, a file that doesn't match its pin
                # is never used.
                target.unlink(missing_ok=True)
                why = f"the {pin.role} model did not match its pinned checksum"
                _last_failure = (time.monotonic(), why)
                raise ModelsUnavailable(why)
        _last_failure = None
        log.info("faces: models verified")
        return base


def problem() -> str | None:
    """Why faces can't run, in words for the household; None when ready."""
    from domovoi import egress

    if ready():
        return None
    if egress.internet_turned_off():
        return ("Recognizing faces needs two small models (about 39 MB) that aren't on this "
                "server yet, and internet access is turned off, so Domovoi can't download them.")
    if _last_failure is not None:
        return f"Domovoi couldn't download the face models ({_last_failure[1]}). It tries again later."
    return "Domovoi is downloading the face models (about 39 MB, one time)."


def _reset_for_tests() -> None:
    global _last_failure
    _last_failure = None
    _verified.clear()
