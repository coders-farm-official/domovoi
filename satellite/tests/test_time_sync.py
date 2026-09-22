"""domovoi-sync-time - the root helper that copies the server's clock and
time zone onto a satellite.

It is a script, not a module (installed to /usr/local/sbin, root-owned,
with a sudoers line for exactly that path), so it is loaded here by path.
Every privileged action is behind an injectable seam; nothing in this file
touches the clock, /etc, or the network.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.machinery
import importlib.util
import json
import types
import urllib.error
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "scripts" / "domovoi-sync-time"


@pytest.fixture(scope="module")
def helper():
    loader = importlib.machinery.SourceFileLoader("domovoi_sync_time", str(HELPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _pins_in_tmp(helper, tmp_path, monkeypatch):
    """Point the root-owned pins at this test's own directory. Absent by
    default, which is exactly the unpinned unit the older tests describe."""
    monkeypatch.setattr(helper, "SERVER_URL_PIN", str(tmp_path / "server.url"))
    monkeypatch.setattr(
        helper, "SERVER_IDENTITY_PIN", str(tmp_path / "server-identity.json")
    )
    monkeypatch.setattr(helper, "VERIFIER_DIR", str(tmp_path / "lib"))
    return tmp_path


def test_it_is_a_self_contained_python3_script():
    """Root runs it on the satellite user's say-so, so it must not import
    anything from ~/domovoi, which that user's code sync rewrites."""
    text = HELPER.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env python3\n")
    assert "from satellite" not in text and "import satellite" not in text
    assert "\r" not in text


def test_the_config_url_is_accepted_as_is(helper):
    assert helper.http_base("ws://192.168.0.117:6370") == "http://192.168.0.117:6370"
    assert helper.http_base("wss://core.lan:6370/v1/stream/den") == "https://core.lan:6370"
    assert helper.http_base("http://core:6370/") == "http://core:6370"


# ─── deciding ─────────────────────────────────────────────────────────────


def test_the_plan_charges_half_the_round_trip_to_the_server(helper):
    p = helper.plan(
        {"tz": "America/New_York", "epoch": 1000.0}, 0.4,
        now=990.0, current_tz="Europe/London",
    )
    assert p["target_epoch"] == pytest.approx(1000.2)
    assert p["drift"] == pytest.approx(10.2)
    assert p["step"] is True and p["tz_change"] is True


def test_a_close_clock_is_left_alone(helper):
    thr = helper.STEP_THRESHOLD_SEC
    assert helper.plan({"epoch": 1000.0}, 0.0, now=1000.0 - thr + 0.1)["step"] is False
    assert helper.plan({"epoch": 1000.0}, 0.0, now=1000.0 - thr)["step"] is True
    assert helper.plan({"epoch": 1000.0}, 0.0, now=1000.0 + thr)["step"] is True


def test_an_unchanged_zone_is_not_a_change(helper):
    p = helper.plan(
        {"tz": "America/New_York", "epoch": 1.0}, 0,
        now=1.0, current_tz="America/New_York",
    )
    assert p["tz_change"] is False and p["tz"] == "America/New_York"


def test_a_server_that_does_not_know_its_zone_changes_nothing(helper):
    p = helper.plan({"tz": None, "epoch": 1.0}, 0, now=1.0, current_tz="Europe/London")
    assert p["tz"] is None and p["tz_change"] is False
    p = helper.plan({"tz": "  ", "epoch": 1.0}, 0, now=1.0, current_tz=None)
    assert p["tz"] is None and p["tz_change"] is False


def test_a_missing_or_bogus_epoch_never_moves_the_clock(helper):
    for doc in ({}, {"epoch": "soon"}, {"epoch": True}, {"epoch": None}):
        p = helper.plan(doc, 0, now=5.0)
        assert p["drift"] is None and p["step"] is False, doc


# ─── applying ─────────────────────────────────────────────────────────────


