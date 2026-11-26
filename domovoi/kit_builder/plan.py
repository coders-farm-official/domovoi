"""The card's view of a kit before it is made: what the boxes put on it,
how big it is, whether it fits the stick, what must be downloaded first,
and why it can't be made now (design section 2).

:func:`release` finds the release this server runs (a kit is always
that release). :func:`catalogue` lists every part (parts.py), cached for
a short while: it reads the pins from the release commit and looks at
the model and package caches. :func:`plan` applies a selection.

Sizes are exact for every file the server already has or has listed (a
pinned size, a model's manifest, a cached mirror, PyPI's file sizes); the
first kit on a server shows "about" for the apt packages and the images
until they have been fetched once. The image size is the FAT32 image's,
from fatimage.image_size, so the fit check is the real one.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from domovoi.offline_kit import format as fmt

from . import fatimage, host, images, parts, pins
from .parts import MAC, Part, Release

log = logging.getLogger(__name__)

#: Stick sizes the card offers ("My stick is"), in marketed GB.
STICK_SIZES = (8, 16, 32, 64, 128, 256)
#: What a marketed "N GB" stick holds, conservatively: N × 10⁹ bytes less
#: 4% (sticks are often a little under; a "16 GB" stick is ~14.9 GiB).
STICK_USABLE = 0.96
#: Kept free on the server beyond the image and the downloads.
DISK_MARGIN = 1024 * 1024 * 1024
CATALOGUE_TTL_SEC = 30.0

INCLUDE_ORDER = ("ai", "vision", "rooms")


@dataclass
class Selection:
    targets: tuple[str, ...] = fmt.DEFAULT_TARGETS
    includes: tuple[str, ...] = INCLUDE_ORDER
    household: tuple[str, ...] = ()
    stick_gb: int | None = None

    @classmethod
    def from_body(cls, body: dict[str, Any] | None) -> "Selection":
        body = body or {}
        targets = tuple(t for t in body.get("targets", fmt.DEFAULT_TARGETS) if t in fmt.TARGETS)
        includes = tuple(i for i in body.get("includes", INCLUDE_ORDER) if i in fmt.INCLUDES)
        household = tuple(str(h) for h in body.get("household", ()) if isinstance(h, str))[:64]
        stick = body.get("stick_gb")
        stick = stick if isinstance(stick, int) and stick in STICK_SIZES else None
        return cls(targets=targets, includes=includes, household=household, stick_gb=stick)


# ─── the release this server runs ────────────────────────────────────────

@dataclass
class ReleaseState:
    release: Release | None
    refusal: dict[str, str] | None
    latest: str | None = None
    behind: bool = False


def _vtuple(v: str) -> tuple[int, ...]:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def release_state(repo_dir: str) -> ReleaseState:
    """The release this server runs, or why a kit can't be made from it."""
    from domovoi import git_run, git_version

    not_release = {"reason": "not_release", "message": (
        "Offline kits are made from a release. This server follows main. Switch to stable in "
        "About & updates.")}
    try:
        channel = git_version.tracking().get("channel")
        r = git_run.run(["git", "describe", "--exact-match", "--tags", "--match", "v[0-9]*", "HEAD"],
                        cwd=repo_dir, timeout=15)
        c = git_run.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, timeout=15)
    except Exception as e:  # noqa: BLE001 — no git: say so on the card
        return ReleaseState(None, {"reason": "no_git", "message": f"This server's version can't be read ({e})."})
    tag = r.stdout.strip() if r.returncode == 0 else ""
    if channel != "stable" or not tag:
        if channel == "stable":
            not_release["message"] = ("Offline kits are made from a release, and this server runs "
                                      "code past its release. Update it to the latest release first.")
        return ReleaseState(None, not_release)
    version = tag[1:]
    if not fmt.VERSION_RE.match(version):
        return ReleaseState(None, not_release)
    rel = Release(version=version, tag=tag, commit=c.stdout.strip())
    try:
        latest = git_version.latest_release()
    except Exception:  # noqa: BLE001
        latest = None
    behind = bool(latest) and _vtuple(latest or "") > _vtuple(version)
    return ReleaseState(rel, None, latest, behind)


# ─── the catalogue ───────────────────────────────────────────────────────

@dataclass
class Catalogue:
    release: Release
    parts: list[Part]
    refused_models: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    made: float = 0.0
    #: Targets this release can't make a kit for, with the card's reason
    #: (parts.target_problems: a Mac with no Ollama for macOS pinned).
    target_problems: dict[str, str] = field(default_factory=dict)


_cat_lock = threading.Lock()
_cat: Catalogue | None = None


