"""The Android app, handed to the household's phones by its own server.

Spare devices, 2026-10-15 (design-notes EASY-SATELLITES-ASSESSMENT §4.2).
A person with an old phone scans one QR code on the dashboard; the page it
opens (``GET /join`` on this server, domovoi/join_page/) offers the
Domovoi app from **this server**, then opens it as the room. No store, no
file juggling.

What may be handed out is pinned in the repo: ``android/release.json``,
written by the owner's release script (android/scripts/release.ps1 or
release.sh) when he signs and publishes a release on GitHub. It names the
release (version, versionCode, tag), the **SHA-256 of the release key's
signing certificate**, and per ABI (arm64-v8a, armeabi-v7a) the APK's
file name, GitHub release URL, size and SHA-256. ``release`` is null until
the first release exists, and then every page says so plainly.

This module is the core's half, shaped like the Windows satellite's
runtime (domovoi/satellite_windows.py):

* :func:`fetch` — only when an admin presses the dashboard's button
  (never by itself: owner decision 2026-10-15), each APK the manifest names is
  downloaded from that GitHub release only (the URL is fixed by the tag
  and file name; through the outbound gate, domovoi/net_safety.py) into
  ``~/.domovoi/satellite_media/cache/android_app/<versionCode>/`` and kept
  only if :func:`check_apk` passes. Refused under ``INTERNET_ACCESS=never``.
* :func:`accept_upload` — the same files dropped in by an admin (a house
  with no internet, or a slow line): kept only if one of the manifest's
  entries matches it exactly and :func:`check_apk` passes.
* :func:`check_apk` — size, SHA-256, **the APK's own v2/v3 signature
  verified and its certificate equal to the pinned one**
  (domovoi/apk_signature.py), and native libraries for exactly the
  entry's ABI.
* :func:`cached_file` — what ``GET /v1/android-app/files/{name}`` serves:
  only a file the manifest names whose bytes pass :func:`check_apk` (held
  in memory per file change, so a file is checked once, not per request).
  Public on the LAN: a phone that has not joined the household downloads
  it, like the satellite code bundle.
* :func:`status` / :func:`public_status` — what the dashboard card and the
  join page show.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO / "android" / "release.json"
PACKAGE = "com.domovoi.app"
#: The one place release APKs come from: this repo's GitHub releases.
RELEASE_DOWNLOAD = "https://github.com/coders-farm-official/domovoi/releases/download/"
#: The ABIs a release carries, in the order pages offer them: "most
#: phones" first, "older or cheaper phones" second.
ABIS = ("arm64-v8a", "armeabi-v7a")
MAX_APK_BYTES = 200 * 1024 * 1024
FILES_PATH = "/v1/android-app/files/"
CACHE_BUCKET = "android_app"

_VERSION_RE = re.compile(r"^[0-9]{1,4}(\.[0-9]{1,4}){1,3}(-[0-9A-Za-z.]{1,20})?$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class ManifestError(ValueError):
    """``android/release.json`` is missing or malformed."""


class ApkRefused(ValueError):
    """An APK that does not match the manifest. ``reason`` is one of
    ``size``, ``sha256``, ``signature``, ``cert``, ``abi``, ``unknown``."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


# ─── the manifest ─────────────────────────────────────────────────────────

def file_name(version_name: str, abi: str) -> str:
    """The release asset's name: ``domovoi-<version>-<abi>.apk``."""
    return f"domovoi-{version_name}-{abi}.apk"


