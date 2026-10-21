"""The Windows satellite Setup this server hands out, and its setup codes.

Spare-device satellites on Windows, the double-click way (2026-10-21).
A household makes a Windows laptop a room with ``DomovoiSatelliteSetup.exe``
(windows/installer/DomovoiSatelliteSetup.iss): a small wizard in front of
the same installer the dashboard's PowerShell command runs
(``satellite/scripts/install-windows.ps1``). It needs two things from the
household, both shown on the dashboard: this server's address and a short
**setup code**. A download from the dashboard's link carries both in its
file name, so usually nobody types anything.

This module is the core's half:

* **Setup codes** (:func:`make_code`, :func:`lookup`). An admin's "make the
  code" (Settings → Satellites → add a spare device) asks for one through
  ``POST /v1/admin/satellite-setup/codes``; it names that room's enrolment
  payload (``domovoi-room:1?core=…&fp=…&room=…``, satellite/enrolment.py)
  for :data:`CODE_TTL_SEC` (24 hours, like the server's own first-run setup
  code). **The code is the payload's own hash** (:func:`code_for`: 60 bits
  of its SHA-256), so it commits to the server's fingerprint inside it.
  ``GET /v1/satellite-setup/codes/{code}?challenge=…`` answers the Setup
  with the payload and the satellite bundle's digest, exactly what the
  PowerShell command quotes, **signed with this server's identity key**
  over the Setup's fresh challenge (:func:`setup_message`). The Setup
  checks that the payload hashes to the code it was given, that the key
  is the one the payload's fingerprint names, and that the signature
  holds: a host that answers in this server's place (a rogue box, a
  renamed download, something on the path) can neither sign as it nor
  find another payload with that code. **A code is not a credential**: the payload
  carries no secret (an address, a public fingerprint, a room name), the
  device still has to be approved with its own six-digit code, and the
  server still has to prove the fingerprint before anything is written. A
  code is only a short name for a payload, so it may sit in a file name
  and in a URL (which a log may keep): it expires, and it names nothing
  secret. Codes live in this process's memory only: a restart forgets
  them (the dashboard makes a new one), and at most :data:`MAX_CODES` are
  live, the oldest dropped first.
* **The Setup file** (:func:`fetch`, :func:`cached_file`, :func:`status`).
  A server installed on Windows gets it with its own Setup: DomovoiSetup.exe
  carries the DomovoiSatelliteSetup.exe of the same build, and DomovoiHost
  places it, with its record, in this cache at every start (a version
  that matches, offline). For a Linux server,
  ``DomovoiSatelliteSetup.exe`` is attached to Domovoi's GitHub releases
  by scripts/release/finish-release.ps1, beside ``DomovoiSetup.exe``, and
  listed in that release's ``SHA256SUMS``. Only when an admin presses the
  dashboard's button, this server downloads it from the release that
  ``android/release.json`` names (the release this checkout belongs to),
  else from the latest release, through the outbound gate
  (domovoi/net_safety.py), and keeps it only if its SHA-256 is the one that
  release's ``SHA256SUMS`` lists and it is a Windows program. It is not
  code-signed yet (owner decision 2026-10-21), so what vouches for it is
  GitHub's HTTPS and the release's own checksum list: said plainly in
  docs/SECURITY_PRIVACY.md. Refused under ``INTERNET_ACCESS=never``. The
  laptops then download it from this server, on the LAN.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SETUP_ASSET = "DomovoiSatelliteSetup.exe"
SUMS_ASSET = "SHA256SUMS"
#: The one place the Setup comes from: this repo's GitHub releases.
RELEASE_DOWNLOAD = "https://github.com/coders-farm-official/domovoi/releases/download/"
LATEST_DOWNLOAD = "https://github.com/coders-farm-official/domovoi/releases/latest/download/"
CACHE_BUCKET = "satellite_setup"
RECORD_NAME = "setup.json"
MAX_SETUP_BYTES = 64 * 1024 * 1024
MAX_SUMS_BYTES = 64 * 1024
#: The public routes (domovoi/main.py).
CODES_PATH = "/v1/satellite-setup/codes/"
DOWNLOAD_PATH = "/v1/satellite-setup/download/"
#: The short form a person types into the laptop's browser.
SHORT_PATH = "/win/"

#: How long a setup code names its room.
CODE_TTL_SEC = 24 * 3600
MAX_CODES = 64
#: Crockford's base 32: no I, L, O or U, so a code read aloud or typed from
#: a phone's screen has no look-alikes; 12 of them are 60 bits of the
#: payload's SHA-256 (:func:`code_for`).
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LEN = 12
#: What the identity key signs for the Setup (:func:`setup_message`).
SETUP_CONTEXT = b"domovoi-satellite-setup-v1"
_CHALLENGE_RE = re.compile(r"^[0-9a-f]{32}$")
_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
#: A host the file name may carry: an IPv4 address or a DNS name (no IPv6,
#: whose colons a Windows file name can't hold).
_NAME_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")


class SetupCodeError(ValueError):
    """A payload a setup code can't name (the message is safe to show)."""


