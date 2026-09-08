"""The Wi-Fi setup portal — form handling and the captive-portal routes.

Driven against a REAL http.server bound to loopback on an ephemeral port,
so the routing, the redirects and the form round-trip are exercised the way
a phone would exercise them. No radio, no root, no nmcli: the AP calls go
through an injected ``run``.
"""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from satellite import portal_transport as pt
from satellite import provisioning_protocol as proto

NONCE = "a1b2c3d4e5f60718"
PSK = "correct-horse-battery"

SCAN_STDOUT = "HomeNet:71\nHomeNet:44\nNeighbour:31\n:55\n"


# p2p-dev-wlan0 is listed FIRST on purpose: it's a virtual companion that
# cannot host an AP, and nmcli will happily pick it if nobody says otherwise.
DEVICE_STATUS = "lo:loopback\np2p-dev-wlan0:wifi-p2p\nwlan0:wifi\n"
ACTIVE_CONNECTIONS = "lo\ndomovoi-setup\n"


def _fake_run(calls, *, fail_on=None, active=ACTIVE_CONNECTIONS):
    """``fail_on`` is a substring; any command containing it returns rc=1."""
    def run(cmd, **kw):
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if fail_on and fail_on in joined:
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="Error: nmcli said no.")
        if "--active" in joined:
            stdout = active
        elif "wifi list" in joined:
            stdout = SCAN_STDOUT
        elif "device status" in joined:
            stdout = DEVICE_STATUS
        else:
            stdout = ""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
    return run


@pytest.fixture
def portal(monkeypatch, tmp_path):
    """A portal serving on loopback, with the radio faked out. state_dir is
    redirected so the approval-code sidecar never touches a real home."""
    calls: list[list[str]] = []
    monkeypatch.setattr(pt.shutil, "which", lambda name: f"/usr/bin/{name}")
    transport = pt.PortalTransport(
        ap_ssid="Domovoi-Setup-A4F2",
        ap_psk="unit-test-key",
        device_profile="xvf3800_usb",
        profiles=["xvf3800_usb", "respeaker_2mic_hat"],
        ip="127.0.0.1",
        bind_host="127.0.0.1",       # the real default is 0.0.0.0
        port=0,                      # ephemeral — never needs root
        state_dir=tmp_path / "domovoi-state",
        run=_fake_run(calls),
    )
    info = proto.build_device_info(
        nonce=NONCE,
        mac="b8:27:eb:aa:bb:cc",
        board="raspberry_pi_zero_2_w",
        model="Raspberry Pi Zero 2 W",
        profiles_supported=["xvf3800_usb"],
    )
    transport.expose(info)
    transport.calls = calls
    yield transport
    transport.withdraw()


def _url(transport, path):
    host, port = transport._server.server_address[:2]
    return f"http://{host}:{port}{path}"


def _get(transport, path, redirects=True):
    opener = urllib.request.build_opener()
    if not redirects:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **kw):
                return None
        opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(_url(transport, path), timeout=5) as r:
            return r.status, r.read().decode("utf-8"), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), e.headers


def _post(transport, path, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(_url(transport, path), data=data, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.read().decode("utf-8")


# ─── room slug ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("Kitchen", "kitchen"),
    ("Living Room", "living-room"),
    ("  Back   Porch  ", "back-porch"),
    ("Kids' Room!", "kids-room"),
    ("den---2", "den-2"),
    ("--Attic--", "attic"),
    ("Bedroom #1", "bedroom-1"),
])
def test_slugify_room(raw, expected):
    assert proto.slugify_room(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "!!!", "???", "-", "x" * 40])
def test_slugify_room_rejects(raw):
    with pytest.raises(proto.ProvisionInvalid):
        proto.slugify_room(raw)


# ─── form → payload ───────────────────────────────────────────────────────


def test_build_payload_happy_path(portal):
    payload = portal.build_payload({
        "ssid": "HomeNet", "psk": PSK, "room": "kitchen",
    })
    assert payload["room_id"] == "kitchen"
    assert payload["device_profile"] == "xvf3800_usb"
    assert payload["wifi"]["ssid"] == "HomeNet"
    assert payload["pairing_token"]                       # self-generated
    # Blank address means "discover at first start", not empty string —
    # validate_provision requires a non-empty value.
    assert payload["domovoi_url"] == proto.AUTO_DISCOVER_URL