def tag_for(version_name: str) -> str:
    return f"android-v{version_name}"


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    """The manifest, every field checked; ``release`` is None before the
    first release. Raises :class:`ManifestError`."""
    path = Path(path or MANIFEST_PATH)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ManifestError(f"the app release manifest is unreadable: {e}") from e
    if not isinstance(doc, dict) or doc.get("manifest_version") != 1:
        raise ManifestError("the app release manifest is not version 1")
    rel = doc.get("release")
    if rel is None:
        return {"manifest_version": 1, "release": None}
    if not isinstance(rel, dict):
        raise ManifestError("release must be an object or null")
    if rel.get("package") != PACKAGE:
        raise ManifestError(f"the release is not {PACKAGE}")
    version = rel.get("version_name")
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise ManifestError(f"bad version_name {version!r}")
    code = rel.get("version_code")
    if not isinstance(code, int) or isinstance(code, bool) or not 1 <= code <= 2_100_000_000:
        raise ManifestError(f"bad version_code {code!r}")
    if rel.get("tag") != tag_for(version):
        raise ManifestError(f"the tag must be {tag_for(version)}")
    cert = rel.get("cert_sha256")
    if not isinstance(cert, str) or not _SHA_RE.match(cert):
        raise ManifestError("cert_sha256 must be 64 lower-case hex digits")
    commit = rel.get("commit")
    if commit is not None and (not isinstance(commit, str) or not _COMMIT_RE.match(commit)):
        raise ManifestError("bad commit")
    published = rel.get("published")
    if published is not None and (not isinstance(published, str) or not _DATE_RE.match(published)):
        raise ManifestError("bad published date")
    apks = rel.get("apks")
    if not isinstance(apks, list) or not apks:
        raise ManifestError("the release names no APKs")
    seen: set[str] = set()
    out_apks = []
    for entry in apks:
        if not isinstance(entry, dict):
            raise ManifestError("an APK entry is not an object")
        abi = entry.get("abi")
        if abi not in ABIS:
            raise ManifestError(f"unknown ABI {abi!r} (one of {', '.join(ABIS)})")
        if abi in seen:
            raise ManifestError(f"{abi} is named twice")
        seen.add(abi)
        name = file_name(version, abi)
        if entry.get("file") != name:
            raise ManifestError(f"{abi}: the file must be {name}")
        if entry.get("url") != RELEASE_DOWNLOAD + tag_for(version) + "/" + name:
            raise ManifestError(f"{abi}: the URL is not this release's GitHub download")
        sha = entry.get("sha256")
        if not isinstance(sha, str) or not _SHA_RE.match(sha):
            raise ManifestError(f"{abi}: bad sha256")
        size = entry.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_APK_BYTES:
            raise ManifestError(f"{abi}: bad size")
        out_apks.append({"abi": abi, "file": name, "url": entry["url"], "sha256": sha, "size": size})
    out_apks.sort(key=lambda e: ABIS.index(e["abi"]))
    return {
        "manifest_version": 1,
        "release": {
            "package": PACKAGE, "version_name": version, "version_code": code,
            "tag": rel["tag"], "cert_sha256": cert, "commit": commit, "published": published,
            "apks": out_apks,
        },
    }