class Recorder:
    def __init__(self, zone_ok=True, zone_result=True, clock_result=True):
        self.calls: list[tuple] = []
        self._zone_ok, self._zone, self._clock = zone_ok, zone_result, clock_result

    def zone_ok(self, tz):
        self.calls.append(("zone_ok", tz))
        return self._zone_ok

    def set_zone(self, tz, run):
        self.calls.append(("set_zone", tz))
        return self._zone

    def set_clock(self, epoch, run):
        self.calls.append(("set_clock", epoch))
        return self._clock

    def save(self, run):
        self.calls.append(("save",))

    def seams(self):
        return dict(run=None, zone_ok=self.zone_ok, zone_setter=self.set_zone,
                    clock_setter=self.set_clock, saver=self.save)


def _plan(**kw):
    base = dict(tz="America/New_York", tz_current="Europe/London", tz_change=True,
                now=990.0, target_epoch=1000.0, drift=10.0, step=True)
    base.update(kw)
    return base


def test_apply_sets_the_zone_then_steps_and_saves(helper):
    rec = Recorder()
    summary, failed = helper.apply(_plan(), **rec.seams())
    assert failed is False
    assert rec.calls == [
        ("zone_ok", "America/New_York"), ("set_zone", "America/New_York"),
        ("set_clock", 1000.0), ("save",),
    ]
    assert "tz America/New_York (was Europe/London)" in summary
    assert "clock stepped +10.0s" in summary


def test_a_dry_run_touches_nothing(helper):
    rec = Recorder()
    summary, failed = helper.apply(_plan(), dry_run=True, **rec.seams())
    assert failed is False
    assert [c[0] for c in rec.calls] == ["zone_ok"]
    assert "would change" in summary and "would step" in summary


def test_a_zone_this_device_does_not_know_is_refused(helper):
    """The server may name a zone the device's tzdata lacks; a symlink to
    nowhere is worse than the wrong zone. The clock is corrected anyway."""
    rec = Recorder(zone_ok=False)
    summary, failed = helper.apply(_plan(), **rec.seams())
    assert failed is True
    assert ("set_zone", "America/New_York") not in rec.calls
    assert "unknown to this device" in summary
    assert ("set_clock", 1000.0) in rec.calls


def test_the_hwclock_is_saved_only_after_a_real_step(helper):
    rec = Recorder(clock_result=False)
    summary, failed = helper.apply(_plan(), **rec.seams())
    assert failed is True and ("save",) not in rec.calls
    assert "FAILED to step" in summary

    rec = Recorder()
    summary, failed = helper.apply(_plan(step=False, drift=0.3), **rec.seams())
    assert failed is False
    assert ("save",) not in rec.calls and ("set_clock", 1000.0) not in rec.calls
    assert "within 0.30s" in summary


def test_nothing_to_do_is_said_plainly(helper):
    rec = Recorder()
    summary, failed = helper.apply(
        _plan(tz_current="America/New_York", tz_change=False, step=False, drift=0.01),
        **rec.seams(),
    )
    assert failed is False and rec.calls == []
    assert "(unchanged)" in summary and "left alone" in summary


def test_zone_names_that_escape_zoneinfo_are_refused(helper, monkeypatch, tmp_path):
    monkeypatch.setattr(helper, "ZONEINFO", tmp_path.as_posix())
    (tmp_path / "America").mkdir()
    (tmp_path / "America" / "New_York").write_bytes(b"TZif")
    assert helper.zone_is_installed("America/New_York") is True
    assert helper.zone_is_installed("../etc/passwd") is False
    assert helper.zone_is_installed("/etc/passwd") is False
    assert helper.zone_is_installed("") is False
    assert helper.zone_is_installed("Europe/Nowhere") is False