def test_custom_room_overrides_picker_and_is_slugged(portal):
    payload = portal.build_payload({
        "ssid": "HomeNet", "psk": PSK,
        "room": "kitchen", "room_custom": "Back Porch",
    })
    assert payload["room_id"] == "back-porch"


def test_explicit_url_is_kept(portal):
    payload = portal.build_payload({
        "ssid": "HomeNet", "psk": PSK, "room": "office",
        "url": "ws://192.168.0.117:6370",
    })
    assert payload["domovoi_url"] == "ws://192.168.0.117:6370"


@pytest.mark.parametrize("fields,fragment", [
    ({"psk": PSK, "room": "kitchen"}, "network"),
    ({"ssid": "HomeNet", "room": "kitchen"}, "password"),
    ({"ssid": "HomeNet", "psk": PSK, "room": ""}, "room"),
    ({"ssid": "HomeNet", "psk": PSK, "room": "kitchen",
      "profile": "nonexistent"}, "microphone"),
])
def test_build_payload_rejections(portal, fields, fragment):
    with pytest.raises(proto.ProvisionInvalid) as e:
        portal.build_payload(fields)
    assert fragment in str(e.value).lower()


def test_payload_validates_against_the_session_nonce(portal):
    payload = portal.build_payload({
        "ssid": "HomeNet", "psk": PSK, "room": "kitchen",
    })
    # build_payload already ran validate_provision; prove the nonce binds by
    # rejecting the same document against a different session.
    doc = proto.build_provision(
        nonce="0000000000000000", room_id="kitchen",
        domovoi_url=proto.AUTO_DISCOVER_URL, sat_type="voice",
        device_profile="xvf3800_usb", pairing_token=payload["pairing_token"],
        wifi_ssid="HomeNet", wifi_psk=PSK,
    )
    with pytest.raises(proto.ProvisionInvalid):
        proto.validate_provision(doc, portal.nonce)


# ─── captive-portal routing ───────────────────────────────────────────────


@pytest.mark.parametrize("path", pt.PROBE_PATHS)
def test_probe_urls_redirect_to_the_portal(portal, path):
    """Failing each OS's connectivity check is what makes the sign-in sheet
    open on its own."""
    status, _body, headers = _get(portal, path, redirects=False)
    assert status == 302
    assert headers["Location"] == f"http://{portal.ip}/"


def test_unknown_paths_also_redirect(portal):
    status, _body, headers = _get(portal, "/anything/at/all", redirects=False)
    assert status == 302
    assert headers["Location"] == f"http://{portal.ip}/"


def test_root_serves_the_form_with_scanned_networks(portal):
    status, body, _h = _get(portal, "/")
    assert status == 200
    assert "<form" in body and 'action="/provision"' in body
    # Strongest-first, deduplicated, hidden (empty SSID) dropped.
    assert body.index("HomeNet") < body.index("Neighbour")
    # One <option> per SSID (the name appears twice inside it — value + text).
    assert body.count('<option value="HomeNet"') == 1


def test_form_needs_no_javascript(portal):
    _s, body, _h = _get(portal, "/")
    assert "<script" not in body.lower()


def test_device_info_endpoint_matches_the_gadget_document(portal):
    status, body, _h = _get(portal, "/device-info")
    assert status == 200
    assert proto.validate_device_info(json.loads(body))["nonce"] == NONCE


# ─── the submit round-trip ────────────────────────────────────────────────


def test_submit_answers_the_phone_then_hands_over(portal):
    """The response must reach the phone before the AP drops — accepting the
    credentials tears down the network it is reading over."""
    status, body = _post(portal, "/provision", {
        "ssid": "HomeNet", "psk": PSK, "room": "kitchen",
    })
    assert status == 200
    assert portal.approval_code in body          # for the dashboard approval
    assert "kitchen" in body
    assert PSK not in body

    payload = portal.wait_for_provision(NONCE)   # already queued
    assert payload["room_id"] == "kitchen"
    assert payload["wifi"]["psk"] == PSK


