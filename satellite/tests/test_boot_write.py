"""Writes to the card's FAT boot partition survive a power cut
(satellite/boot_write.py), and nothing writes there any other way.

2026-10-23: a card pulled after a failed Wi-Fi join held a
``domovoi/device-info.json`` Windows called "corrupted and unreadable".
FAT has no journal and a satellite is usually switched off at the plug.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from satellite import boot_write, setup_report

SATELLITE = Path(__file__).resolve().parents[1]


# ─── the writer ───────────────────────────────────────────────────────────


def test_it_writes_and_reports_it(tmp_path):
    target = tmp_path / "device-info.json"
    assert boot_write.write_json(target, {"status": "x", "name": "Küche"}) is True
    raw = target.read_bytes()
    assert json.loads(raw) == {"status": "x", "name": "Küche"}
    assert "Küche" in raw.decode("utf-8")      # readable as typed
    assert b"\r\n" not in raw
    assert not (tmp_path / "device-info.json.tmp").exists()


def test_the_same_content_is_not_written_again(tmp_path, monkeypatch):
    target = tmp_path / "device-info.json"
    boot_write.write_json(target, {"a": 1})
    opened = []
    real_open = open

    def spy(file, mode="r", *a, **k):
        if "w" in mode or "a" in mode:
            opened.append(file)
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr("builtins.open", spy)
    assert boot_write.write_json(target, {"a": 1}) is False
    assert opened == []


def test_a_crash_between_the_write_and_the_replace_leaves_the_old_file(tmp_path, monkeypatch):
    target = tmp_path / "device-info.json"
    target.write_bytes(b'{"status": "old"}\n')

    class PowerCut(BaseException):
        """Not an OSError: nothing in the writer may catch it, like a real
        power cut."""

    def cut(*a, **k):
        raise PowerCut

    monkeypatch.setattr(boot_write.os, "replace", cut)
    with pytest.raises(PowerCut):
        boot_write.write_json(target, {"status": "new"})
    assert target.read_bytes() == b'{"status": "old"}\n'
    # The half-done temporary is beside it; the next write just overwrites it.
    stray = tmp_path / "device-info.json.tmp"
    assert json.loads(stray.read_bytes()) == {"status": "new"}

    monkeypatch.undo()
    stray.write_bytes(b'{"stat')        # and even a torn one is harmless
    assert boot_write.write_json(target, {"status": "newer"}) is True
    assert json.loads(target.read_bytes()) == {"status": "newer"}
    assert not stray.exists()


def test_a_failed_replace_leaves_the_old_file_and_no_temporary(tmp_path, monkeypatch):
    target = tmp_path / "device-info.json"
    target.write_bytes(b'{"status": "old"}\n')

    def boom(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(boot_write.os, "replace", boom)
    with pytest.raises(OSError):
        boot_write.write_json(target, {"status": "new"})
    assert target.read_bytes() == b'{"status": "old"}\n'
    assert not (tmp_path / "device-info.json.tmp").exists()


def test_the_data_is_synced_before_the_rename_and_the_card_after_it(tmp_path, monkeypatch):
    events: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(boot_write.os, "fsync",
                        lambda fd: (events.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(boot_write.os, "replace",
                        lambda a, b: (events.append("replace"), real_replace(a, b))[1])
    monkeypatch.setattr(boot_write.os, "sync", lambda: events.append("sync"), raising=False)
    boot_write.write_text(tmp_path / "cmdline.txt", "console=tty1 root=PARTUUID=ab-02\n")
    assert events[0] == "fsync"                 # the file's data first
    assert events.index("replace") > 0
    assert events[-1] == "sync"                 # then the rename, onto the card


@pytest.mark.parametrize("raw", [
    b"", b'{"domovoi_setup": 1, "status": "bootstr', b"\x00" * 726, b"\xff\xfe garbage",
    b"[1, 2]", b'"a string"', b"null",
])
def test_a_damaged_file_reads_as_nothing(tmp_path, raw):
    target = tmp_path / "device-info.json"
    target.write_bytes(raw)
    assert boot_write.read_json(target) is None


def test_a_missing_or_unreadable_file_reads_as_nothing(tmp_path):
    assert boot_write.read_json(tmp_path / "nope.json") is None
    assert boot_write.read_json(tmp_path) is None          # a directory


# ─── the card's report (satellite/setup_report.py) ────────────────────────


@pytest.fixture
def card(satellite_paths_in_tmp):
    folder = satellite_paths_in_tmp / "boot" / "domovoi"
    folder.mkdir(parents=True)
    path = folder / "device-info.json"
    path.write_text(json.dumps({"domovoi_setup": 1, "status": "awaiting_provision"}),
                    encoding="utf-8")
    setup_report._last_write.clear()
    return path


@pytest.mark.parametrize("raw", [b'{"domovoi_setup": 1, "status": "bootstr', b"\x00" * 726, b""])
def test_a_damaged_report_is_replaced_rather_than_left_forever(card, raw):
    card.write_bytes(raw)
    assert setup_report.update(top={"status": "wifi_failed", "step": "home-wifi-failed"}) is True
    doc = json.loads(card.read_text(encoding="utf-8"))
    assert doc["domovoi_setup"] == 1 and doc["status"] == "wifi_failed"
    assert "unreadable" in doc["report_replaced"]


def test_a_report_that_says_nothing_new_is_not_written(card, monkeypatch):
    assert setup_report.update(top={"step": "x"}) is True
    writes = []
    monkeypatch.setattr(setup_report.boot_write, "write_json",
                        lambda *a: writes.append(a) or True)
    assert setup_report.update(top={"step": "x"}) is False
    assert writes == []


def test_only_the_clock_moving_is_written_at_most_every_half_hour(card, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(setup_report.time, "monotonic", lambda: clock[0])
    net = {"state": "failed", "last_error": "no radio"}
    assert setup_report.update(network={**net, "attempts": 1, "line": "at 10:00"}) is True
    # A retry loop's next turn: same failure, new count and time.
    clock[0] += 300
    assert setup_report.update(network={**net, "attempts": 2, "line": "at 10:05"}) is False
    assert json.loads(card.read_text(encoding="utf-8"))["setup_network"]["attempts"] == 1
    clock[0] += setup_report.VOLATILE_REWRITE_SEC
    assert setup_report.update(network={**net, "attempts": 9, "line": "at 10:40"}) is True
    assert json.loads(card.read_text(encoding="utf-8"))["setup_network"]["attempts"] == 9
    # Anything a person would read as news goes straight on.
    clock[0] += 1
    assert setup_report.update(network={**net, "last_error": "other"}) is True


# ─── the guard: nothing writes the boot partition but boot_write ──────────

# A function that names the boot partition (or receives it) ...
_BOOT_MARKERS = ("BOOT_DIRS", "boot_dirs", "report_path(", "/boot/firmware", '"/boot"')
# ... may not write a file any way but boot_write's.
_WRITE_ATTRS = {"write_text", "write_bytes", "replace", "rename", "renames", "copy",
                "copy2", "copyfile", "copytree", "move", "truncate", "touch", "dump"}
# Modules that are about the card and nothing else: no raw writes anywhere.
_CARD_ONLY_MODULES = {"setup_report.py"}


def _raw_writes(node: ast.AST) -> list[str]:
    found = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        fn = sub.func
        if isinstance(fn, ast.Attribute) and fn.attr in _WRITE_ATTRS:
            if isinstance(fn.value, ast.Name) and fn.value.id == "boot_write":
                continue                # the one sanctioned way
            # str.replace is not a file operation: os.replace / Path.replace
            # take a path, str.replace two strings. Only flag the call on
            # `os` or with one argument (Path.replace(target)).
            if fn.attr == "replace" and not (
                (isinstance(fn.value, ast.Name) and fn.value.id == "os") or len(sub.args) == 1
            ):
                continue
            found.append(f"line {sub.lineno}: .{fn.attr}(")
        elif isinstance(fn, ast.Name) and fn.id == "open":
            mode = sub.args[1] if len(sub.args) > 1 else next(
                (k.value for k in sub.keywords if k.arg == "mode"), None)
            text = mode.value if isinstance(mode, ast.Constant) else "r"
            if not isinstance(text, str) or set(text) & set("wax+"):
                found.append(f"line {sub.lineno}: open(..., {text!r})")
    return found


def boot_write_violations(source: str, name: str) -> list[str]:
    tree = ast.parse(source)
    if name in _CARD_ONLY_MODULES:
        return [f"{name} {v}" for v in _raw_writes(tree)]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(source, node) or ""
            if any(m in seg for m in _BOOT_MARKERS):
                out += [f"{name}:{node.name} {v}" for v in _raw_writes(node)]
    return out


def test_nothing_in_the_satellite_writes_the_boot_partition_but_boot_write():
    offenders = []
    scripts = [p for p in sorted((SATELLITE / "scripts").iterdir()) if p.is_file() and (
        p.suffix == ".py" or p.read_text(encoding="utf-8", errors="replace")
        .startswith("#!/usr/bin/env python"))]
    assert any(p.name == "domovoi-sync-time" for p in scripts)
    for path in sorted(SATELLITE.glob("*.py")) + scripts:
        if path.name == "boot_write.py":
            continue
        offenders += boot_write_violations(path.read_text(encoding="utf-8"), path.name)
    assert offenders == [], (
        "write to the boot partition (FAT, no journal) through satellite/boot_write.py: "
        + "; ".join(offenders))


def test_the_guard_catches_the_writes_it_replaced():
    """The guard itself works: the in-place writes this change removed."""
    old_revert = '''
def revert(boot_dirs=None):
    for boot in (boot_dirs if boot_dirs is not None else BOOT_DIRS):
        (boot / "cmdline.txt").write_text("x", encoding="utf-8", newline="\\n")
'''
    assert boot_write_violations(old_revert, "provisioning_mode.py")
    old_report = '''
def _update_locked(path, top, network):
    with open(tmp, "wb") as fh:
        fh.write(raw)
    os.replace(tmp, path)
'''
    assert len(boot_write_violations(old_report, "setup_report.py")) == 2
    # ...and leaves alone what is not a file write.
    fine = '''
def ap_credential_paths():
    return [b / "domovoi" / "ap.json" for b in BOOT_DIRS]

def line(x):
    return x.replace("a", "b")
'''
    assert boot_write_violations(fine, "provisioning_mode.py") == []