def invalidate() -> None:
    global _cat
    with _cat_lock:
        _cat = None


def catalogue(rel: Release, repo_dir: str, *, read: pins.Reader | None = None,
              mpd_tag: str | None = None, fresh: bool = False) -> Catalogue:
    """Every part this server could put on a kit of ``rel``."""
    global _cat
    with _cat_lock:
        if (not fresh and _cat is not None and _cat.release.commit == rel.commit
                and time.time() - _cat.made < CATALOGUE_TTL_SEC):
            return _cat
    from domovoi import egress

    try:
        p = pins.load(read or pins.git_reader(repo_dir, rel.commit), rel.commit)
    except pins.PinError as e:
        # Said on the card; not cached, so a fixed checkout is read again.
        return Catalogue(release=rel, parts=[], problems=[f"{rel.tag}'s pins can't be used: {e}"],
                         made=time.time())
    problems: list[str] = []
    if not rel.assets:
        try:
            # From the cache, else GitHub's release API (through the gate).
            rel.assets = parts.release_assets(rel.tag)
        except egress.InternetTurnedOff:
            problems.append(f"This server has no internet, so it can't fetch: the list of "
                            f"{rel.tag}'s files. Allow internet for a while.")
        except Exception as e:  # noqa: BLE001 — said on the card
            problems.append(f"The list of {rel.tag}'s files can't be read ({e}).")
    out: list[Part] = []
    out += parts.release_parts(rel)
    out += parts.source_parts(rel, repo_dir)
    out += parts.apt_parts(p)
    out += parts.wheel_parts(p)
    out += parts.image_parts(p, mpd_tag)
    out += parts.speech_parts(p)
    out += parts.wake_parts()
    out += parts.phone_parts(p)
    out += parts.platform_parts(p)
    out += parts.ollama_runtime_parts(p)
    models = parts.model_parts(p)
    out += models
    out += parts.rooms_parts(rel, p)
    out += parts.household_model_parts(p)
    out += parts.household_wake_parts()
    out.append(parts.licence_part(rel, repo_dir, models))
    cat = Catalogue(release=rel, parts=out, refused_models=parts.refused_models(p), problems=problems,
                    made=time.time(), target_problems=parts.target_problems(p))
    with _cat_lock:
        _cat = cat
    return cat


# ─── a selection ─────────────────────────────────────────────────────────

def selected(cat: Catalogue, sel: Selection) -> list[Part]:
    out = []
    for p in cat.parts:
        if not p.wanted(sel.targets):
            continue
        if p.group == "household":
            if p.id in sel.household:
                out.append(p)
        elif p.group == "base" or p.group in sel.includes:
            out.append(p)
    return out


def _file_sizes(ps: list[Part], part_size: int = fmt.PART_SIZE) -> list[tuple[str, int]]:
    """Stand-in (path, size) files for the image size: each part's bytes
    spread over its file count, big files split as the writer splits."""
    files: list[tuple[str, int]] = [("kit.json", 256 * 1024), ("SHA256SUMS", 128 * 1024),
                                    ("START-HERE.html", 64 * 1024), ("START-HERE.txt", 32 * 1024)]
    for p in ps:
        n = max(1, p.files_estimate)
        each = p.size // n
        folder = fmt.KIND_FOLDERS.get(p.kind, "x/")
        for i in range(n):
            size = each + (p.size - each * n if i == 0 else 0)
            chunks = fmt.part_sizes(size, part_size) or [size]
            for j, c in enumerate(chunks):
                files.append((f"{folder}{p.id.replace(':', '_')}/{i}.{j}", c))
    return files


def image_bytes(ps: list[Part]) -> int:
    return fatimage.image_size(_file_sizes(ps))


def stick_capacity(gb: int) -> int:
    return int(gb * 1e9 * STICK_USABLE)


def smallest_stick(total: int) -> int | None:
    return next((g for g in STICK_SIZES if stick_capacity(g) >= total), None)


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


def _group_bytes(cat: Catalogue, sel: Selection, group: str) -> int:
    return sum(p.size for p in cat.parts if p.group == group and p.wanted(sel.targets))