# ─── setup codes ──────────────────────────────────────────────────────────

_codes: dict[str, dict[str, Any]] = {}
_codes_lock = threading.Lock()


def normalize_code(text: str) -> str | None:
    """The code as stored (12 characters of :data:`ALPHABET`), from what a
    person typed: case, dashes and spaces don't matter, and O, I and L are
    read as 0, 1 and 1. None when it can't be a code."""
    if not isinstance(text, str) or len(text) > 32:
        return None
    code = re.sub(r"[\s-]", "", text).upper().translate(_LOOKALIKES)
    if len(code) != CODE_LEN or any(c not in ALPHABET for c in code):
        return None
    return code


def display_code(code: str) -> str:
    """``K7M4QX2P9TRA`` as people read it: ``K7M4-QX2P-9TRA``."""
    return f"{code[:4]}-{code[4:8]}-{code[8:]}"


def code_for(payload: str) -> str:
    """The setup code of ``payload``: the first 60 bits of its SHA-256 (of
    its UTF-8 bytes), in :data:`ALPHABET`, most significant first. The
    Setup's worker (windows/installer/satellite-setup.ps1) computes the
    same and refuses an answer whose payload has another code: finding a
    second payload, with another fingerprint in it, that has a given code
    is a 2^60 search."""
    top = int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:8], "big") >> 4
    return "".join(ALPHABET[(top >> (5 * (CODE_LEN - 1 - i))) & 31] for i in range(CODE_LEN))


def valid_challenge(text: object) -> bool:
    """A Setup's challenge: 16 random bytes in lower-case hex."""
    return isinstance(text, str) and bool(_CHALLENGE_RE.match(text))


def setup_message(challenge: str, code: str, payload: str, bundle_sha256: str) -> bytes:
    """The bytes this server's identity key signs for a Setup: the Setup's
    challenge (so an old answer can't be replayed), the code, the payload
    and the bundle's SHA-256, one per line. Every part is ASCII (the
    payload is percent-encoded)."""
    return b"\n".join([SETUP_CONTEXT, challenge.encode("ascii"), code.encode("ascii"),
                       payload.encode("utf-8"), bundle_sha256.encode("ascii")])


def _expire(now: float) -> None:
    for code in [c for c, t in _codes.items() if t["expires_at"] <= now]:
        del _codes[code]