def test_set_zone_prefers_timedatectl_and_falls_back_to_the_link(helper, monkeypatch, tmp_path):
    calls = []

    def ok(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0)

    assert helper.set_zone("Asia/Tokyo", run=ok) is True
    assert calls == [["timedatectl", "set-timezone", "Asia/Tokyo"]]

    # No D-Bus (stage 1's boot target): timedatectl fails, the link it
    # would have made is made directly, and /etc/timezone follows.
    def no_dbus(cmd, **kw):
        return types.SimpleNamespace(returncode=1)

    links = []
    monkeypatch.setattr(helper, "LOCALTIME", (tmp_path / "localtime").as_posix())
    monkeypatch.setattr(helper, "ETC_TIMEZONE", (tmp_path / "timezone").as_posix())
    monkeypatch.setattr(helper.os, "symlink", lambda src, dst: links.append(("link", src, dst)))
    monkeypatch.setattr(helper.os, "replace", lambda a, b: links.append(("replace", a, b)))
    assert helper.set_zone("Asia/Tokyo", run=no_dbus) is True
    assert links[0] == ("link", "/usr/share/zoneinfo/Asia/Tokyo", (tmp_path / "localtime").as_posix() + ".domovoi-tmp")
    assert links[1][0] == "replace" and links[1][2] == (tmp_path / "localtime").as_posix()
    assert (tmp_path / "timezone").read_text(encoding="utf-8") == "Asia/Tokyo\n"


def test_set_clock_uses_the_syscall_then_date(helper, monkeypatch):
    set_to = []
    monkeypatch.setattr(helper.time, "CLOCK_REALTIME", 0, raising=False)
    monkeypatch.setattr(helper.time, "clock_settime", lambda clk, t: set_to.append(t), raising=False)
    assert helper.set_clock(1234.5, run=lambda *a, **k: pytest.fail("date not needed")) is True
    assert set_to == [1234.5]

    def denied(clk, t):
        raise OSError("EPERM")

    monkeypatch.setattr(helper.time, "clock_settime", denied, raising=False)
    calls = []

    def date(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0)

    assert helper.set_clock(1234.5, run=date) is True
    assert calls == [["date", "-u", "-s", "@1234.500"]]


# ─── the command ──────────────────────────────────────────────────────────


def test_usage_is_enforced(helper):
    assert helper.main([]) == 2
    assert helper.main(["a", "b"]) == 2


def test_an_unreachable_server_is_exit_one_with_a_reason(helper, monkeypatch, capsys):
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0, raising=False)

    def down(url):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(helper, "fetch", down)
    assert helper.main(["ws://192.168.0.117:6370"]) == 1
    out = capsys.readouterr().out
    assert "unreachable" in out and "http://192.168.0.117:6370/v1/time" in out