def test_bad_room_redisplays_the_form_without_the_password(portal):
    status, body = _post(portal, "/provision", {
        "ssid": "HomeNet", "psk": PSK, "room": "", "room_custom": "!!!",
    })
    assert status == 200
    assert "<form" in body
    assert PSK not in body
    assert portal._queue.empty()                 # nothing handed over


def test_clear_provision_drops_the_payload(portal):
    portal._queue.put({"room_id": "kitchen"})
    portal.clear_provision()
    assert portal._queue.empty()


# ─── AP lifecycle ─────────────────────────────────────────────────────────


def test_the_server_listens_before_the_ap_exists(monkeypatch, tmp_path):
    """A phone probes the instant it associates. If nothing answers, the OS
    records "connected, no internet" with no sign-in offer AND caches that
    verdict against the SSID — so the prompt never appears again on that
    device. Listening first means the first probe is always answered."""
    monkeypatch.setattr(pt.shutil, "which", lambda n: f"/usr/bin/{n}")
    seen = {}
    calls = []
    base = _fake_run(calls)

    t = _transport(calls, tmp_path)

    def run(cmd, **kw):
        if "hotspot" in " ".join(cmd):
            seen["server_up_when_ap_raised"] = t._server is not None
        return base(cmd, **kw)

    t.run = run
    t.expose(_info())
    try:
        assert seen["server_up_when_ap_raised"] is True
    finally:
        t.withdraw()


def test_a_failed_ap_leaves_no_socket_behind(monkeypatch, tmp_path):
    monkeypatch.setattr(pt.shutil, "which", lambda n: f"/usr/bin/{n}")
    t = _transport([], tmp_path, fail_on="wifi hotspot")
    with pytest.raises(RuntimeError):
        t.expose(_info())
    assert t._server is None


def test_expose_scans_before_raising_the_ap(portal):
    """Once the radio is hosting it can't survey the neighbourhood, so the
    scan has to come first."""
    joined = [" ".join(c) for c in portal.calls]
    scan = next(i for i, c in enumerate(joined) if "list" in c)
    hotspot = next(i for i, c in enumerate(joined) if "hotspot" in c)
    assert scan < hotspot


def test_ap_psk_is_never_logged(portal, caplog):
    with caplog.at_level("DEBUG"):
        portal._start_ap()
    assert "unit-test-key" not in caplog.text


def test_withdraw_brings_the_ap_down_and_stops_serving(portal):
    portal.withdraw()
    assert any("down" in " ".join(c) for c in portal.calls)
    assert portal._server is None
    with pytest.raises(Exception):
        _get(portal, "/")


def test_no_server_thread_survives_withdraw(portal):
    portal.withdraw()
    assert not any(
        t.name == "domovoi-portal" and t.is_alive() for t in threading.enumerate()
    )


# ─── which transport an image boots into ──────────────────────────────────


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    from satellite import provisioning_mode as pm
    monkeypatch.setattr(pm, "CONFIG_DIR", tmp_path)
    return tmp_path


def test_baked_ap_credentials_select_the_portal(cfg, monkeypatch):
    from satellite import provisioning_mode as pm
    monkeypatch.setattr(pt.shutil, "which", lambda name: f"/usr/bin/{name}")
    (cfg / "ap.json").write_text(
        json.dumps({"ssid": "Domovoi-Setup-9C1E", "psk": "unit-test-key"})
    )
    (cfg / "image_device_profile").write_text("xvf3800_usb\n")

    transport = pm._default_transport()
    assert isinstance(transport, pt.PortalTransport)
    assert transport.ap_ssid == "Domovoi-Setup-9C1E"
    assert transport.device_profile == "xvf3800_usb"


def test_no_ap_credentials_means_the_usb_gadget(cfg):
    from satellite import provisioning_mode as pm
    assert isinstance(pm._default_transport(), pm.GadgetBackend)


@pytest.mark.parametrize("body", ['{"ssid": "x"}', "not json at all", "{}"])
def test_unusable_ap_json_falls_back_rather_than_crashing(cfg, body):
    """A half-written sidecar must not strand a device with no way to be
    adopted at all."""
    from satellite import provisioning_mode as pm
    (cfg / "ap.json").write_text(body)
    assert isinstance(pm._default_transport(), pm.GadgetBackend)