def make_code(payload: str, *, now: float | None = None) -> dict[str, Any]:
    """A setup code for ``payload`` (refused unless satellite/enrolment.py
    parses it), live for :data:`CODE_TTL_SEC` from now. Asking again for
    the same payload while its code lives gives the same code, with the
    time renewed. ``{code, expires_at, payload, core, room, room_id}``."""
    from satellite import enrolment

    try:
        parsed = enrolment.parse(payload)
    except enrolment.EnrolmentError as e:
        raise SetupCodeError(f"not an enrolment payload: {e}") from None
    now = time.time() if now is None else now
    with _codes_lock:
        _expire(now)
        code = code_for(payload)
        if code not in _codes:
            while len(_codes) >= MAX_CODES:
                del _codes[min(_codes, key=lambda c: _codes[c]["made_at"])]
            _codes[code] = {"payload": payload, "made_at": now}
        _codes[code]["expires_at"] = now + CODE_TTL_SEC
        entry = dict(_codes[code])
    return {"code": code, "expires_at": entry["expires_at"], "payload": payload,
            "core": parsed.core, "room": parsed.room, "room_id": parsed.room_id}


def lookup(text: str, *, now: float | None = None) -> str | None:
    """The payload a live code names, or None (unknown, expired, or not a
    code at all)."""
    code = normalize_code(text)
    if code is None:
        return None
    now = time.time() if now is None else now
    with _codes_lock:
        _expire(now)
        entry = _codes.get(code)
        return entry["payload"] if entry else None


def expires_at(text: str) -> float | None:
    code = normalize_code(text)
    with _codes_lock:
        entry = _codes.get(code) if code else None
        return entry["expires_at"] if entry else None


def forget_codes() -> None:
    """Tests: drop every code."""
    with _codes_lock:
        _codes.clear()


def server_text(core: str) -> str:
    """The server as the Setup's "Domovoi server" box takes it, shortest
    first: ``192.168.1.20`` for ``http://192.168.1.20:6370`` (the Setup
    adds http:// and :6370), ``domovoi.local:6400`` for another port, the
    whole URL for https."""
    parts = urllib.parse.urlsplit(core)
    host = parts.hostname or ""
    shown = f"[{host}]" if ":" in host else host
    if parts.scheme != "http":
        return core
    return shown if (parts.port or 80) == 6370 else f"{shown}:{parts.port or 80}"


def download_name(code: str, core: str) -> str:
    """The file name a download from the dashboard's link gets, which the
    Setup reads its code and server back out of:
    ``DomovoiSatelliteSetup_K7M4QX2P9TRA_192.168.1.20_6370.exe``. An https
    server or an IPv6 address doesn't fit a file name: then it carries the
    code only (``DomovoiSatelliteSetup_K7M4QX2P9TRA.exe``) and the Setup asks
    for the server. A browser that adds " (1)" to a second download keeps
    it readable."""
    stem = f"DomovoiSatelliteSetup_{code}"
    parts = urllib.parse.urlsplit(core)
    host = (parts.hostname or "").lower()
    if parts.scheme == "http" and _NAME_HOST_RE.match(host) and ".." not in host:
        return f"{stem}_{host}_{parts.port or 80}.exe"
    return f"{stem}.exe"


# ─── the Setup file, from the GitHub release ──────────────────────────────

_job: dict[str, Any] = {"running": False, "started_at": None, "finished_at": None, "ok": None,
                        "message": None}
_job_lock = threading.Lock()


def _bucket() -> Path:
    from domovoi.satellite_media import cache

    return cache.bucket(CACHE_BUCKET)


def release_tag() -> str | None:
    """The release this checkout belongs to: the tag android/release.json
    names (the release scripts write it at the tag), or None before the
    first release."""
    from domovoi import android_app

    try:
        rel = android_app.load_manifest().get("release")
    except android_app.ManifestError:
        return None
    return rel.get("tag") if isinstance(rel, dict) else None


def sources(tag: str | None) -> list[tuple[str, str]]:
    """``(label, base URL)`` to try, in order: this checkout's release,
    then the latest one (an older release has no Setup)."""
    out = []
    if tag and re.fullmatch(r"v[0-9][0-9A-Za-z.-]{0,40}", tag):
        out.append((tag, f"{RELEASE_DOWNLOAD}{tag}/"))
    out.append(("latest", LATEST_DOWNLOAD))
    return out


