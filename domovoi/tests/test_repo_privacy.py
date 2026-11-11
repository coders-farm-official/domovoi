"""No local machine details in the tracked files (AGENTS.md convention 5).

The repo is public, so it carries nothing of the machines it is built on:
no maintainer's home folder, no path into a maintainer's own workspace or
the private functional test harness kept outside the repo, and no
maintainer's home network. Examples use neutral ones instead: a user
called alex (``C:/Users/alex``, ``/home/alex``, ``%USERPROFILE%``) and the
example network 192.168.1.0/24 (router .1, server .20, PC .30, satellites
.4x). ``192.168.0.0/16`` itself, the RFC 1918 block, is fine as a block.

The patterns are bracket-split, the branding gate's trick, so this file
does not match itself.

The owner's real name stays out of the repo too. The gate holds only
SHA-256 digests of the lowercase name tokens (so the name is not written
here either) and flags any word of a tracked text file whose digest
matches. Allowed: the public CMU dictionary data (a dictionary word) and a
third-party font licence (another person's legitimate copyright line).
Examples use fictional names (alex, sam, priya, jordan).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

BANNED: dict[str, re.Pattern[bytes]] = {
    "a maintainer's workspace folder": re.compile(rb"(?i)claude-ex[p]|domovoi-projec[t]|wt-domovo[i]"),
    "the private test harness folder": re.compile(rb"(?i)functional-testin[g]"),
    "a temporary session folder": re.compile(rb"(?i)temp[\\/]+clau[d]e[\\/]"),
    "the owner's home network (192.168.0/24)": re.compile(rb"192\.168\.[0]\.(?!0/16\b)"),
}

# SHA-256 of the lowercase tokens of the owner's name: first name, surname,
# and the email local part (both run together).
NAME_DIGESTS = frozenset({
    "bed680f702a8712bdbb338fc8d7edf01083a65a3ed9578429327de46d7214257",
    "214f3a838430f20ccd9ed41e1dc8983dfb989301ce22fd7676416ec73e34a6f9",
    "63d9d811a3c02c0c124fe5ad6e74e36928577e013b514c89e966a88e83b6c8a1",
})
# Files that may hold such a token. Third-party text only, plus one TEMPORARY
# exemption: ios/ belongs to the iOS track, which has unmerged branches; it
# scrubs its own files and then this entry is deleted.
NAME_ALLOWED_FILES = {
    "domovoi/phonemizer/data/cmudict/cmudict.dict",  # public dictionary data
    "web/static/fonts/figtree/OFL.txt",              # the font designer's copyright line
}
NAME_ALLOWED_PREFIXES = (
    "ios/",  # TEMPORARY: remove once the iOS session has cleaned its files
)
_WORD = re.compile(rb"[A-Za-z]+")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")


def _name_findings(rel: str, data: bytes, digests: frozenset[str] = NAME_DIGESTS) -> list[str]:
    """Words of the text whose lowercase SHA-256 is one of the digests.

    A word is a run of letters, so ``x-y``, ``x_y`` and ``x's`` are caught;
    a camelCase run is checked whole and word by word.
    """
    found = []
    for m in _WORD.finditer(data):
        word = m.group().decode("ascii")
        parts = {word, *_CAMEL.findall(word)}
        if any(hashlib.sha256(p.lower().encode()).hexdigest() in digests for p in parts):
            line = data.count(b"\n", 0, m.start()) + 1
            found.append(f"{rel}:{line}: the owner's name")
    return found


def _tracked() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
            capture_output=True, check=True, timeout=60,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        pytest.skip(f"not a git checkout, so no tracked-file list: {e}")
    return [p for p in out.decode("utf-8").split("\0") if p]


def _findings(rel: str, data: bytes) -> list[str]:
    found = []
    for what, pattern in BANNED.items():
        for m in pattern.finditer(data):
            line = data.count(b"\n", 0, m.start()) + 1
            found.append(f"{rel}:{line}: {what}")
    return found


def test_the_patterns_catch_what_they_are_for() -> None:
    """Each pattern on a sample it must catch and one it must leave."""
    caught = [
        b"..\\claude-" + b"exp\\x", b"domovoi-" + b"project/",
        b"wt-" + b"domovoi/x", b"functional-" + b"testing/at.py", b"Temp\\" + b"claude\\",
        b"http://192.168." + b"0.117:6370", b"192.168." + b"0.0/24", b'f"192.168.' + b'0.{n}"',
    ]
    left = [
        b"C:/Users/alex/Music", b"/home/alex/Music", b"%USERPROFILE%", b"192.168.0.0/16",
        b"http://192.168.1.20:6370", b"192.168.10.5", b"the functional test harness",
        b"the owner's choice",
    ]
    for sample in caught:
        assert _findings("x", sample), sample
    for sample in left:
        assert not _findings("x", sample), sample


def test_the_name_gate_catches_a_token_however_it_is_written() -> None:
    """The same logic on a fictional name's digest: the real one is not in the repo."""
    digests = frozenset({hashlib.sha256(b"zorbo").hexdigest()})
    for text in (b"Zorbo", b"ZORBO's phone", b"zorbo-desktop:6369", b"/home/zorbo/x", b"the_zorbo_room",
                 b"zorboOffice", b"ZorboOffice", b"x = 'Zorbo Quill'"):
        assert _name_findings("x", text, digests), text
    for text in (b"zorbos", b"zorbonic", b"alex", b"Priya's phone", b"the owner"):
        assert not _name_findings("x", text, digests), text


def test_the_owners_name_is_not_in_tracked_files() -> None:
    found: list[str] = []
    for rel in _tracked():
        if rel in NAME_ALLOWED_FILES or rel.startswith(NAME_ALLOWED_PREFIXES):
            continue
        try:
            data = (REPO_ROOT / rel).read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        found += _name_findings(rel, data)
    assert not found, (
        f"{len(found)} use(s) of the owner's real name in tracked files; say "
        "\"the owner\" in prose, or a fictional name (alex, sam, priya) in examples:\n"
        + "\n".join(found[:50])
    )


def test_no_local_machine_details_in_tracked_files() -> None:
    found: list[str] = []
    for rel in _tracked():
        path = REPO_ROOT / rel
        try:
            data = path.read_bytes()
        except OSError:  # a tracked file deleted in the working tree
            continue
        if b"\0" in data:
            continue  # binary
        found += _findings(rel, data)
    assert not found, (
        f"{len(found)} local machine detail(s) in tracked files; use the neutral "
        "examples (AGENTS.md convention 5):\n" + "\n".join(found[:50])
    )