def test_device_profile_falls_back_when_unstamped(cfg):
    from satellite import provisioning_mode as pm
    assert pm.image_device_profile() == "respeaker_2mic_hat"
    (cfg / "image_device_profile").write_text("xvf3800_usb\n")
    assert pm.image_device_profile() == "xvf3800_usb"


# ─── binding and the AP address ───────────────────────────────────────────
#
# Found on hardware: the server bound PORTAL_IP explicitly and died with
# EADDRNOTAVAIL because NetworkManager's shared mode had raised the AP on
# its own 10.42.0.1 instead. The service crash-looped 27 times while the
# setup network flapped up and down.


def test_the_server_binds_every_interface_by_default():
    """A specific bind races the AP's address assignment; the socket is
    created microseconds after nmcli returns."""
    assert pt.BIND_HOST == "0.0.0.0"
    transport = pt.PortalTransport(
        ap_ssid="x", ap_psk="y", device_profile="xvf3800_usb",
    )
    assert transport.bind_host == "0.0.0.0"
    assert transport.ip == pt.PORTAL_IP        # still advertised for DNS


def test_bind_host_is_separate_from_the_advertised_address(portal):
    """The redirect URL and DNS target must stay the advertised address even
    though the socket listens more broadly."""
    status, _body, headers = _get(portal, "/generate_204", redirects=False)
    assert status == 302
    assert headers["Location"] == f"http://{portal.ip}/"


def test_the_ap_is_pinned_to_the_advertised_address(portal):
    """The captive-portal DNS drop-in is baked at prepare time and points at
    a FIXED address. If the AP comes up on NetworkManager's own subnet
    instead, every hostname resolves somewhere nothing is listening."""
    joined = [" ".join(c) for c in portal.calls]
    modify = next(c for c in joined if "connection modify" in c)
    assert "ipv4.addresses" in modify
    assert f"{portal.ip}/24" in modify
    assert "ipv4.method shared" in modify
    # and re-activated so the new address actually applies
    assert any("connection up" in c for c in joined)


def test_pinning_happens_after_the_hotspot_exists(portal):
    joined = [" ".join(c) for c in portal.calls]
    hotspot = next(i for i, c in enumerate(joined) if "hotspot" in c)
    modify = next(i for i, c in enumerate(joined) if "connection modify" in c)
    assert hotspot < modify


# ─── nmcli failures must not look like success ────────────────────────────
#
# Found on hardware: every nmcli call was fire-and-forget. `nmcli device wifi
# hotspot` failed, we set _ap_up = True regardless, bound the server on
# 0.0.0.0 (which succeeds with or without an AP), logged "setup portal up",
# and waited 21 minutes for a phone that had nothing to join. `nmcli
# connection show` listed only `lo`.


def _transport(calls, tmp_path, **kw):
    return pt.PortalTransport(
        ap_ssid="Domovoi-Setup-A4F2", ap_psk="unit-test-key",
        device_profile="xvf3800_usb", ip="127.0.0.1", bind_host="127.0.0.1",
        port=0, state_dir=tmp_path / "state", run=_fake_run(calls, **kw),
    )


def _info():
    return proto.build_device_info(
        nonce=NONCE, mac="b8:27:eb:aa:bb:cc", board="raspberry_pi_zero_2_w",
        model="Raspberry Pi Zero 2 W", profiles_supported=["xvf3800_usb"],
    )


def test_a_failed_hotspot_raises_instead_of_pretending(monkeypatch, tmp_path):
    monkeypatch.setattr(pt.shutil, "which", lambda n: f"/usr/bin/{n}")
    calls = []
    t = _transport(calls, tmp_path, fail_on="wifi hotspot")
    with pytest.raises(RuntimeError, match="raising the setup AP"):
        t.expose(_info())
    assert t._ap_up is False, "must not claim an AP that was never created"


def test_the_failure_message_carries_nmcli_stderr(monkeypatch, tmp_path):
    monkeypatch.setattr(pt.shutil, "which", lambda n: f"/usr/bin/{n}")
    t = _transport([], tmp_path, fail_on="wifi hotspot")
    with pytest.raises(RuntimeError, match="nmcli said no"):
        t.expose(_info())


