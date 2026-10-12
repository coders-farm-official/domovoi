"""The shared protocol fixtures (docs/uml/satellite-fixtures.json), from the
Pi client's side.

S0 (2026-10-12) taught the core a hello's optional `supports` list and
`board`; a hello without them is a Pi and keeps everything it always got.
The core's tests check the core against these fixtures
(domovoi/tests/test_satellite_supports.py). These check the other half:
that today's Pi client really is the fixtures' Pi — its hello carries the
same keys and no `supports` or `board` — and that it handles every frame
the fixtures say the core may send a Pi. The Android room mode's JVM tests
read the same file later, so the two implementations can't drift apart.

Static on purpose (the hello is read off the source, not captured from a
session): it needs no audio stack, no event loop and no server.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = json.loads(
    (ROOT / "docs" / "uml" / "satellite-fixtures.json").read_text(encoding="utf-8")
)
CLIENT = ast.parse((ROOT / "satellite" / "client.py").read_text(encoding="utf-8"))
PI_DEVICES = ("pi_2mic_hat", "pi_xvf3800")


def _client_hello_keys() -> list[str]:
    """The keys of the dict literal the client sends as its hello."""
    found = []
    for node in ast.walk(CLIENT):
        if not isinstance(node, ast.Dict):
            continue
        pairs = dict(zip(
            [k.value if isinstance(k, ast.Constant) else None for k in node.keys],
            node.values,
        ))
        kind = pairs.get("type")
        if isinstance(kind, ast.Constant) and kind.value == "hello":
            found.append([k for k in pairs if isinstance(k, str)])
    assert len(found) == 1, f"expected one hello literal in client.py, found {len(found)}"
    return found[0]


def _handled_frame_types() -> set[str]:
    """Every `t == "<type>"` the client's text-frame handler branches on."""
    handler = next(
        n for n in ast.walk(CLIENT)
        if isinstance(n, ast.FunctionDef) and n.name == "_handle_text_frame"
    )
    types = set()
    for node in ast.walk(handler):
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name) and node.left.id == "t"
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Constant)
        ):
            types.add(node.comparators[0].value)
    return types


def test_the_fixtures_describe_the_four_devices():
    assert set(FIXTURES["devices"]) == {*PI_DEVICES, "linux_pc", "android_room"}
    assert FIXTURES["protocol_version"] == "0.1"
    for name, device in FIXTURES["devices"].items():
        assert device["hello"]["type"] == "hello", name
        assert device["ready"]["type"] == "ready", name


def test_today_s_pi_hello_is_the_fixtures_pi_hello():
    keys = _client_hello_keys()
    assert "supports" not in keys and "board" not in keys, (
        "a Pi sends no `supports` and no `board`: that is what keeps it on "
        "today's behaviour in the core"
    )
    for device in PI_DEVICES:
        assert set(FIXTURES["devices"][device]["hello"]) == set(keys), device


def test_the_pi_handles_every_frame_the_core_may_send_it():
    handled = _handled_frame_types()
    for device in PI_DEVICES:
        expect = FIXTURES["devices"][device]["expect"]
        # "announcement" is an out-of-turn response_start, not a type of its own.
        frames = set(FIXTURES["always_sent"]) | set(expect["frames_sent"])
        frames.discard("announcement")
        missing = frames - handled
        assert not missing, f"{device}: the client has no branch for {sorted(missing)}"


def test_the_pi_declares_what_the_fixtures_say_it_declares():
    """The boolean keys the Pi's hello sets are the ones the fixtures' Pi
    sets (the core reads them as before; `supports` never enters into it)."""
    src = (ROOT / "satellite" / "client.py").read_text(encoding="utf-8")
    hello = FIXTURES["devices"]["pi_2mic_hat"]["hello"]
    for key in ("speech_pause", "announce_after_session_end"):
        assert hello[key] is True
        assert f'"{key}": True,' in src, key
    assert '"capture_control": self.cfg.early_commit,' in src
    assert '"supports_full_duplex": self.cfg.device.supports_full_duplex,' in src
