"""Where a file channel may write (satellite/sound_sync.py ``_safe_rel`` and
``safe_dest``), on every board.

Security review of wf/sat-windows (2026-10-13), F1: the path check only knew
POSIX, so a signed sound or wake-model list naming ``C:/Users/<you>/…/
Startup/x.cmd`` (or the satellite's own client.py) was written there on
Windows, on connect, without "ask me". Now a path is held to one rule on
every board (no drive, colon, backslash, UNC or device name, nothing
absolute, no ``..``), the result is resolved and kept inside the cache (a
link or junction can't lead out), and each channel writes only its own
file types. Scratch folders only.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from satellite import code_sync, sound_sync, wake_model_sync

WINDOWS = sys.platform == "win32"
FP = "SHA256:" + "A" * 43


@pytest.mark.parametrize("rel", [
    "greetings/greet_a.mp3", "network_issues.mp3", "setup/ready.wav", "hey_domo.onnx",
    "hey_domo.onnx.json", "satellite/client.py", "voices/amy/greetings/greet_funny_1a2b.mp3",
    "console.mp3", "a b/c-d_e (1).mp3",
])
def test_plain_relative_paths_are_taken(rel):
    assert sound_sync._safe_rel(rel)


@pytest.mark.parametrize("rel", [
    "", "/abs.mp3", "//server/share/x.mp3", "C:/Users/kid/AppData/Roaming/Microsoft/Windows/Start Menu/"
    "Programs/Startup/evil.cmd", "c:evil.mp3", "C:", "x.mp3:stream", "a\\b.mp3", "\\\\server\\share\\x.mp3",
    "\\\\?\\C:\\x.mp3", "../x.mp3", "a/../../x.mp3", "./x.mp3", "a/./x.mp3", "a//x.mp3", "x.mp3.",
    "x.mp3 ", " x.mp3", "CON.mp3", "greetings/nul", "com1", "LPT9.wav", "x\x00.mp3", "x\n.mp3",
    "a*b.mp3", "a?.mp3", 'a"b.mp3', "a<b.mp3", "a|b.mp3", "x" * 401, None, 7,
])
def test_anything_else_is_refused_on_every_board(rel):
    assert not sound_sync._safe_rel(rel)


def test_each_channel_writes_only_its_own_file_types(tmp_path):
    assert sound_sync.safe_dest(tmp_path, "greetings/a.mp3", sound_sync.SOUND_TYPES) == \
        (tmp_path / "greetings" / "a.mp3").resolve()
    assert sound_sync.safe_dest(tmp_path, "A.MP3", sound_sync.SOUND_TYPES) is not None
    for rel in ("evil.cmd", "evil.py", "evil.onnx", "x.mp3.lnk"):
        assert sound_sync.safe_dest(tmp_path, rel, sound_sync.SOUND_TYPES) is None, rel
    for rel in ("hey.onnx", "hey.onnx.json"):
        assert sound_sync.safe_dest(tmp_path, rel, sound_sync.WAKE_MODEL_TYPES) is not None, rel
    for rel in ("hey.json", "hey.mp3", "hey.onnx.py", "hey.dll"):
        assert sound_sync.safe_dest(tmp_path, rel, sound_sync.WAKE_MODEL_TYPES) is None, rel


def _link_out(link: Path, target: Path) -> None:
    """A directory link inside the cache that leads out of it: a junction
    on Windows (no rights needed), a symlink elsewhere."""
    target.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


def test_a_link_inside_the_cache_does_not_lead_out_of_it(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _link_out(cache / "greetings", tmp_path / "outside")
    assert sound_sync._safe_rel("greetings/x.mp3")
    assert sound_sync.safe_dest(cache, "greetings/x.mp3", sound_sync.SOUND_TYPES) is None


class _Resp:
    def __init__(self, body: bytes) -> None:
        self.content = body
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass


def _sync(channel: str, cache: Path, manifest: dict, body: bytes, monkeypatch) -> int:
    mod = sound_sync if channel == "sounds" else wake_model_sync
    monkeypatch.setattr(mod, "fetch_manifest", lambda *a, **k: dict(manifest))
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _Resp(body))
    if channel == "sounds":
        return sound_sync.sync("http://192.168.1.2:6370", cache, None, expected_fingerprint=FP)
    return wake_model_sync.sync("http://192.168.1.2:6370", cache, expected_fingerprint=FP)


@pytest.mark.parametrize("channel", ["sounds", "wake_models"])
def test_f1_a_signed_list_naming_a_path_outside_the_cache_writes_nothing(tmp_path, monkeypatch, channel):
    cache = tmp_path / "Satellite" / ".domovoi" / channel
    outside = tmp_path / "Startup" / "evil.cmd"
    body = b"@echo off\r\nrem written by a file list\r\n"
    sha = hashlib.sha256(body).hexdigest()
    # The review's case on Windows (a drive path written with slashes), and
    # the same path's POSIX spelling, a UNC path and a parent walk.
    manifest = {outside.as_posix(): sha, "//server/share/evil.cmd": sha, "../../Startup/evil.cmd": sha,
                "x.mp3:evil.cmd": sha, "evil.cmd": sha}
    assert _sync(channel, cache, manifest, body, monkeypatch) == 0
    assert not outside.exists()
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written == [] or all(p.name == sound_sync.RECORD_NAME for p in written), written


@pytest.mark.parametrize("channel", ["sounds", "wake_models"])
def test_f1_a_link_in_the_cache_is_not_a_way_out(tmp_path, monkeypatch, channel):
    cache = tmp_path / "cache"
    cache.mkdir()
    _link_out(cache / "sub", tmp_path / "outside")
    name = "sub/x.mp3" if channel == "sounds" else "sub/x.onnx"
    body = b"model or clip"
    assert _sync(channel, cache, {name: hashlib.sha256(body).hexdigest()}, body, monkeypatch) == 0
    assert list((tmp_path / "outside").iterdir()) == []


@pytest.mark.parametrize("channel,name", [("sounds", "greetings/hello.mp3"), ("wake_models", "hey_domo.onnx")])
def test_a_proper_entry_is_still_written(tmp_path, monkeypatch, channel, name):
    cache = tmp_path / "cache"
    body = b"fine"
    assert _sync(channel, cache, {name: hashlib.sha256(body).hexdigest()}, body, monkeypatch) == 1
    assert (cache / name).read_bytes() == body


def test_the_prune_never_follows_a_junction_or_link_out(tmp_path):
    cache = tmp_path / "cache"
    (cache / "greetings").mkdir(parents=True)
    _link_out(cache / "voices", tmp_path / "server-voices")
    precious = tmp_path / "server-voices" / "greet_x.mp3"
    precious.write_bytes(b"the server's")
    (cache / sound_sync.RECORD_NAME).write_text('["voices/greet_x.mp3", "greetings/old.mp3"]')
    (cache / "greetings" / "old.mp3").write_bytes(b"old")
    sound_sync._prune(cache, set())
    assert precious.exists(), "a file reached through a link is never pruned"
    assert not (cache / "greetings" / "old.mp3").exists()


def test_the_code_channel_keeps_inside_its_tree_too(tmp_path):
    root = tmp_path / "app" / "satellite"
    root.mkdir(parents=True)
    _link_out(root / "scripts", tmp_path / "elsewhere")
    assert code_sync.safe_dest(root, "client.py") == (root / "client.py").resolve()
    assert code_sync.safe_dest(root, "scripts/x.ps1") is None
    assert code_sync.safe_dest(root, "C:/Windows/x.py") is None