def _apks(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rel = manifest.get("release")
    return list(rel["apks"]) if rel else []


def _bucket_path(manifest: dict[str, Any]) -> Path | None:
    from domovoi.satellite_media import cache

    rel = manifest.get("release")
    return cache.CACHE_ROOT / CACHE_BUCKET / str(rel["version_code"]) if rel else None


def _bucket(manifest: dict[str, Any]) -> Path:
    path = _bucket_path(manifest)
    if path is None:
        raise ManifestError("there is no app release yet")
    path.mkdir(parents=True, exist_ok=True)
    return path


# ─── checking one APK ─────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def native_abis(path: Path) -> set[str]:
    """The ABIs an APK carries native libraries for (``lib/<abi>/…``)."""
    with zipfile.ZipFile(path) as z:
        return {n.split("/")[1] for n in z.namelist() if n.startswith("lib/") and n.count("/") >= 2}


def check_apk(path: Path, entry: dict[str, Any], cert_sha256: str) -> None:
    """Hold the file at ``path`` to its manifest ``entry``: the size, the
    SHA-256, the APK's own signature (v2, and v3 when present) verified,
    its certificate the release key's, and libraries for the entry's ABI
    only. Raises :class:`ApkRefused`."""
    from domovoi import apk_signature

    name = entry["file"]
    try:
        size = path.stat().st_size
    except OSError as e:
        raise ApkRefused("size", f"{name}: can't be read: {e}") from e
    if size != entry["size"]:
        raise ApkRefused("size", f"{name}: {size} bytes, the release says {entry['size']}")
    got = _sha256(path)
    if got != entry["sha256"]:
        raise ApkRefused("sha256", f"{name}: SHA-256 {got[:16]}…, the release says {entry['sha256'][:16]}…")
    try:
        signer = apk_signature.verify(path)
    except apk_signature.ApkSignatureError as e:
        raise ApkRefused("signature", f"{name}: {e}") from e
    if signer.cert_sha256 != cert_sha256:
        raise ApkRefused(
            "cert",
            f"{name}: signed by certificate {signer.cert_sha256[:16]}…, not the release key "
            f"{cert_sha256[:16]}…",
        )
    try:
        abis = native_abis(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise ApkRefused("abi", f"{name}: its files can't be listed: {e}") from e
    if abis != {entry["abi"]}:
        shown = ", ".join(sorted(abis)) or "none"
        raise ApkRefused("abi", f"{name}: carries libraries for {shown}, not {entry['abi']} only")


# (path, size, mtime_ns, pins) -> None when it passed, else the refusal's
# message: a file is checked once per change, not on every request.
_checked: dict[tuple[Any, ...], str | None] = {}
_checked_lock = threading.Lock()


def _passes(path: Path, entry: dict[str, Any], cert_sha256: str) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    if path.is_symlink() or not path.is_file() or st.st_size != entry["size"]:
        return False
    key = (str(path), st.st_size, st.st_mtime_ns, entry["sha256"], cert_sha256, entry["abi"])
    with _checked_lock:
        if key in _checked:
            return _checked[key] is None
    try:
        check_apk(path, entry, cert_sha256)
        verdict = None
    except ApkRefused as e:
        log.warning("Android app: %s", e)
        verdict = str(e)
    with _checked_lock:
        _checked[key] = verdict
    return verdict is None


def entry_for(manifest: dict[str, Any], name: str) -> dict[str, Any] | None:
    if not isinstance(name, str):
        return None
    return next((e for e in _apks(manifest) if e["file"] == name), None)


def cached_file(manifest: dict[str, Any], name: str) -> Path | None:
    """The cached APK called ``name`` if the manifest names it and its bytes
    pass :func:`check_apk`; else None."""
    entry = entry_for(manifest, name)
    bucket = _bucket_path(manifest)
    if entry is None or bucket is None:
        return None
    path = bucket / entry["file"]
    return path if _passes(path, entry, manifest["release"]["cert_sha256"]) else None


# ─── status ───────────────────────────────────────────────────────────────

def public_status(manifest: dict[str, Any]) -> dict[str, Any]:
    """What the join page reads (``GET /v1/android-app``, open on the LAN):
    the release, and each APK with whether this server holds it."""
    rel = manifest.get("release")
    if not rel:
        return {"release": None, "apks": [], "ready": False}
    apks = [
        {"abi": e["abi"], "file": e["file"], "size": e["size"],
         "ready": cached_file(manifest, e["file"]) is not None,
         "path": FILES_PATH + e["file"]}
        for e in rel["apks"]
    ]
    return {
        "release": {"version_name": rel["version_name"], "version_code": rel["version_code"],
                    "cert_sha256": rel["cert_sha256"]},
        "apks": apks,
        "ready": all(a["ready"] for a in apks),
    }


def status(manifest: dict[str, Any]) -> dict[str, Any]:
    """The admin card's view: :func:`public_status` plus what is missing,
    the sizes, the fetch's state and whether a fetch would be refused."""
    from domovoi import egress

    out = public_status(manifest)
    apks = out["apks"]
    out.update(
        missing=[a["file"] for a in apks if not a["ready"]],
        bytes_total=sum(a["size"] for a in apks),
        bytes_cached=sum(a["size"] for a in apks if a["ready"]),
        upload_max=max((a["size"] for a in apks), default=0),
        internet_off=egress.internet_turned_off(),
        fetch=dict(_job),
    )
    return out


# ─── fetch and upload ─────────────────────────────────────────────────────

def _tidy(manifest: dict[str, Any]) -> None:
    """Drop anything in the cache the manifest doesn't vouch for: strays in
    this release's folder, and older releases' folders."""
    from domovoi.satellite_media import cache

    bucket = _bucket_path(manifest)
    wanted = {e["file"] for e in _apks(manifest)}
    if bucket is not None and bucket.is_dir():
        for p in bucket.iterdir():
            if p.name in wanted or not (p.is_file() or p.is_symlink()):
                continue
            # A download or an upload still being written (dot files, made
            # by fetch and upload_target) is left alone for an hour.
            try:
                if p.name.startswith(".") and time.time() - p.lstat().st_mtime < 3600:
                    continue
            except OSError:
                continue
            p.unlink(missing_ok=True)
    root = cache.CACHE_ROOT / CACHE_BUCKET
    if root.is_dir():
        for p in root.iterdir():
            if bucket is None or p.resolve() != bucket.resolve():
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)


def _place(src: Path, entry: dict[str, Any], manifest: dict[str, Any]) -> Path:
    """Check ``src`` against ``entry`` and move it into the cache under the
    entry's name. ``src`` is removed either way."""
    try:
        check_apk(src, entry, manifest["release"]["cert_sha256"])
        dest = _bucket(manifest) / entry["file"]
        os.replace(src, dest)
        return dest
    finally:
        src.unlink(missing_ok=True)


_job: dict[str, Any] = {"running": False, "started_at": None, "finished_at": None, "ok": None, "message": None,
                        "bytes_done": 0, "bytes_todo": 0}
_job_lock = threading.Lock()


def fetch(manifest: dict[str, Any]) -> tuple[bool, str]:
    """Download every APK the manifest names that this server doesn't hold
    yet, each held to :func:`check_apk`. Blocking (minutes on a slow
    line): run it off the event loop. ``(ok, message)``."""
    from domovoi.satellite_media import fetchers

    if not manifest.get("release"):
        return False, "there is no app release yet"
    refused = fetchers._turned_off("the Android app")
    if refused:
        return refused
    dest = _bucket(manifest)
    cert = manifest["release"]["cert_sha256"]
    failed: list[str] = []
    fetched = 0
    todo = [e for e in _apks(manifest) if not _passes(dest / e["file"], e, cert)]
    with _job_lock:
        _job.update(bytes_done=0, bytes_todo=sum(e["size"] for e in todo))

    def progress(n: int) -> None:
        with _job_lock:
            _job["bytes_done"] += n

    for entry in todo:
        part = dest / f".{entry['file']}.download"
        try:
            fetchers._fetch_pinned(entry["url"], part, sha256=entry["sha256"], size=entry["size"],
                                   progress=progress)
            _place(part, entry, manifest)
        except Exception as e:  # noqa: BLE001 — one file's failure, said and moved on
            log.warning("Android app: %s refused: %s", entry["file"], e)
            failed.append(f"{entry['file']}: {e}")
            continue
        fetched += 1
    _tidy(manifest)
    total = len(_apks(manifest))
    if failed:
        return False, f"{len(failed)} of {total} app files failed: " + "; ".join(failed)[:900]
    return True, f"{total} app files verified ({fetched} downloaded)"


def start_fetch(manifest: dict[str, Any]) -> bool:
    """Run :func:`fetch` on a thread unless one is running. True if started."""
    with _job_lock:
        if _job["running"]:
            return False
        _job.update(running=True, started_at=time.time(), finished_at=None, ok=None, message=None,
                    bytes_done=0, bytes_todo=0)

    def run() -> None:
        try:
            ok, message = fetch(manifest)
        except Exception as e:  # noqa: BLE001
            ok, message = False, f"{type(e).__name__}: {e}"
        with _job_lock:
            _job.update(running=False, finished_at=time.time(), ok=ok, message=message)
        log.info("Android app: %s", message)

    threading.Thread(target=run, name="android-app-fetch", daemon=True).start()
    return True


def upload_target(manifest: dict[str, Any]) -> Path:
    """A fresh temporary file in this release's cache folder for an upload
    to be written to (same disk, so the move into place is atomic)."""
    import secrets

    return _bucket(manifest) / f".upload-{secrets.token_hex(8)}.part"


MANIFEST_ABOUT = [
    "The Android app release a Domovoi server hands to phones (domovoi/android_app.py, the join page).",
    "Written by android/scripts/release.ps1 or release.sh when a release is signed and published on GitHub;",
    "commit it with the release. Each APK is pinned by size and SHA-256, and the release key by its",
    "signing certificate's SHA-256: the server serves an APK only when its own signature is that key's.",
    "release is null until the first release exists. See android/README.md, 'Publishing a release'.",
]


def build_manifest(folder: Path, *, version_name: str, version_code: int, cert_sha256: str,
                   commit: str | None, published: str | None) -> dict[str, Any]:
    """The release.json document for the signed APKs in ``folder``
    (``domovoi-<version>-<abi>.apk`` for every ABI in :data:`ABIS`)."""
    apks = []
    for abi in ABIS:
        name = file_name(version_name, abi)
        path = Path(folder) / name
        apks.append({
            "abi": abi, "file": name, "url": RELEASE_DOWNLOAD + tag_for(version_name) + "/" + name,
            "sha256": _sha256(path), "size": path.stat().st_size,
        })
    return {
        "manifest_version": 1,
        "about": MANIFEST_ABOUT,
        "release": {
            "package": PACKAGE, "version_name": version_name, "version_code": version_code,
            "tag": tag_for(version_name), "commit": commit, "published": published,
            "cert_sha256": cert_sha256, "apks": apks,
        },
    }


def _main(argv: list[str]) -> int:
    """The release script's helpers (android/scripts/release.ps1 and
    release.sh), so the manifest's format and its checks live here only:

    * ``check MANIFEST DIR`` — hold every APK the manifest names, found by
      name in DIR, to :func:`check_apk`: exactly what a Domovoi server does
      before it serves them.
    * ``write OUT DIR --version-name V --version-code N --cert HEX
      [--commit SHA] [--published YYYY-MM-DD]`` — write the release.json
      for the signed APKs in DIR (and ``SHA256SUMS`` beside them), checked
      by :func:`load_manifest` before it is written.
    """
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="python -m domovoi.android_app")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("manifest")
    c.add_argument("dir")
    w = sub.add_parser("write")
    w.add_argument("out")
    w.add_argument("dir")
    w.add_argument("--version-name", required=True)
    w.add_argument("--version-code", required=True, type=int)
    w.add_argument("--cert", required=True)
    w.add_argument("--commit")
    w.add_argument("--published")
    args = ap.parse_args(argv)

    if args.cmd == "write":
        try:
            doc = build_manifest(Path(args.dir), version_name=args.version_name, version_code=args.version_code,
                                 cert_sha256=args.cert.lower(), commit=args.commit, published=args.published)
        except OSError as e:
            print(f"an APK is missing: {e}", file=sys.stderr)
            return 2
        out = Path(args.out)
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_bytes((json.dumps(doc, indent=2) + "\n").encode("utf-8"))
        try:
            load_manifest(tmp)
        except ManifestError as e:
            tmp.unlink(missing_ok=True)
            print(f"the manifest would be refused: {e}", file=sys.stderr)
            return 2
        os.replace(tmp, out)
        sums = "".join(f"{e['sha256']}  {e['file']}\n" for e in doc["release"]["apks"])
        (Path(args.dir) / "SHA256SUMS").write_bytes(sums.encode("ascii"))
        print(out)
        return 0

    try:
        manifest = load_manifest(Path(args.manifest))
    except ManifestError as e:
        print(f"manifest refused: {e}", file=sys.stderr)
        return 2
    rel = manifest["release"]
    if not rel:
        print("the manifest names no release", file=sys.stderr)
        return 2
    bad = 0
    for entry in rel["apks"]:
        try:
            check_apk(Path(args.dir) / entry["file"], entry, rel["cert_sha256"])
            print(f"ok   {entry['file']} ({entry['abi']}, {entry['size']} bytes)")
        except ApkRefused as e:
            bad += 1
            print(f"FAIL {e}", file=sys.stderr)
    return 1 if bad else 0


def accept_upload(manifest: dict[str, Any], src: Path) -> dict[str, Any]:
    """Keep the uploaded file at ``src`` if it is one of the release's APKs:
    the entry whose size and SHA-256 it has, then :func:`check_apk`.
    Returns that entry; raises :class:`ApkRefused` (``src`` is removed
    either way)."""
    try:
        rel = manifest.get("release")
        if not rel:
            raise ApkRefused("unknown", "there is no app release yet, so no file can be checked")
        size = src.stat().st_size
        digest = _sha256(src)
        entry = next((e for e in rel["apks"] if e["size"] == size and e["sha256"] == digest), None)
        if entry is None:
            names = "; ".join(f"{e['file']} ({e['size']} bytes)" for e in rel["apks"])
            raise ApkRefused(
                "unknown",
                f"this is not one of the Domovoi {rel['version_name']} app files ({size} bytes, "
                f"SHA-256 {digest[:16]}…). The release's files are: {names}",
            )
        _place(src, entry, manifest)
        _tidy(manifest)
        return entry
    finally:
        src.unlink(missing_ok=True)


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