def test_a_non_root_caller_is_refused_before_any_network(helper, monkeypatch):
    monkeypatch.setattr(helper.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(helper, "fetch", lambda url: pytest.fail("must not fetch"))
    assert helper.main(["ws://x:6370"]) == 2
    # ...unless it only wants to look.
    monkeypatch.setattr(helper, "fetch", lambda url: ({"tz": None, "epoch": None}, 0.0))
    assert helper.main(["ws://x:6370", "--dry-run"]) == 0


def test_the_verdict_is_one_line_and_the_exit_code_says_whether_it_applied(helper, monkeypatch, capsys):
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(helper, "fetch", lambda url: ({"tz": "America/New_York", "epoch": 1000.0}, 0.0))
    monkeypatch.setattr(helper, "current_zone", lambda: "America/New_York")
    monkeypatch.setattr(helper.time, "time", lambda: 1000.5)
    assert helper.main(["ws://x:6370"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert "(unchanged)" in out[0] and "within 0.50s" in out[0]

    # A plan that cannot be applied is exit 2, verdict still printed.
    monkeypatch.setattr(helper, "current_zone", lambda: "Europe/London")
    monkeypatch.setattr(helper, "zone_is_installed", lambda tz: False)
    assert helper.main(["ws://x:6370"]) == 2
    assert "unknown to this device" in capsys.readouterr().out


# --- whose clock this is -------------------------------------------------
#
# The address arrives on a command line from a process running as the
# satellite user, and this script sets the clock as root. On a card
# prepared from the dashboard, root wrote the answer down at adoption and
# that is the answer used.


def _keypair(seed_byte=1):
    from satellite import _ed25519

    seed = bytes([seed_byte]) * 32
    public = _ed25519.public_key(seed)
    digest = hashlib.sha256(public).digest()
    return seed, public, "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


class _R:
    def __init__(self, body):
        self._body, self.status = body, 200

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _signing_opener(seed, public, *, sign=True, fingerprint=None):
    """Answers /v1/health the way a core does, signing whatever nonce the
    helper actually sent."""
    from satellite import _ed25519

    def opener(url, timeout=None):
        challenge = url.split("challenge=", 1)[1]
        digest = hashlib.sha256(public).digest()
        block = {
            "algorithm": "ed25519",
            "fingerprint": fingerprint or (
                "SHA256:" + base64.b64encode(digest).decode().rstrip("=")
            ),
            "public_key": base64.b64encode(public).decode(),
            "challenge": challenge,
        }
        if sign:
            block["signature"] = base64.b64encode(
                _ed25519.sign(
                    seed, b"domovoi-health-v1\n" + challenge.encode("utf-8")
                )
            ).decode()
        return _R(json.dumps({"status": "ok", "identity": block}).encode("utf-8"))

    return opener


def test_without_a_pin_the_argument_stands(helper):
    """A hand-built unit, and every Pi flashed before pins existed."""
    assert helper.authorized_url("ws://192.168.0.117:6370", None) == (
        "ws://192.168.0.117:6370", "unpinned",
    )


def test_the_pinned_address_is_the_one_used(helper):
    assert helper.authorized_url(
        "ws://192.168.0.117:6370", "ws://192.168.0.117:6370"
    ) == ("ws://192.168.0.117:6370", "pinned")


def test_a_different_address_is_refused_by_name(helper):
    url, why = helper.authorized_url(
        "ws://192.168.0.9:6370", "ws://192.168.0.117:6370"
    )
    assert url is None
    assert "192.168.0.9" in why and "192.168.0.117" in why


def test_a_refused_address_leaves_the_clock_and_the_zone_alone(
    helper, monkeypatch, capsys, _pins_in_tmp,
):
    (_pins_in_tmp / "server.url").write_text(
        "ws://192.168.0.117:6370\n", encoding="utf-8"
    )
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(helper, "fetch", lambda url: pytest.fail("must not fetch"))
    monkeypatch.setattr(
        helper, "set_zone", lambda *a, **k: pytest.fail("must not set the zone")
    )
    monkeypatch.setattr(
        helper, "set_clock", lambda *a, **k: pytest.fail("must not step the clock")
    )
    assert helper.main(["ws://192.168.0.9:6370"]) == 2
    assert "this device's time source is" in capsys.readouterr().out


def test_the_pinned_address_is_still_synced(helper, monkeypatch, _pins_in_tmp):
    (_pins_in_tmp / "server.url").write_text(
        "ws://192.168.0.117:6370\n", encoding="utf-8"
    )
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(
        helper, "fetch",
        lambda url: ({"tz": "America/New_York", "epoch": 1000.0}, 0.0),
    )
    monkeypatch.setattr(helper, "current_zone", lambda: "America/New_York")
    monkeypatch.setattr(helper.time, "time", lambda: 1000.1)
    assert helper.main(["ws://192.168.0.117:6370"]) == 0


# --- and whether that address is still our server ------------------------


def test_the_server_that_signs_our_nonce_passes(helper):
    from satellite import _ed25519

    seed, public, fingerprint = _keypair()
    assert helper.verify_identity(
        "ws://192.168.0.117:6370", {"fingerprint": fingerprint},
        opener=_signing_opener(seed, public), verifier=_ed25519,
    ) == ""


def test_a_different_server_on_the_pinned_address_is_named(helper):
    from satellite import _ed25519

    _seed_a, _public_a, ours = _keypair(1)
    seed_b, public_b, _theirs = _keypair(2)
    assert "a different server" in helper.verify_identity(
        "ws://192.168.0.117:6370", {"fingerprint": ours},
        opener=_signing_opener(seed_b, public_b), verifier=_ed25519,
    )


def test_claiming_our_fingerprint_without_the_key_does_not_pass(helper):
    from satellite import _ed25519

    _seed_a, _public_a, ours = _keypair(1)
    seed_b, public_b, _ = _keypair(2)
    assert helper.verify_identity(
        "ws://192.168.0.117:6370", {"fingerprint": ours},
        opener=_signing_opener(seed_b, public_b, fingerprint=ours),
        verifier=_ed25519,
    )


def test_an_answer_with_no_signature_does_not_pass(helper):
    from satellite import _ed25519

    seed, public, fingerprint = _keypair()
    assert helper.verify_identity(
        "ws://192.168.0.117:6370", {"fingerprint": fingerprint},
        opener=_signing_opener(seed, public, sign=False), verifier=_ed25519,
    )


def test_a_unit_with_no_verifier_installed_carries_on(helper):
    """The compatibility promise: a payload older than the verifier still
    syncs its clock. The client proved the same server before calling us."""
    _seed, _public, fingerprint = _keypair()
    assert helper.verify_identity(
        "ws://192.168.0.117:6370", {"fingerprint": fingerprint}, verifier=None,
        opener=lambda *a, **k: pytest.fail("must not ask"),
    ) == ""


def test_a_server_that_cannot_prove_itself_changes_nothing(
    helper, monkeypatch, capsys, _pins_in_tmp,
):
    (_pins_in_tmp / "server-identity.json").write_text(
        json.dumps({"fingerprint": "SHA256:ours"}), encoding="utf-8"
    )
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(
        helper, "verify_identity",
        lambda url, pin, **k: "the server's signature did not verify",
    )
    monkeypatch.setattr(helper, "fetch", lambda url: pytest.fail("must not fetch"))
    monkeypatch.setattr(
        helper, "set_zone", lambda *a, **k: pytest.fail("must not set the zone")
    )
    monkeypatch.setattr(
        helper, "set_clock", lambda *a, **k: pytest.fail("must not step the clock")
    )
    assert helper.main(["ws://192.168.0.117:6370"]) == 2
    assert "refusing the time from" in capsys.readouterr().out


def test_a_server_that_proves_itself_is_synced(
    helper, monkeypatch, _pins_in_tmp,
):
    (_pins_in_tmp / "server-identity.json").write_text(
        json.dumps({"fingerprint": "SHA256:ours"}), encoding="utf-8"
    )
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(helper, "verify_identity", lambda url, pin, **k: "")
    monkeypatch.setattr(
        helper, "fetch",
        lambda url: ({"tz": "America/New_York", "epoch": 1000.0}, 0.0),
    )
    monkeypatch.setattr(helper, "current_zone", lambda: "America/New_York")
    monkeypatch.setattr(helper.time, "time", lambda: 1000.1)
    assert helper.main(["ws://192.168.0.117:6370"]) == 0


def test_a_malformed_identity_pin_is_treated_as_no_pin(helper, _pins_in_tmp):
    (_pins_in_tmp / "server-identity.json").write_text("{", encoding="utf-8")
    assert helper.read_identity_pin(helper.SERVER_IDENTITY_PIN) is None
    (_pins_in_tmp / "server-identity.json").write_text(
        json.dumps({"fingerprint": "not-a-fingerprint"}), encoding="utf-8"
    )
    assert helper.read_identity_pin(helper.SERVER_IDENTITY_PIN) is None


def test_the_zone_is_still_checked_against_this_devices_tzdata(helper):
    """A name the device's own tzdata does not know is never applied, pin
    or no pin: a bogus symlink is worse than the wrong zone."""
    assert helper.zone_is_installed("../../etc/shadow") is False
    assert helper.zone_is_installed("/etc/localtime") is False
    assert helper.zone_is_installed("") is False
