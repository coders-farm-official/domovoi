"""No local machine details in the tracked files (AGENTS.md convention 5).

The repo is public, so it carries nothing of the machines it is built on:
no maintainer's home folder, no path into a maintainer's own workspace or
the private functional test harness kept outside the repo, and no
maintainer's home network. Examples use neutral ones instead: a user
called alex (``C:/Users/alex``, ``/home/alex``, ``%USERPROFILE%``) and the
example network 192.168.1.0/24 (router .1, server .20, PC .30, satellites
.4x). ``192.168.0.0/16`` itself, the RFC 1918 block, is fine as a block.

The patterns are bracket-split, the branding gate's trick, so this file
does not match itself. Nothing is allowlisted.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

BANNED: dict[str, re.Pattern[bytes]] = {
    "the owner's home folder": re.compile(rb"(?i)(?:users|home|media)[\\/]+kam[r]on\b"),
    "a maintainer's workspace folder": re.compile(rb"(?i)claude-ex[p]|domovoi-projec[t]|wt-domovo[i]"),
    "the private test harness folder": re.compile(rb"(?i)functional-testin[g]"),
    "a temporary session folder": re.compile(rb"(?i)temp[\\/]+clau[d]e[\\/]"),
    "the owner's home network (192.168.0/24)": re.compile(rb"192\.168\.[0]\.(?!0/16\b)"),
}


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
        b"C:/Users/" + b"Kamron/Music", b"C:\\\\Users\\\\" + b"kamron", b"/home/" + b"kamron/x",
        b"/media/" + b"kamron/sdb1", b"..\\claude-" + b"exp\\x", b"domovoi-" + b"project/",
        b"wt-" + b"domovoi/x", b"functional-" + b"testing/at.py", b"Temp\\" + b"claude\\",
        b"http://192.168." + b"0.117:6370", b"192.168." + b"0.0/24", b'f"192.168.' + b'0.{n}"',
    ]
    left = [
        b"C:/Users/alex/Music", b"/home/alex/Music", b"%USERPROFILE%", b"192.168.0.0/16",
        b"http://192.168.1.20:6370", b"192.168.10.5", b"the functional test harness",
        b"Kamron's choice", b"/home/kamronx",
    ]
    for sample in caught:
        assert _findings("x", sample), sample
    for sample in left:
        assert not _findings("x", sample), sample


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
