"""The offline kit's format: the single source of truth.

A kit is a folder (the root of a FAT32 stick labelled ``DOMOVOIKIT``)::

    START-HERE.html, START-HERE.txt   plain steps for each computer
    kit.json                          what is here (KIT_JSON below)
    SHA256SUMS                        every file and part, and kit.json
    release/    the GitHub release's own files: DomovoiSetup.exe and its
                signed manifest (PC), DomovoiSetup-mac.pkg and its signed
                manifest (Mac), the release's SHA256SUMS
    linux/      install.sh at the release commit
    source/     domovoi.bundle, a git bundle up to the release tag
    apt/        partial apt mirrors (ubuntu/, docker/, nvidia/)
    python/     wheels from the lock, one folder per platform
    images/     docker save of the pinned images and the MPD image
    ollama/     the Ollama tarball; models/ holds manifests/ and blobs/
    speech/     whisper-small.en/, piper/, speaker/
    wake/       wake words, their model cards and notices, VAD
    phone/      the Android APKs and android/release.json
    windows/    the WSL MSI and the Ubuntu WSL image
    mac/        the Ubuntu arm64 cloud image, Ollama for macOS, ggml STT
    rooms/      satellite cache, Windows satellite Setup, room-stick image
    licences/   every licence text and notice

**kit.json** (``kit_format`` 1)::

    {
      "kit_format": 1,
      "domovoi_version": "1.5.0",
      "tag": "v1.5.0",
      "commit": "<40 hex>",
      "created_at": "2026-11-22T10:15:00Z",
      "targets": ["pc-x86_64"],
      "includes": ["ai", "vision", "rooms"],
      "android_version_code": 261120010,
      "part_size": 3900000000,
      "notices": [{"id": "do-not-sell", "text": "..."}],
      "components": [
        {"kind": "ollama-model", "name": "qwen3:8b", "version": "<digest>",
         "licence": "Apache-2.0", "household_added": false,
         "pinned_by": "scripts/linux/provision-manifest.conf",
         "files": [
           {"path": "ollama/models/blobs/sha256-<hex>", "size": 5200000000,
            "sha256": "<hex>",
            "parts": [{"path": "ollama/models/blobs/sha256-<hex>.part01",
                       "size": 3900000000, "sha256": "<hex>"},
                      {"path": "ollama/models/blobs/sha256-<hex>.part02",
                       "size": 1300000000, "sha256": "<hex>"}]}]}
      ]
    }

``kit_format`` changes only when an older reader would get a kit wrong.
New fields don't change it: a reader ignores fields it doesn't know (and
the checker notes them), so a server can still read a newer release's kit
to update from it. A kit with a ``kit_format`` above ``KIT_FORMAT`` is
refused as a whole.

**Split files.** FAT32 holds files up to 4 GiB less one byte. A file
bigger than ``part_size`` (3.9 GB by default, recorded in kit.json) is
stored as parts only, never whole: ``<path>.part01``, ``<path>.part02``,
... (two digits; more when a file has 100 parts or more). Every part but
the last is exactly ``part_size`` bytes; the last holds the rest. Joined
in number order they are the file kit.json names, with its size and
SHA-256. Each part has its own size and SHA-256 too, so a damaged part is
named. ``part_size`` is at least 1 MiB, a file at most 1 TiB, and kit.json
at most 64 MiB, so a reader's work is bounded by the stick, not by what
kit.json claims.

**SHA256SUMS** is ``sha256sum``'s own format (``<hex>  <path>``, sorted by
path, LF): every file on the stick as stored (parts, not joined files) and
kit.json itself. ``cd DOMOVOIKIT && sha256sum -c SHA256SUMS`` works on any
Linux. It catches a bad copy or a dying stick; it is not trust. Trust is
the release's signed manifest (sshsig.py) and the pins in that commit.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

KIT_FORMAT = 1

KIT_JSON = "kit.json"
SUMS = "SHA256SUMS"
VOLUME_LABEL = "DOMOVOIKIT"
GUIDE_FILES = ("START-HERE.html", "START-HERE.txt")

#: FAT32's largest file: 4 GiB less one byte.
FAT32_MAX_FILE = 4 * 1024**3 - 1
#: Files bigger than this are split. 3.9 GB, well under FAT32_MAX_FILE.
PART_SIZE = 3_900_000_000
#: The smallest part_size a kit may record (a reader's work grows with the
#: number of parts).
MIN_PART_SIZE = 1024 * 1024
#: The biggest file a kit may list (far above any model or image).
MAX_FILE_SIZE = 1024**4
#: The biggest kit.json a reader opens.
MAX_KIT_JSON = 64 * 1024 * 1024

#: What each kit target means. PC (Windows and Linux) is ticked by
#: default, Mac is not (owner answer 3, 2026-11-22).
TARGETS = {
    "pc-x86_64": "A Windows or Linux PC",
    "mac-arm64": "A Mac with an Apple chip",
}
DEFAULT_TARGETS = ("pc-x86_64",)

#: The card's "Include" tick boxes.
INCLUDES = {
    "ai": "AI chat and questions",
    "vision": "Pictures in chat",
    "rooms": "Rooms: Raspberry Pis and spare computers",
}

#: Each component kind, and the folder its files live in ("" = the root,
#: for the START-HERE guides only).
KIND_FOLDERS = {
    "guide": "",
    "release": "release/",
    "linux-installer": "linux/",
    "source": "source/",
    "apt": "apt/",
    "python-wheels": "python/",
    "container-image": "images/",
    "ollama-runtime": "ollama/",
    "ollama-model": "ollama/models/",
    "whisper-model": "speech/",
    "piper-voice": "speech/",
    "speaker-model": "speech/",
    "wake-word": "wake/",
    "wake-support": "wake/",
    "phone-app": "phone/",
    "windows-part": "windows/",
    "mac-part": "mac/",
    "rooms": "rooms/",
    "licence": "licences/",
}
#: The kit's own folders: anything in them must be listed in kit.json.
FOLDERS = tuple(sorted({f.rstrip("/").split("/")[0] for f in KIND_FOLDERS.values() if f}))

#: Kinds a household may add itself (owner answer 4: optional, off by
#: default, marked "not checked against a release").
HOUSEHOLD_KINDS = frozenset({"ollama-model", "wake-word"})

#: Licences a kit may carry (docs/LICENSES.md; design section 9). The kit
#: builder copies nothing whose licence is not here.
LICENCES = {
    "MIT": "notice",
    "Apache-2.0": "licence and NOTICE",
    "BSD-2-Clause": "notice",
    "BSD-3-Clause": "notice",
    "ISC": "notice",
    "MPL-2.0": "unmodified, notice",
    "PostgreSQL": "notice",
    "CC0-1.0": "none",
    "LicenseRef-public-domain": "none",
    "CC-BY-4.0": "attribution",
    "CC-BY-NC-SA-4.0": "credit, same licence, never sold",
    "LicenseRef-Llama-3.2-Community": "licence, acceptable use policy, Built with Llama",
    "LicenseRef-distro-archive": "unmodified Ubuntu or Debian packages; their source archives are public",
    "LicenseRef-mixed-see-sbom": "a set of files under the licences the release's SBOM lists",
}
#: Licences known to forbid passing the files on, with the reason.
NEVER_ON_A_KIT = {
    "LicenseRef-Qwen-Research": "research or evaluation only",
    "LicenseRef-Gemma": "Google's terms bind every recipient",
    "AGPL-3.0-only": "AGPL",
    "AGPL-3.0-or-later": "AGPL",
    "LicenseRef-NVIDIA-EULA": "NVIDIA's EULA",
}

#: Notices a kit carries, by id. write_kit fills them in; the checker
#: requires the ones the kit's licences call for.
NOTICES = {
    "do-not-sell": "Don't sell this stick: some files on it may only be shared for free.",
    "wake-word-nc-sa": (
        "The Domovoi wake word (domovoi_v1) and openWakeWord's pre-trained wake words and "
        "melspectrogram model are licensed CC BY-NC-SA 4.0: give credit, share them under the "
        "same licence, and never sell them. Their notices are in wake/ and licences/."
    ),
    "llama": (
        "Built with Llama. Llama 3.2 is licensed under the Llama 3.2 Community License, "
        "Copyright (c) Meta Platforms, Inc. All Rights Reserved. The licence and the acceptable "
        "use policy are in licences/."
    ),
    "household-added": (
        "Some files were added by the house that made this kit. They are not checked against a "
        "Domovoi release."
    ),
}
#: Notices every kit carries.
ALWAYS_NOTICES = ("do-not-sell",)
#: Notices a licence brings with it.
LICENCE_NOTICES = {
    "CC-BY-NC-SA-4.0": ("wake-word-nc-sa", "do-not-sell"),
    "LicenseRef-Llama-3.2-Community": ("llama",),
}

#: The release's signed manifest for each target (scripts/release/
#: finish_release.py writes them; namespaces and kinds are its own).
RELEASE_MANIFESTS = {
    "pc-x86_64": {
        "manifest": "release/DomovoiSetup.manifest.json",
        "namespace": "domovoi-windows-setup-manifest",
        "kind": "domovoi-windows-setup",
        "file": "DomovoiSetup.exe",
    },
    "mac-arm64": {
        "manifest": "release/DomovoiSetup-mac.manifest.json",
        "namespace": "domovoi-mac-setup-manifest",
        "kind": "domovoi-mac-setup",
        "file": "DomovoiSetup-mac.pkg",
    },
}

#: Files an operating system leaves on a stick by itself. Never reported.
OS_LITTER_DIRS = frozenset({
    "System Volume Information", "$RECYCLE.BIN", ".Trashes", ".Spotlight-V100",
    ".fseventsd", ".TemporaryItems", ".DocumentRevisions-V100", "lost+found",
})
OS_LITTER_FILES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini", ".metadata_never_index"})

VERSION_RE = re.compile(r"^[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}(-[0-9A-Za-z.]{1,40})?\Z")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}\Z")
SHA256_RE = re.compile(r"^[0-9a-f]{64}\Z")
CREATED_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
NOTICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}\Z")
# FAT32 refuses these in a name, and a trailing dot or space.
_BAD_NAME_CHARS = re.compile(r'[<>:"|?*\\\x00-\x1f]')

KNOWN_KEYS = frozenset({
    "kit_format", "domovoi_version", "tag", "commit", "created_at", "targets", "includes",
    "android_version_code", "part_size", "notices", "components",
})
COMPONENT_KEYS = frozenset({"kind", "name", "version", "licence", "household_added", "pinned_by", "files"})
FILE_KEYS = frozenset({"path", "size", "sha256", "parts"})
PART_KEYS = frozenset({"path", "size", "sha256"})


class KitProblem(Exception):
    """kit.json can't be read or doesn't follow the format. ``problems``
    holds every reason, in plain words."""

    def __init__(self, problems: list[str], *, notes: list[str] | None = None, newer: bool = False):
        super().__init__("; ".join(problems))
        self.problems = problems
        self.notes = notes or []
        self.newer = newer


def is_os_litter(name: str) -> bool:
    return name in OS_LITTER_FILES or name.startswith("._")


def path_problem(path: Any) -> str | None:
    """Why ``path`` can't be a kit path, or None. Kit paths are relative,
    use ``/``, and every name in them is one FAT32 holds."""
    if not isinstance(path, str) or not path:
        return "a path must be text"
    if path.startswith("/") or "\\" in path or re.match(r"^[A-Za-z]:", path):
        return f"{path!r} must be a relative path with / between folders"
    names = path.split("/")
    for name in names:
        if name in ("", ".", ".."):
            return f"{path!r} has an empty, '.' or '..' part"
        if _BAD_NAME_CHARS.search(name) or name[-1] in ". " or len(name.encode("utf-8")) > 255:
            return f"{path!r} has a name a FAT32 stick can't hold"
    return None


def part_paths(path: str, count: int) -> list[str]:
    """The part names for ``path`` split into ``count`` parts."""
    width = max(2, len(str(count)))
    return [f"{path}.part{i:0{width}d}" for i in range(1, count + 1)]


def part_count(size: int, part_size: int) -> int:
    """How many parts a file of ``size`` bytes splits into: 0 when it
    isn't split. Arithmetic only: nothing the size of the count is built."""
    if size <= part_size:
        return 0
    return -(-size // part_size)


def part_size_at(size: int, part_size: int, index: int) -> int:
    """The size of part ``index`` (from 0) of a split file."""
    return min(part_size, size - index * part_size)


def part_sizes(size: int, part_size: int) -> list[int]:
    """How a file of ``size`` bytes splits: [] when it isn't split. For
    sizes the writer has in hand; a reader checking kit.json uses
    :func:`part_count`."""
    if size <= part_size:
        return []
    full, rest = divmod(size, part_size)
    return [part_size] * full + ([rest] if rest else [])


def required_notices(components: list[dict[str, Any]]) -> list[str]:
    """The notice ids a kit with these components must carry, in order."""
    need: list[str] = list(ALWAYS_NOTICES)
    for c in components:
        for n in LICENCE_NOTICES.get(c.get("licence"), ()):
            if n not in need:
                need.append(n)
        if c.get("household_added") is True and "household-added" not in need:
            need.append("household-added")
    return need


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(doc: Any) -> tuple[list[str], list[str]]:
    """Every way ``doc`` (kit.json, parsed) breaks the format, and notes
    (fields this code doesn't know). ([], notes) means it is well-formed.
    A kit_format newer than this code's raises KitProblem(newer=True)."""
    problems: list[str] = []
    notes: list[str] = []
    if not isinstance(doc, dict):
        return ["kit.json must hold one JSON object"], notes
    fmt = doc.get("kit_format")
    if not _is_int(fmt) or fmt < 1:
        problems.append("kit.json has no kit_format (a whole number)")
    elif fmt > KIT_FORMAT:
        raise KitProblem(
            [f"This kit was made by a newer Domovoi (kit format {fmt}); this Domovoi reads kit "
             f"format {KIT_FORMAT}. Update this Domovoi first, or check the kit with the newer "
             "one's checker."], newer=True)
    for key in sorted(set(doc) - KNOWN_KEYS):
        notes.append(f"kit.json has a field this Domovoi doesn't know ({key!r}); it was ignored")

    version = doc.get("domovoi_version")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        problems.append("domovoi_version must be a release version like 1.5.0")
    elif doc.get("tag") != f"v{version}":
        problems.append(f"tag must be v{version}, the release tag of domovoi_version")
    if not isinstance(doc.get("commit"), str) or not COMMIT_RE.match(doc["commit"]):
        problems.append("commit must be the release commit (40 lowercase hex digits)")
    if not isinstance(doc.get("created_at"), str) or not CREATED_RE.match(doc["created_at"]):
        problems.append("created_at must be a UTC time like 2026-11-22T10:15:00Z")

    targets = doc.get("targets")
    if not isinstance(targets, list) or not targets:
        problems.append("targets must list at least one of: " + ", ".join(TARGETS))
    else:
        for t in targets:
            if t not in TARGETS:
                problems.append(f"targets: {t!r} is not one of: " + ", ".join(TARGETS))
        if len(set(map(str, targets))) != len(targets):
            problems.append("targets lists something twice")
    includes = doc.get("includes")
    if not isinstance(includes, list):
        problems.append("includes must be a list (it may be empty)")
    else:
        for i in includes:
            if i not in INCLUDES:
                problems.append(f"includes: {i!r} is not one of: " + ", ".join(INCLUDES))
        if len(set(map(str, includes))) != len(includes):
            problems.append("includes lists something twice")
    avc = doc.get("android_version_code")
    if avc is not None and (not _is_int(avc) or avc < 1):
        problems.append("android_version_code must be a positive whole number, or null")
    part_size = doc.get("part_size")
    if not _is_int(part_size) or not MIN_PART_SIZE <= part_size <= FAT32_MAX_FILE:
        problems.append(f"part_size must be a whole number of bytes from {MIN_PART_SIZE} to {FAT32_MAX_FILE}")
        part_size = None

    components = doc.get("components")
    if not isinstance(components, list) or not components:
        problems.append("components must list what is on the kit")
        components = []
    seen: dict[str, tuple[Any, Any]] = {}
    folded: dict[str, str] = {}

    def claim(path: str, size: Any, sha: Any, where: str) -> None:
        if path in (KIT_JSON, SUMS):
            problems.append(f"{where}: {path} is the kit's own index and can't be listed")
            return
        if path in seen:
            if seen[path] != (size, sha):
                problems.append(f"{where}: {path} is listed twice with different contents")
            return
        low = path.casefold()
        if low in folded and folded[low] != path:
            problems.append(f"{where}: {path} and {folded[low]} differ only in capitals, "
                            "and a FAT32 stick can't hold both")
            return
        seen[path] = (size, sha)
        folded[low] = path

    for ci, comp in enumerate(components):
        where = f"component {ci + 1}"
        if not isinstance(comp, dict):
            problems.append(f"{where} must be a JSON object")
            continue
        name = comp.get("name")
        if isinstance(name, str) and name:
            where = f"component {ci + 1} ({name})"
        else:
            problems.append(f"{where}: name must be text")
        for key in sorted(set(comp) - COMPONENT_KEYS):
            notes.append(f"{where} has a field this Domovoi doesn't know ({key!r}); it was ignored")
        kind = comp.get("kind")
        if kind not in KIND_FOLDERS:
            problems.append(f"{where}: kind {kind!r} is not one of: " + ", ".join(KIND_FOLDERS))
            kind = None
        if not isinstance(comp.get("version"), str) or not comp["version"]:
            problems.append(f"{where}: version must be text")
        licence = comp.get("licence")
        if licence in NEVER_ON_A_KIT:
            problems.append(f"{where}: its licence ({licence}) doesn't allow passing it on "
                            f"({NEVER_ON_A_KIT[licence]}); it must not be on a kit")
        elif licence not in LICENCES:
            problems.append(f"{where}: licence {licence!r} is not on the list of licences a kit "
                            "may carry")
        household = comp.get("household_added")
        if not isinstance(household, bool):
            problems.append(f"{where}: household_added must be true or false")
        pinned = comp.get("pinned_by")
        if pinned is not None and (not isinstance(pinned, str) or not pinned):
            problems.append(f"{where}: pinned_by must be text, or null")
        if household is True:
            if kind is not None and kind not in HOUSEHOLD_KINDS:
                problems.append(f"{where}: a household can add only "
                                + " and ".join(sorted(HOUSEHOLD_KINDS)) + f", not {kind}")
            if pinned is not None:
                problems.append(f"{where}: a household-added file is not checked against a "
                                "release, so it can't have pinned_by")
        files = comp.get("files")
        if not isinstance(files, list) or not files:
            problems.append(f"{where}: files must list at least one file")
            continue
        for fi, entry in enumerate(files):
            fwhere = f"{where}, file {fi + 1}"
            if not isinstance(entry, dict):
                problems.append(f"{fwhere} must be a JSON object")
                continue
            for key in sorted(set(entry) - FILE_KEYS):
                notes.append(f"{fwhere} has a field this Domovoi doesn't know ({key!r}); it was ignored")
            path = entry.get("path")
            bad = path_problem(path)
            if bad:
                problems.append(f"{fwhere}: {bad}")
                continue
            fwhere = f"{where}, {path}"
            if kind is not None:
                folder = KIND_FOLDERS[kind]
                if folder == "":
                    if path not in GUIDE_FILES:
                        problems.append(f"{fwhere}: a guide is one of " + ", ".join(GUIDE_FILES))
                elif not path.startswith(folder):
                    problems.append(f"{fwhere}: a {kind} belongs in {folder}")
            size, sha = entry.get("size"), entry.get("sha256")
            ok = True
            if not _is_int(size) or not 0 <= size <= MAX_FILE_SIZE:
                problems.append(f"{fwhere}: size must be a whole number of bytes, at most {MAX_FILE_SIZE}")
                ok = False
            if not isinstance(sha, str) or not SHA256_RE.match(sha):
                problems.append(f"{fwhere}: sha256 must be 64 lowercase hex digits")
                ok = False
            parts = entry.get("parts")
            if not ok or part_size is None:
                continue
            count = part_count(size, part_size)
            if parts is None:
                if count:
                    problems.append(f"{fwhere}: it is bigger than part_size ({part_size} bytes), "
                                    "so it must be stored in parts")
                    continue
                claim(path, size, sha, fwhere)
                continue
            if not isinstance(parts, list) or not count:
                problems.append(f"{fwhere}: only a file bigger than part_size ({part_size} bytes) "
                                "is stored in parts")
                continue
            if len(parts) != count:
                problems.append(f"{fwhere}: it splits into {count} parts, kit.json lists "
                                f"{len(parts)}")
                continue
            # The logical file is never stored whole: claim its name so no
            # listed file can take it.
            claim(path, size, sha, fwhere)
            names = part_paths(path, count)
            want = [part_size_at(size, part_size, i) for i in range(count)]
            for pi, (part, pname, psize) in enumerate(zip(parts, names, want)):
                pwhere = f"{fwhere}, part {pi + 1}"
                if not isinstance(part, dict):
                    problems.append(f"{pwhere} must be a JSON object")
                    continue
                if part.get("path") != pname:
                    problems.append(f"{pwhere}: its name must be {pname} (parts are listed in "
                                    f"order), not {part.get('path')!r}")
                if part.get("size") != psize:
                    problems.append(f"{pwhere}: its size must be {psize} bytes, not "
                                    f"{part.get('size')!r}")
                psha = part.get("sha256")
                if not isinstance(psha, str) or not SHA256_RE.match(psha):
                    problems.append(f"{pwhere}: sha256 must be 64 lowercase hex digits")
                    continue
                if part.get("path") == pname:
                    claim(pname, psize, psha, pwhere)

    notices = doc.get("notices")
    have: set[str] = set()
    if not isinstance(notices, list):
        problems.append("notices must be a list")
    else:
        for n in notices:
            if (not isinstance(n, dict) or not isinstance(n.get("id"), str)
                    or not NOTICE_ID_RE.match(n["id"]) or not isinstance(n.get("text"), str)
                    or not n["text"].strip()):
                problems.append("each notice must have an id and its text")
                continue
            have.add(n["id"])
    if isinstance(components, list):
        for need in required_notices([c for c in components if isinstance(c, dict)]):
            if need not in have:
                problems.append(f"notices: the kit's files call for the {need!r} notice, and it "
                                "is missing")
    return problems, notes


def read_kit(root: str | Path) -> tuple[dict[str, Any], list[str]]:
    """kit.json from the kit at ``root``, checked against the format, and
    the notes. Raises KitProblem with every reason it can't be used."""
    path = Path(root) / KIT_JSON
    try:
        if path.stat().st_size > MAX_KIT_JSON:
            raise KitProblem([f"{KIT_JSON} is bigger than any kit's ({MAX_KIT_JSON} bytes). The stick may "
                              "be damaged: make it again."])
        raw = path.read_bytes()
    except FileNotFoundError:
        raise KitProblem([f"There is no {KIT_JSON} here, so this is not an offline kit (or the "
                          "stick is damaged)."]) from None
    except OSError as e:
        raise KitProblem([f"{KIT_JSON} can't be read ({e.strerror or e})."]) from None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise KitProblem([f"{KIT_JSON} is not valid JSON ({e}). The stick may be damaged: make "
                          "it again."]) from None
    problems, notes = validate(doc)
    if problems:
        raise KitProblem(problems, notes=notes)
    return doc, notes


def stored_files(doc: dict[str, Any]) -> Iterator[tuple[str, int, str]]:
    """(path, size, sha256) of every file as stored on the stick: whole
    files and parts, never a split file's joined name. Each path once."""
    seen: set[str] = set()
    for comp in doc["components"]:
        for entry in comp["files"]:
            for item in entry.get("parts") or [entry]:
                if item["path"] not in seen:
                    seen.add(item["path"])
                    yield item["path"], item["size"], item["sha256"]


def split_files(doc: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Each split file's kit.json entry, once."""
    seen: set[str] = set()
    for comp in doc["components"]:
        for entry in comp["files"]:
            if entry.get("parts") and entry["path"] not in seen:
                seen.add(entry["path"])
                yield entry


def format_sums(entries: dict[str, str]) -> bytes:
    """SHA256SUMS text: ``<hex>  <path>``, sorted by path, LF."""
    return "".join(f"{entries[p]}  {p}\n" for p in sorted(entries)).encode("utf-8")


_SUMS_LINE = re.compile(r"^([0-9a-f]{64}) [ *](.+)\Z")


def parse_sums(raw: bytes) -> tuple[dict[str, str], list[str]]:
    """SHA256SUMS parsed: {path: sha256}, and what is wrong with it."""
    problems: list[str] = []
    out: dict[str, str] = {}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return out, [f"{SUMS} is not text"]
    for n, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        m = _SUMS_LINE.match(line.rstrip("\r"))
        if not m:
            problems.append(f"{SUMS} line {n} is not '<sha256>  <path>'")
            continue
        sha, path = m.groups()
        if path in out:
            problems.append(f"{SUMS} lists {path} twice")
        out[path] = sha
    return out, problems
