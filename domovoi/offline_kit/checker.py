"""Check an offline kit: ``python -m domovoi.offline_kit verify DIR``.

What it checks, reporting every problem rather than stopping at the first:

1. kit.json reads and follows the format (format.validate). A kit from a
   newer kit format is refused as a whole.
2. Every file and every part kit.json lists is there, with the right size
   and SHA-256.
3. Each split file's parts, joined in order, give the file kit.json names
   (its SHA-256 and size).
4. Nothing else is on the stick (what operating systems leave by
   themselves, like ``System Volume Information`` or ``.DS_Store``, is
   ignored).
5. SHA256SUMS agrees with kit.json and with kit.json's own bytes.
6. The release's signed manifest for each target (``release/``) is signed
   by Domovoi's release key and names this kit's version, tag and commit,
   and the installer beside it is the one it names.

Steps 1-5 catch a bad copy or a dying stick. Step 6 ties the kit to a
Domovoi release, with the key this program holds. Run from the stick
itself, a tampered stick could carry a tampered checker too: the checker
the installed server or a downloaded Domovoi runs is the one to trust
(docs/OFFLINE_KIT.md, "What the checks prove").
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import format as fmt
from .sshsig import RELEASE_KEY, parse_public_key, sshsig_problem

_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Finding:
    level: str  # "error" or "note"
    message: str


@dataclass
class Report:
    root: Path
    findings: list[Finding] = field(default_factory=list)
    files_checked: int = 0
    bytes_checked: int = 0
    doc: dict[str, Any] | None = None

    @property
    def errors(self) -> list[str]:
        return [f.message for f in self.findings if f.level == "error"]

    @property
    def notes(self) -> list[str]:
        return [f.message for f in self.findings if f.level == "note"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, message: str) -> None:
        self.findings.append(Finding("error", message))

    def note(self, message: str) -> None:
        self.findings.append(Finding("note", message))


def _hash_file(path: Path, *also: Any) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
            for other in also:
                other.update(chunk)
    return h.hexdigest()


def verify(root: str | Path, *, release_key: str | None = RELEASE_KEY) -> Report:
    """Check the kit at ``root``. ``release_key`` is the ssh-ed25519 public
    key line the release manifests must be signed with; None skips that
    check (and notes it)."""
    report = Report(Path(root))
    root = report.root
    if not root.is_dir():
        report.error(f"{root} is not a folder.")
        return report
    try:
        doc, notes = fmt.read_kit(root)
    except fmt.KitProblem as e:
        for n in e.notes:
            report.note(n)
        for p in e.problems:
            report.error(p)
        if not e.newer and (root / fmt.KIT_JSON).is_file():
            report.error("The files weren't checked, because kit.json is not right.")
        return report
    report.doc = doc
    for n in notes:
        report.note(n)

    # 2 + 3: every stored file and part, hashed once; split files joined.
    actual: dict[str, str] = {}
    split_parts = {p["path"] for e in fmt.split_files(doc) for p in e["parts"]}
    for path, size, sha in fmt.stored_files(doc):
        if path in split_parts:
            continue  # hashed with its split file below
        _check_stored(report, root, path, size, sha, actual)
    for entry in fmt.split_files(doc):
        # Each part is checked on its own and fed, in order, into the
        # joined hash: one read per part.
        joined = hashlib.sha256()
        complete = True
        for part in entry["parts"]:
            if not _check_stored(report, root, part["path"], part["size"], part["sha256"], actual,
                                 joined):
                complete = False
        if not complete:
            report.error(f"{entry['path']} can't be put back together from its parts.")
            continue
        joined_size = sum((root / part["path"]).stat().st_size for part in entry["parts"])
        if joined_size != entry["size"] or joined.hexdigest() != entry["sha256"]:
            report.error(f"{entry['path']}: its parts, joined in order, are not the file "
                         "kit.json names. Make the stick again.")
    if (root / fmt.KIT_JSON).is_file():
        actual[fmt.KIT_JSON] = _hash_file(root / fmt.KIT_JSON)

    # 4: nothing unlisted.
    listed = {p for p, _, _ in fmt.stored_files(doc)} | {fmt.KIT_JSON, fmt.SUMS}
    for extra in _unlisted(root, listed, {e["path"] for e in fmt.split_files(doc)}):
        report.error(extra)

    # 5: SHA256SUMS.
    _check_sums(report, root, doc, actual)

    # 6: the release's signed manifests.
    if release_key is None:
        report.note("The release signature was not checked (asked not to).")
    else:
        _check_release(report, root, doc, release_key)
    return report


def _check_stored(report: Report, root: Path, path: str, size: int, sha: str,
                  actual: dict[str, str], *also: Any) -> bool:
    """One stored file: there, the right size, the right SHA-256. True when
    it was hashed (into ``also`` too)."""
    p = root / path
    if not p.is_file():
        report.error(f"{path} is missing.")
        return False
    got_size = p.stat().st_size
    if got_size != size:
        report.error(f"{path} is the wrong size: {got_size} bytes, kit.json says {size}. "
                     "Make the stick again.")
        if not also:
            return False
    try:
        actual[path] = _hash_file(p, *also)
    except OSError as e:
        report.error(f"{path} can't be read ({e.strerror or e}). The stick may be failing.")
        return False
    report.files_checked += 1
    report.bytes_checked += got_size
    if got_size == size and actual[path] != sha:
        report.error(f"{path} is damaged: its contents don't match kit.json. Make the stick again.")
    return True


def _unlisted(root: Path, listed: set[str], joined_names: set[str]) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        if rel_dir == ".":
            dirnames[:] = [d for d in dirnames if d not in fmt.OS_LITTER_DIRS]
        dirnames.sort()
        for name in sorted(filenames):
            if fmt.is_os_litter(name):
                continue
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            if rel in listed:
                continue
            if rel in joined_names:
                out.append(f"{rel} is here whole, but kit.json says it is stored in parts only.")
            else:
                out.append(f"{rel} is not part of the kit (kit.json doesn't list it).")
    return out


def _check_sums(report: Report, root: Path, doc: dict[str, Any], actual: dict[str, str]) -> None:
    try:
        raw = (root / fmt.SUMS).read_bytes()
    except FileNotFoundError:
        report.error(f"{fmt.SUMS} is missing.")
        return
    except OSError as e:
        report.error(f"{fmt.SUMS} can't be read ({e.strerror or e}).")
        return
    sums, problems = fmt.parse_sums(raw)
    for p in problems:
        report.error(p)
    want = {path: sha for path, _, sha in fmt.stored_files(doc)}
    for path, sha in want.items():
        if path not in sums:
            report.error(f"{fmt.SUMS} doesn't list {path}.")
        elif sums[path] != sha:
            report.error(f"{fmt.SUMS} and kit.json disagree about {path}.")
    if fmt.KIT_JSON not in sums:
        report.error(f"{fmt.SUMS} doesn't list {fmt.KIT_JSON}.")
    elif fmt.KIT_JSON in actual and sums[fmt.KIT_JSON] != actual[fmt.KIT_JSON]:
        report.error(f"{fmt.KIT_JSON} is not the one {fmt.SUMS} was made with (it was changed "
                     "or damaged).")
    for path in sorted(set(sums) - set(want) - {fmt.KIT_JSON}):
        report.error(f"{fmt.SUMS} lists {path}, which kit.json doesn't.")


def _check_release(report: Report, root: Path, doc: dict[str, Any], release_key: str) -> None:
    key = parse_public_key(release_key)
    if key is None:
        report.error("The release key given is not an ssh-ed25519 public key.")
        return
    entries = {e["path"]: e for c in doc["components"] for e in c["files"]}
    for target in doc["targets"]:
        spec = fmt.RELEASE_MANIFESTS[target]
        mpath = spec["manifest"]
        label = fmt.TARGETS[target]
        try:
            manifest = (root / mpath).read_bytes()
            sig = (root / f"{mpath}.sig").read_bytes()
        except OSError:
            report.error(f"{mpath} or its .sig is missing, so nothing ties the kit's "
                         f"{label} installer to a Domovoi release.")
            continue
        problem = sshsig_problem(sig, manifest, key, spec["namespace"])
        if problem:
            report.error(f"{mpath} can't be trusted: {problem}.")
            continue
        try:
            m = json.loads(manifest.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            report.error(f"{mpath} is signed but isn't JSON.")
            continue
        if not isinstance(m, dict):
            report.error(f"{mpath} is signed but isn't a manifest.")
            continue
        if m.get("kind") != spec["kind"] or m.get("schema") != 1 or m.get("file") != spec["file"]:
            report.error(f"{mpath} is signed but is not the {label} manifest this kit expects.")
            continue
        if (m.get("tag"), m.get("version"), m.get("commit")) != (doc["tag"], doc["domovoi_version"],
                                                                 doc["commit"]):
            report.error(f"{mpath} is for Domovoi {m.get('version')} ({m.get('tag')}, commit "
                         f"{str(m.get('commit'))[:12]}), but kit.json says {doc['domovoi_version']} "
                         f"({doc['tag']}, commit {doc['commit'][:12]}).")
            continue
        installer = f"release/{spec['file']}"
        entry = entries.get(installer)
        if entry is None:
            report.error(f"kit.json doesn't list {installer}, the {label} installer.")
        elif (entry["size"], entry["sha256"]) != (m.get("size"), m.get("sha256")):
            report.error(f"{installer} is not the installer the signed release manifest names.")