def parse_sums(text: str, name: str = SETUP_ASSET) -> str | None:
    """The SHA-256 ``SHA256SUMS`` lists for ``name`` (``<hex>  <name>``,
    sha256sum's format, a ``*`` before the name allowed), or None."""
    for line in text.splitlines():
        m = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line.strip())
        if m and m.group(2) == name:
            return m.group(1).lower()
    return None


def _client():
    """The egress-hooked client (each request, redirects included, refused
    under ``never``). Tests replace it."""
    from domovoi.satellite_media import fetchers

    return fetchers._oww_http_client()


def _get(url: str, dest: Path | None, limit: int) -> bytes | str:
    """GET ``url`` through net_safety (every hop to a public address),
    reading at most ``limit`` bytes: into memory (``dest`` None) or into
    ``dest``, returning its SHA-256. Raises ValueError (``HTTP 404`` for a
    release without the file)."""
    from domovoi import net_safety

    h = hashlib.sha256()
    body = bytearray()
    written = 0
    with _client() as client:
        response = net_safety.open_stream_sync(client, url)
        try:
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}")
            f = open(dest, "wb") if dest is not None else None
            try:
                for chunk in response.iter_bytes(1 << 20):
                    written += len(chunk)
                    if written > limit:
                        raise ValueError(f"larger than {limit} bytes")
                    h.update(chunk)
                    if f is None:
                        body.extend(chunk)
                    else:
                        f.write(chunk)
            finally:
                if f is not None:
                    f.close()
        finally:
            response.close()
    return bytes(body) if dest is None else h.hexdigest()