def test_the_failure_message_never_leaks_the_psk(monkeypatch, tmp_path):
    """The command is in argv; it must not end up in an exception or log."""
    monkeypatch.setattr(pt.shutil, "which", lambda n: f"/usr/bin/{n}")
    t = _transport([], tmp_path, fail_on="wifi hotspot")
    try:
        t.expose(_info())
    except RuntimeError as e:
        assert "unit-test-key" not in str(e)
    else:
        pytest.fail("should have raised")


def test_the_interface_is_named_and_p2p_is_skipped(portal):
    """p2p-dev-wlan0 cannot host an AP but sits right next to wlan0 in
    nmcli's list. Letting nmcli choose is how this failed."""
    hotspot = next(c for c in portal.calls if "hotspot" in " ".join(c))
    assert "ifname" in hotspot
    assert hotspot[hotspot.index("ifname") + 1] == "wlan0"


def test_no_wifi_interface_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(pt.shutil, "which", lambda n: f"/usr/bin/{n}")
    # Don't sit through the real boot-race wait.
    monkeypatch.setattr(pt, "_IFACE_WAIT_SEC", 0.05)
    monkeypatch.setattr(pt, "_IFACE_POLL_SEC", 0.01)
    monkeypatch.setattr(pt.time, "sleep", lambda s: None)
    calls = []
    t = pt.PortalTransport(
        ap_ssid="x", ap_psk="y", device_profile="xvf3800_usb",
        ip="127.0.0.1", bind_host="127.0.0.1", port=0,
        state_dir=tmp_path / "state",
        run=lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, stdout="lo:loopback\n", stderr=""),
    )
    with pytest.raises(RuntimeError, match="no Wi-Fi interface"):
        t.expose(_info())


def test_an_ap_that_isnt_actually_active_is_caught(monkeypatch, tmp_path):
    """nmcli can return 0 and still leave nothing usable behind."""
    monkeypatch.setattr(pt.shutil, "which", lambda n: f"/usr/bin/{n}")
    t = _transport([], tmp_path, active="lo\n")     # AP missing from active
    with pytest.raises(RuntimeError, match="not active"):
        t.expose(_info())


def test_teardown_failures_are_logged_not_raised(monkeypatch, tmp_path, caplog):
    """Bringing the AP up must fail loudly; taking it down must not — the
    device is about to reboot anyway."""
    monkeypatch.setattr(pt.shutil, "which", lambda n: f"/usr/bin/{n}")
    calls = []
    t = _transport(calls, tmp_path)
    t.expose(_info())
    t.run = _fake_run(calls, fail_on="connection down")
    with caplog.at_level("WARNING"):
        t.withdraw()
    assert "taking the setup AP down" in caplog.text


# ─── the wireless device can lag the service ──────────────────────────────


def test_it_waits_for_a_late_wifi_interface(monkeypatch, tmp_path):
    """Observed on hardware: the service beat the wireless device to
    readiness, failed, and systemd restarted it eight seconds later — eight
    seconds with no setup network."""
    monkeypatch.setattr(pt.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(pt, "_IFACE_POLL_SEC", 0.01)
    sleeps = []
    monkeypatch.setattr(pt.time, "sleep", lambda s: sleeps.append(s))

    attempts = {"n": 0}
    calls = []
    base = _fake_run(calls)

    def run(cmd, **kw):
        if "device status" in " ".join(cmd):
            attempts["n"] += 1
            if attempts["n"] < 3:          # not there yet
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="lo:loopback\n", stderr="")
        return base(cmd, **kw)

    t = _transport(calls, tmp_path)
    t.run = run
    t.expose(_info())
    try:
        assert attempts["n"] >= 3          # polled until it appeared
        assert sleeps                      # and actually waited between tries
    finally:
        t.withdraw()


def test_it_gives_up_eventually_with_a_clear_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(pt.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(pt, "_IFACE_WAIT_SEC", 0.05)
    monkeypatch.setattr(pt, "_IFACE_POLL_SEC", 0.01)
    monkeypatch.setattr(pt.time, "sleep", lambda s: None)
    t = pt.PortalTransport(
        ap_ssid="x", ap_psk="y", device_profile="xvf3800_usb",
        ip="127.0.0.1", bind_host="127.0.0.1", port=0,
        state_dir=tmp_path / "state",
        run=lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, stdout="lo:loopback\n", stderr=""),
    )
    with pytest.raises(RuntimeError, match="rfkill-blocked"):
        t.expose(_info())
