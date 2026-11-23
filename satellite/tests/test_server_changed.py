"""A room whose server changed says so (1.5.0, S3-4).

The house was reinstalled without keeping its identity (or another house
took the address): the server at the room's own address proves a DIFFERENT
key. The room keeps refusing it (no re-pin, nothing recorded), and instead
of the generic no-server orange it shows its own LED pattern (teal) and
says "my server changed, set me up again": once when it is first seen, then
at most every 12 hours, and whenever someone says the wake word.

What a rogue server can and can't do: it can make the room SAY this (a
valid signature from any key over our nonce and the dialled address is
enough), rate-limited; it can't make the room trust it, and the room sends
it nothing it doesn't send any server (the question carries a nonce and the
dialled address, never the pin). A relayed or unsigned answer doesn't even
count as "changed".

No network, no audio stack, no Postgres.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from satellite import leds, server_identity
from satellite.tests._client_import import import_client
from satellite.tests.test_server_identity import _health_opener, _server

client = import_client()

ADDR_URL = "ws://192.168.1.20:6370"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(server_identity, "RECORD_SIDECAR", tmp_path / "recorded.json")
    monkeypatch.setattr(server_identity, "ROOT_PIN", tmp_path / "etc-pin.json")
    monkeypatch.setattr(server_identity, "PENDING_SERVER_SIDECAR", tmp_path / "pending-server.json")
    monkeypatch.setattr(client, "_SERVER_HAS_NO_IDENTITY", False)
    monkeypatch.setattr(client, "SERVER_CHANGED_SAID_SIDECAR", tmp_path / "server-changed-said")
    server_identity.forget_different_server()


def _cfg(fingerprint: str):
    return types.SimpleNamespace(domovoi_url=ADDR_URL, server_fingerprint=fingerprint, room_id="kitchen")


# ─── detection: only a complete, valid proof for another key ──────────────

def test_a_reinstalled_server_on_our_address_is_a_changed_server_and_still_refused():
    _a, _pa, ours = _server(1)
    seed_b, public_b, theirs = _server(2)
    asked: list[dict] = []
    ok = client._verify_server_identity(_cfg(ours), opener=_health_opener(seed_b, public_b, asked=asked))
    assert ok is False
    assert client._LAST_IDENTITY_CHANGED == theirs
    assert "the server changed" in client._LAST_IDENTITY_FAILURE
    assert "Set this room up again" in client._LAST_IDENTITY_FAILURE
    # No re-pin and nothing recorded: the pin is still ours, from config.
    assert server_identity.pinned_fingerprint(ours) == (ours, "config")
    assert not server_identity.RECORD_SIDECAR.exists()
    assert server_identity.read_pending_server() == (None, None)
    # Nothing leaked: the question is the nonce and the dialled address.
    assert asked and all(set(q) == {"challenge", "addr"} for q in asked)
    assert all(ours not in str(q) for q in asked)
    # And the next check refuses it again.
    assert client._verify_server_identity(_cfg(ours), opener=_health_opener(seed_b, public_b)) is False


def test_our_own_server_clears_it():
    seed, public, ours = _server(1)
    client._LAST_IDENTITY_CHANGED = "SHA256:old"
    assert client._verify_server_identity(_cfg(ours), opener=_health_opener(seed, public)) is True
    assert client._LAST_IDENTITY_CHANGED is None


@pytest.mark.parametrize("kind", ["relay", "unsigned", "unbound", "lying-fingerprint"])
def test_an_answer_that_proves_nothing_is_not_a_changed_server(kind):
    """A relay (signed for another address), a missing signature, an
    answer bound to no address, or a fingerprint that is not the key's:
    refused as before, and never announced as "my server changed"."""
    _a, _pa, ours = _server(1)
    seed_b, public_b, _theirs = _server(2)
    opener = {
        "relay": _health_opener(seed_b, public_b, bind="192.168.1.99:6370"),
        "unsigned": _health_opener(seed_b, public_b, sign_challenge=False),
        "unbound": _health_opener(seed_b, public_b, bind=None),
        "lying-fingerprint": _health_opener(seed_b, public_b, fingerprint=ours),
    }[kind]
    assert client._verify_server_identity(_cfg(ours), opener=opener) is False
    assert client._LAST_IDENTITY_CHANGED is None
    assert server_identity.last_different_server() is None


def test_the_module_raises_its_own_kind_only_after_the_signature_checks():
    _a, _pa, ours = _server(1)
    seed_b, public_b, theirs = _server(2)
    with pytest.raises(server_identity.DifferentServer) as e:
        server_identity.verify_server(
            "http://192.168.1.20:6370", expected_fingerprint=ours,
            opener=_health_opener(seed_b, public_b),
        )
    assert e.value.fingerprint == theirs and e.value.addr == "192.168.1.20:6370"
    assert isinstance(e.value, server_identity.IdentityError)   # every caller still refuses it
    assert server_identity.last_different_server()[1:] == ("192.168.1.20:6370", theirs)


# ─── the rate limit ───────────────────────────────────────────────────────

def test_said_once_then_at_most_every_twelve_hours(tmp_path):
    side = tmp_path / "said"
    t0 = 1_800_000_000.0
    assert client._server_changed_say_due(t0, side)
    client._server_changed_said(t0, side)
    assert not client._server_changed_say_due(t0 + 60, side)
    assert not client._server_changed_say_due(t0 + 11.9 * 3600, side)
    assert client._server_changed_say_due(t0 + 12 * 3600, side)
    # A clock set back (or a garbled file) doesn't silence it for good.
    assert client._server_changed_say_due(t0 - 3600, side)
    side.write_text("nonsense", encoding="utf-8")
    assert client._server_changed_say_due(t0, side)


class _Leds:
    def __init__(self):
        self.state = "idle"
        self.history: list[str] = []

    def set_state(self, s):
        self.state = s
        self.history.append(s)

    def set_state_if(self, expected, new):
        if self.state == expected:
            self.set_state(new)

    def set_state_unless(self, unless, new):
        if self.state != unless:
            self.set_state(new)


def _sat(monkeypatch, tmp_path: Path, *, clip: bool = True):
    sat = object.__new__(client.Satellite)
    sat._leds = _Leds()
    played: list[str] = []

    def play(path, label):
        played.append(label)
        sat._leds.set_state("error")
        sat._leds.set_state_unless("listening", "idle")

    sat._play_canned_mp3 = play
    mp3 = tmp_path / "server_changed.mp3"
    if clip:
        mp3.write_bytes(b"ID3")
    monkeypatch.setattr(client, "_resolve_sound", lambda rel: tmp_path / rel)
    return sat, played


def test_the_room_says_it_once_shows_teal_and_doesnt_nag(monkeypatch, tmp_path):
    sat, played = _sat(monkeypatch, tmp_path)
    sat._server_changed("SHA256:new")
    assert played == ["server-changed"]
    # The ring ends on the teal pattern, not idle after the clip.
    assert sat._leds.state == "server_changed"
    for _ in range(5):                      # every reconnect attempt after
        sat._server_changed("SHA256:new")
    assert played == ["server-changed"]     # not said again
    assert sat._leds.state == "server_changed"
    # A restart (a fresh object) doesn't say it again either: it is on disk.
    sat2, played2 = _sat(monkeypatch, tmp_path)
    sat2._server_changed("SHA256:new")
    assert played2 == []
    assert sat2._leds.state == "server_changed"


def test_asking_the_room_gets_the_answer_every_time(monkeypatch, tmp_path):
    sat, played = _sat(monkeypatch, tmp_path)
    sat._server_changed("SHA256:new")
    assert sat._say_server_changed() and sat._say_server_changed()
    assert played == ["server-changed"] * 3


def test_without_the_clip_it_is_the_ring_alone(monkeypatch, tmp_path):
    sat, played = _sat(monkeypatch, tmp_path, clip=False)
    sat._server_changed("SHA256:new")
    assert played == [] and sat._leds.state == "server_changed"
    assert not sat._say_server_changed()


def test_a_session_up_clears_the_pattern(monkeypatch, tmp_path):
    sat, _ = _sat(monkeypatch, tmp_path)
    sat._server_changed("SHA256:new")
    sat._server_changed_cleared()
    assert sat._server_changed_fp is None and sat._leds.state == "idle"


def test_the_ready_frame_clears_it_and_the_no_server_ring_steps_aside():
    import inspect

    src = inspect.getsource(client.Satellite)
    ready = src.index('self._set_conn("connected")')
    assert src.index("self._server_changed_cleared()", ready) - ready < 200
    loop = inspect.getsource(client.Satellite.run)
    assert "unreachable == _UNREACHABLE_AFTER and self._server_changed_fp" in loop
    assert loop.index("self._server_changed_fp") < loop.index('_setup_status("no-server")')
    session = inspect.getsource(client.Satellite._run_session)
    assert "self._server_changed, changed" in session


def test_the_core_renders_the_line_for_every_voice():
    from domovoi import canned_sounds

    names = [entry[0] for entry in canned_sounds._CANNED]
    assert client.SERVER_CHANGED_CLIP in names
    text = dict((e[0], e[2]) for e in canned_sounds._CANNED)[client.SERVER_CHANGED_CLIP]
    assert "set me up again" in text


# ─── the LED pattern ──────────────────────────────────────────────────────

def test_teal_is_used_by_no_other_state():
    teal = leds.COLOR_SERVER_CHANGED
    others = {leds.COLOR_LISTENING, leds.COLOR_THINKING, leds.COLOR_SPEAKING, leds.COLOR_ERROR,
              leds.COLOR_DROPIN, leds.COLOR_RECORDING, leds.COLOR_SUPPORT, leds.COLOR_QUIET,
              leds.XVF_LISTEN_BASE, leds.XVF_SPEAK, leds.XVF_THINK}
    others |= {v for k, v in leds.SETUP_COLORS.items() if k != "server-changed"}
    assert teal not in others
    assert leds.SETUP_COLORS["server-changed"] == teal
    assert leds.SETUP_COLORS["no-server"] != teal


class _Recorder(leds._WS2812XvfDriver):
    def __init__(self) -> None:      # no hardware
        self.calls: list[tuple[str, ...]] = []

    def _run(self, *args: str) -> None:
        self.calls.append(args)


def test_xvf_breathes_teal():
    d = _Recorder()
    d.render_state("server_changed")
    assert ("led_color", leds._hex(leds.COLOR_SERVER_CHANGED)) in d.calls
    assert d.calls[-1] == ("led_effect", "1")


class _Pixels:
    native_states = False

    def __init__(self):
        self.frames: list[tuple] = []
        self.px: dict[int, tuple] = {}

    def set_pixel(self, i, r, g, b):
        self.px[i] = (r, g, b)

    def show(self):
        self.frames.append(tuple(self.px[i] for i in sorted(self.px)))

    def clear(self):
        pass

    def close(self):
        pass

    def render_state(self, state, support=False):
        pass


def test_apa102_double_blinks_teal_until_another_state(monkeypatch):
    monkeypatch.setattr(leds, "SERVER_CHANGED_BLINKS", ((0.01, 0.01), (0.01, 0.03)))
    c = leds.LEDController(leds.LEDConfig(enabled=False, num_leds=3))
    drv = _Pixels()
    c._driver = drv
    c.set_state("server_changed")
    import threading

    t = threading.Thread(target=c._run_pixel, daemon=True)
    t.start()
    import time

    time.sleep(0.25)
    c.set_state("idle")
    time.sleep(0.05)
    c._stop.set()
    c._changed.set()
    t.join(timeout=2)
    teal = (leds.COLOR_SERVER_CHANGED,) * 3
    off = (leds.COLOR_OFF,) * 3
    assert drv.frames.count(teal) >= 4 and off in drv.frames
    assert drv.frames[-1] == off    # idle after
