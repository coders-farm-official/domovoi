"""Write a kit folder from a list of components, and put split files back
together.

The server's kit builder only collects files: it hands ``write_kit`` the
components (what each is, its licence, where its files come from and where
they go on the stick) and this copies them, splits the big ones, fills in
the notices, and writes kit.json and SHA256SUMS last. The result passes
``verify``. ``reassemble`` is the installers' side: one split file joined
into a destination, checked as it goes.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Sequence

from . import format as fmt

_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Source:
    """One file of a component: ``path`` on the stick, and where its bytes
    come from (a file on the server, or the bytes themselves for small
    generated files such as START-HERE.txt)."""

    path: str
    src: Path | bytes


@dataclass(frozen=True)
class Component:
    kind: str
    name: str
    version: str
    licence: str
    files: Sequence[Source] = field(default_factory=tuple)
    household_added: bool = False
    pinned_by: str | None = None


def _open(src: Path | bytes) -> BinaryIO:
    if isinstance(src, bytes):
        return io.BytesIO(src)
    return open(src, "rb")


def _write_one(root: Path, source: Source, part_size: int) -> dict[str, Any]:
    """Copy one source to the kit, split when it is bigger than part_size.
    Its kit.json entry."""
    size = len(source.src) if isinstance(source.src, bytes) else os.stat(source.src).st_size
    sizes = fmt.part_sizes(size, part_size)
    whole = hashlib.sha256()
    entry: dict[str, Any] = {"path": source.path, "size": size}
    targets = list(zip(fmt.part_paths(source.path, len(sizes)), sizes)) if sizes else [(source.path, size)]
    parts = []
    with _open(source.src) as f:
        for path, want in targets:
            dest = root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256()
            left = want
            with open(dest, "xb") as out:
                while left:
                    chunk = f.read(min(_CHUNK, left))
                    if not chunk:
                        raise ValueError(f"{source.path}: its source got shorter while it was copied")
                    out.write(chunk)
                    h.update(chunk)
                    whole.update(chunk)
                    left -= len(chunk)
            parts.append({"path": path, "size": want, "sha256": h.hexdigest()})
        if f.read(1):
            raise ValueError(f"{source.path}: its source grew while it was copied")
    entry["sha256"] = whole.hexdigest()
    if sizes:
        entry["parts"] = parts
    return entry


def write_kit(root: str | Path, *, version: str, commit: str, components: Sequence[Component],
              targets: Sequence[str] = fmt.DEFAULT_TARGETS, includes: Sequence[str] = (),
              android_version_code: int | None = None, created_at: str | None = None,
              part_size: int = fmt.PART_SIZE) -> dict[str, Any]:
    """Write a kit into ``root`` (missing or empty). Returns kit.json as
    written. Raises ValueError, with every reason, when the kit would not
    follow the format; files already copied stay for the caller to remove."""
    root = Path(root)
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"{root} is not empty; a kit is written into an empty folder")
    root.mkdir(parents=True, exist_ok=True)
    # The metadata is checked before anything is copied.
    stub = _doc(version, commit, targets, includes, android_version_code, created_at, part_size,
                [_component_doc(c, [{"path": s.path, "size": 0, "sha256": "0" * 64}
                                    for s in c.files]) for c in components])
    problems, _ = fmt.validate(stub)
    if problems:
        raise ValueError("; ".join(problems))
    written: dict[str, dict[str, Any]] = {}
    comps = []
    for c in components:
        entries = []
        for s in c.files:
            if s.path not in written:
                written[s.path] = _write_one(root, s, part_size)
            entries.append(written[s.path])
        comps.append(_component_doc(c, entries))
    doc = _doc(version, commit, targets, includes, android_version_code, created_at, part_size, comps)
    problems, _ = fmt.validate(doc)
    if problems:
        raise ValueError("; ".join(problems))
    raw = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    (root / fmt.KIT_JSON).write_bytes(raw)
    sums = {path: sha for path, _, sha in fmt.stored_files(doc)}
    sums[fmt.KIT_JSON] = hashlib.sha256(raw).hexdigest()
    (root / fmt.SUMS).write_bytes(fmt.format_sums(sums))
    return doc


def _component_doc(c: Component, files: list[dict[str, Any]]) -> dict[str, Any]:
    return {"kind": c.kind, "name": c.name, "version": c.version, "licence": c.licence,
            "household_added": c.household_added, "pinned_by": c.pinned_by, "files": files}


def _doc(version: str, commit: str, targets: Sequence[str], includes: Sequence[str],
         android_version_code: int | None, created_at: str | None, part_size: int,
         components: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "kit_format": fmt.KIT_FORMAT,
        "domovoi_version": version,
        "tag": f"v{version}",
        "commit": commit,
        "created_at": created_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "targets": list(targets),
        "includes": list(includes),
        "android_version_code": android_version_code,
        "part_size": part_size,
        "notices": [{"id": n, "text": fmt.NOTICES[n]} for n in fmt.required_notices(components)],
        "components": components,
    }


def reassemble(root: str | Path, entry: dict[str, Any], dest: str | Path) -> None:
    """Join a split file's parts (its kit.json ``entry``) into ``dest``,
    checking each part and the whole. Raises ValueError, and leaves no
    ``dest``, when something doesn't match."""
    root, dest = Path(root), Path(dest)
    tmp = dest.with_name(dest.name + ".joining")
    whole = hashlib.sha256()
    try:
        with open(tmp, "wb") as out:
            for part in entry["parts"]:
                h = hashlib.sha256()
                with open(root / part["path"], "rb") as f:
                    while chunk := f.read(_CHUNK):
                        h.update(chunk)
                        whole.update(chunk)
                        out.write(chunk)
                if h.hexdigest() != part["sha256"]:
                    raise ValueError(f"{part['path']} is damaged. Make the stick again.")
        if tmp.stat().st_size != entry["size"] or whole.hexdigest() != entry["sha256"]:
            raise ValueError(f"{entry['path']}: its parts don't join into the file kit.json names.")
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