def fit(cat: Catalogue, sel: Selection, total: int) -> dict[str, Any]:
    """The stick check: the smallest stick that holds it, and for the
    chosen size whether it fits, and if not, what to leave out."""
    need = smallest_stick(total)
    out: dict[str, Any] = {"needed_gb": need, "chosen_gb": sel.stick_gb, "fits": None, "over_by": 0,
                           "message": None}
    if sel.stick_gb is None:
        return out
    cap = stick_capacity(sel.stick_gb)
    if total <= cap:
        out.update(fits=True, message="Fits.")
        return out
    over = total - cap
    # What to leave out: the largest optional boxes first, until it fits.
    options = []
    for inc in INCLUDE_ORDER:
        if inc in sel.includes:
            options.append((fmt.INCLUDES[inc], _group_bytes(cat, sel, inc)))
    if MAC in sel.targets and len(sel.targets) > 1:
        options.append(("the Mac parts", sum(p.size for p in cat.parts if p.targets == (MAC,))))
    for hid in sel.household:
        hp = next((p for p in cat.parts if p.id == hid), None)
        if hp:
            options.append((hp.name, hp.size))
    options.sort(key=lambda o: o[1], reverse=True)
    drop, freed = [], 0
    for name, size in options:
        if freed >= over:
            break
        if size > 0:
            drop.append(f"{name} ({_gb(size)})")
            freed += size
    msg = f"Too big for {'an' if sel.stick_gb == 8 else 'a'} {sel.stick_gb} GB stick by {_gb(over)}."
    if drop and freed >= over:
        msg += " Leave out " + " and ".join(drop) + " to fit."
    else:
        msg += f" Use a {need} GB stick or bigger." if need else " It doesn't fit any stick the card offers."
    out.update(fits=False, over_by=over, message=msg)
    return out


def free_bytes(path: Path) -> int:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return 0


def plan(cat: Catalogue, sel: Selection, *, freeable: int = 0,
         tools_ok: Callable[[], bool] = lambda: fatimage.tools() is not None,
         store_problem: Callable[[], str | None] = images.store_problem,
         busy: bool = False) -> dict[str, Any]:
    """The card's numbers and the first refusal, for ``sel``."""
    from domovoi import egress

    ps = selected(cat, sel)
    total = image_bytes(ps)
    exact = all(p.exact for p in ps)
    downloads = [p for p in ps if p.download > 0]
    dl_bytes = sum(p.download for p in downloads)
    labels = list(dict.fromkeys(p.label or p.name for p in downloads))
    includes = [{"id": i, "label": fmt.INCLUDES[i], "bytes": _group_bytes(cat, sel, i),
                 "ticked": i in sel.includes} for i in INCLUDE_ORDER]
    household = [{"id": p.id, "name": p.name, "kind": p.kind, "bytes": p.size, "note": p.note,
                  "licence": p.licence, "ticked": p.id in sel.household}
                 for p in cat.parts if p.group == "household" and p.wanted(sel.targets)]
    need_disk = total + dl_bytes + DISK_MARGIN
    free = free_bytes(host.kit_root()) + freeable

    refusal = None
    if busy:
        refusal = {"reason": "busy", "message": "A kit is already being made."}
    elif not sel.targets:
        refusal = {"reason": "no_target", "message": "Tick at least one kind of computer."}
    elif cat.problems:
        refusal = {"reason": "release", "message": cat.problems[0]}
    elif (bad := next((t for t in sel.targets if t in cat.target_problems), None)) is not None:
        refusal = {"reason": "release", "message": cat.target_problems[bad]}
    elif not tools_ok():
        refusal = {"reason": "no_tools", "message": (
            "This server can't make kit images yet: it needs the dosfstools and mtools packages "
            "(sudo apt install dosfstools mtools).")}
    elif (why := store_problem()) is not None:
        refusal = {"reason": "docker", "message": why}
    elif downloads and egress.internet_turned_off():
        refusal = {"reason": "internet_off", "message": (
            "This server has no internet, so it can't fetch: " + ", ".join(labels)
            + ". Untick them, or allow internet for a while.")}
    elif free < need_disk:
        refusal = {"reason": "disk", "message": (
            f"Needs {_gb(need_disk)} free on the server. It has {_gb(free)}.")}
    return {
        "release": {"version": cat.release.version, "tag": cat.release.tag, "commit": cat.release.commit},
        "selection": {"targets": list(sel.targets), "includes": list(sel.includes),
                      "household": list(sel.household), "stick_gb": sel.stick_gb},
        "targets": [{"id": t, "label": fmt.TARGETS[t], "ticked": t in sel.targets,
                     "default": t in fmt.DEFAULT_TARGETS} for t in fmt.TARGETS],
        "includes": includes,
        "household": household,
        "refused_models": cat.refused_models,
        "total_bytes": total,
        "exact": exact,
        "download_bytes": dl_bytes,
        "download_labels": labels,
        "stick": fit(cat, sel, total),
        "stick_sizes": list(STICK_SIZES),
        "disk_free": free,
        "need_disk": need_disk,
        "refusal": refusal,
    }