def read_record() -> dict[str, Any] | None:
    try:
        doc = json.loads((_bucket() / RECORD_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not _SHA_RE.match(str(doc.get("sha256"))) or \
            not isinstance(doc.get("size"), int):
        return None
    return doc


def cached_file() -> Path | None:
    """The Setup this server holds, if its bytes are still the ones it
    checked when it downloaded them; else None."""
    from domovoi import satellite_windows

    rec = read_record()
    if rec is None:
        return None
    path = _bucket() / SETUP_ASSET
    return path if satellite_windows._matches(path, rec["sha256"], rec["size"]) else None


#: The record DomovoiHost writes beside a Setup it placed (windows/host):
#: ``release`` is this, so the card knows it came with the server.
BUNDLED = "bundled"
#: How long a look at the releases is trusted (:func:`release_with_setup`).
PROBE_TTL_SEC = 6 * 3600
PROBE_RETRY_SEC = 10 * 60
_probe: dict[str, Any] = {"at": 0.0, "found": None}
_probe_lock = threading.Lock()


def windows_install() -> bool:
    """Whether this server is the one Domovoi's Windows Setup installed
    (inside its WSL distro): its Setup brings the satellite's, and nothing
    is fetched from GitHub for it."""
    from domovoi import host_kind

    try:
        return host_kind.is_wsl()
    except Exception:  # noqa: BLE001
        return False


def release_with_setup(*, now: float | None = None) -> str | None:
    """The release (this checkout's, else the latest) whose ``SHA256SUMS``
    lists the Setup, or None: what "get the Windows Setup" would fetch.
    Asked of GitHub at most every :data:`PROBE_TTL_SEC` (a no every
    :data:`PROBE_RETRY_SEC`), never under ``INTERNET_ACCESS=never``, never
    on a Windows install; a checksum list is all it reads."""
    from domovoi import egress

    if windows_install() or egress.internet_turned_off():
        return None
    now = time.time() if now is None else now
    with _probe_lock:
        ttl = PROBE_TTL_SEC if _probe["found"] else PROBE_RETRY_SEC
        if _probe["at"] and now - _probe["at"] < ttl:
            return _probe["found"]
    found = None
    for label, base in sources(release_tag()):
        try:
            sums = _get(base + SUMS_ASSET, None, MAX_SUMS_BYTES)
        except Exception:  # noqa: BLE001 — no answer is "not there"
            continue
        if isinstance(sums, bytes) and parse_sums(sums.decode("utf-8", "replace")):
            found = label
            break
    with _probe_lock:
        _probe.update(at=now, found=found)
    return found


def forget_probe() -> None:
    with _probe_lock:
        _probe.update(at=0.0, found=None)


def status(*, probe: bool = True) -> dict[str, Any]:
    """What the card shows. ``fetchable``: the label of the release "get the
    Windows Setup" would fetch from (a Linux server that doesn't hold it
    yet), else None, and then the card offers no such button."""
    from domovoi import egress

    rec = read_record() if cached_file() is not None else None
    windows = windows_install()
    fetchable = None
    if rec is None and not windows and probe:
        fetchable = release_with_setup()
    return {
        "ready": rec is not None,
        "file": SETUP_ASSET,
        "size": rec["size"] if rec else None,
        "sha256": rec["sha256"] if rec else None,
        "release": rec.get("release") if rec else None,
        "bundled": bool(rec and rec.get("release") == BUNDLED),
        "fetched_at": rec.get("fetched_at") if rec else None,
        "windows_install": windows,
        "fetchable": fetchable,
        "internet_off": egress.internet_turned_off(),
        "fetch": dict(_job),
    }


def fetch() -> tuple[bool, str]:
    """Download the Setup from the GitHub release (see the module notes),
    keep it only if ``SHA256SUMS`` there vouches for it. Blocking: run it
    off the event loop. ``(ok, message)``."""
    from domovoi import egress

    if windows_install():
        return False, ("this server came with Domovoi's Windows Setup, which brings the satellite Setup "
                       "with it: run the newest DomovoiSetup.exe to update it")
    if egress.internet_turned_off():
        return False, ("internet access is off (Settings → Internet), so this server can't download "
                       "the Windows Setup")
    dest = _bucket()
    problems: list[str] = []
    for label, base in sources(release_tag()):
        try:
            sums = _get(base + SUMS_ASSET, None, MAX_SUMS_BYTES)
            assert isinstance(sums, bytes)
            want = parse_sums(sums.decode("utf-8", "replace"))
            if want is None:
                problems.append(f"{label}: no Setup in that release")
                continue
            part = dest / f".{SETUP_ASSET}.download"
            try:
                got = _get(base + SETUP_ASSET, part, MAX_SETUP_BYTES)
                if got != want:
                    raise ValueError(f"checksum mismatch: {SUMS_ASSET} says {want}, got {got}")
                with open(part, "rb") as f:
                    if f.read(2) != b"MZ":
                        raise ValueError("not a Windows program")
                size = part.stat().st_size
                os.replace(part, dest / SETUP_ASSET)
            finally:
                part.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001 — one source's failure, said and the next tried
            problems.append(f"{label}: {e}")
            continue
        record = {"sha256": want, "size": size, "release": label, "fetched_at": time.time()}
        (dest / RECORD_NAME).write_text(json.dumps(record, indent=2), encoding="utf-8")
        forget_probe()
        return True, f"{SETUP_ASSET} from the {label} release verified ({size} bytes)"
    # The details go to the log; the card says it in plain words.
    log.warning("Windows satellite Setup not fetched: %s", "; ".join(problems)[:900])
    return False, ("the Windows Setup could not be downloaded from Domovoi's releases just now; "
                   "try again later, or use the PowerShell command")


def start_fetch() -> bool:
    """Run :func:`fetch` on a thread unless one is running. True if started."""
    with _job_lock:
        if _job["running"]:
            return False
        _job.update(running=True, started_at=time.time(), finished_at=None, ok=None, message=None)

    def run() -> None:
        try:
            ok, message = fetch()
        except Exception as e:  # noqa: BLE001
            ok, message = False, f"{type(e).__name__}: {e}"
        with _job_lock:
            _job.update(running=False, finished_at=time.time(), ok=ok, message=message)
        log.info("Windows satellite Setup: %s", message)

    threading.Thread(target=run, name="satellite-setup-fetch", daemon=True).start()
    return True
