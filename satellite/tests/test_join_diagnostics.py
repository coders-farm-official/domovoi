"""The card says what each join attempt did, and the form remembers what
was chosen (2026-10-22, the second hardware report).

A card with the WPA2/WPA3 fix still read "the password wasn't accepted
(Secrets were required)", and its ``join_log`` was the last eight journal
lines: NetworkManager giving up, then the regulatory domain re-applied
after the disconnect. Nothing said which access point was tried, with what,
or what it answered - and two access points broadcast that network's name.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from satellite import join_diag
from satellite import portal_pages
from satellite import provisioning_mode as pm
from satellite.tests.test_portal_transport import _get, _post, portal  # noqa: F401
from satellite.tests.test_wifi_security_matrix import PSK, SECRETS, HINT, SSID, Radio

MAIN = "50:91:e3:12:3c:ef"         # the TP-Link
SECOND = "a8:42:a1:77:01:9b"       # the second access point
SELF = "b8:27:eb:aa:bb:cc"         # the Pi's own radio


def _ok(cmd, out=""):
    return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


def _esc(mac):
    return mac.replace(":", "\\:")


SCAN = "\n".join([
    f"{_esc(MAIN)}:{SSID}:WPA2 WPA3:6:2437 MHz:41",
    f"{_esc(SECOND)}:{SSID}:WPA2:1:2412 MHz:78",
    f"{_esc('11:22:33:44:55:66')}:SmartLife-0000::11:2462 MHz:90",
]) + "\n"

JOURNAL_WRONG_KEY = "\n".join([
    f"wlan0: SME: Trying to authenticate with {SECOND} (SSID='{SSID}' freq=2412 MHz)",
    f"wlan0: Trying to associate with {SECOND} (SSID='{SSID}' freq=2412 MHz)",
    f"wlan0: Associated with {SECOND}",
    "<info> device (wlan0): supplicant interface state: associating -> 4way_handshake",
    "wlan0: WPA: 4-Way Handshake failed - pre-shared key may be incorrect",
    f"wlan0: CTRL-EVENT-SSID-TEMP-DISABLED id=0 ssid=\"{SSID}\" auth_failures=1 duration=10 reason=WRONG_KEY",
    f"wlan0: CTRL-EVENT-DISCONNECTED bssid={SECOND} reason=15",
    f"<info> device (wlan0): Activation: (wifi) disconnected during association, asking for new key",
    "<info> dhcp4 (wlan0): something irrelevant",
    f"wlan0: CTRL-EVENT-REGDOM-CHANGE init=USER type=COUNTRY alpha2=US",
    f"<info> device (wlan0): own address {SELF} psk={PSK}",
])

JOURNAL_PMF_REJECT = "\n".join([
    f"wlan0: Trying to associate with {MAIN} (SSID='{SSID}' freq=2437 MHz)",
    f"wlan0: CTRL-EVENT-ASSOC-REJECT bssid={MAIN} status_code=31",
])


def _diag_with_scan():
    d = join_diag.Diag()
    d.take_scan(lambda cmd, **kw: _ok(cmd, SCAN), "nmcli", SSID)
    return d


# ─── the scan: every access point on the name ────────────────────────────


def test_two_access_points_on_one_name_are_told_apart():
    d = _diag_with_scan()
    assert [r["ap"] for r in d.scan] == ["AP-1 (..:01:9b)", "AP-2 (..:3c:ef)"]   # strongest first
    assert [r["security"] for r in d.scan] == ["WPA2", "WPA2 WPA3"]
    assert [r["freq_mhz"] for r in d.scan] == [2412, 2437]
    assert [r["channel"] for r in d.scan] == [1, 6]
    assert [r["signal"] for r in d.scan] == [78, 41]
    raw = json.dumps(d.card())
    assert MAIN not in raw and SECOND not in raw and SSID not in raw
    assert "SmartLife" not in raw                       # other networks stay off


# ─── one attempt ─────────────────────────────────────────────────────────


def _record(d, journal, *, ok=False, kind="wrong-password", km="wpa-psk", pmf="disable"):
    return d.record(lambda cmd, **kw: _ok(cmd, journal), since=1_700_000_000.0,
                    key_mgmt=km, pmf=pmf, ok=ok, kind=kind,
                    detail="Error: Connection activation failed: Secrets were required, but not provided.",
                    ssid=SSID, psk=PSK)


def test_an_attempt_says_which_access_point_refused_the_key():
    d = _diag_with_scan()
    e = _record(d, JOURNAL_WRONG_KEY)
    assert e["key_mgmt"] == "wpa-psk" and e["pmf"] == "disable"
    assert e["ap"] == "AP-1 (..:01:9b)" and e["freq_mhz"] == 2412
    assert e["outcome"] == "wrong-password"
    assert e["reason_codes"] == ["15: 4-way handshake timeout (the access point's key did not match)"]
    assert "the access point rejected the key in the 4-way handshake" in e["hints"]
    log = "\n".join(e["log"])
    assert "4-Way Handshake failed" in log and "WRONG_KEY" in log and "REGDOM-CHANGE" in log
    assert "dhcp4" not in log
    assert PSK not in log and SSID not in log
    assert SECOND not in log and SELF not in log and "AP-1 (..:01:9b)" in log


def test_an_access_point_that_requires_pmf_is_named():
    d = _diag_with_scan()
    e = _record(d, JOURNAL_PMF_REJECT, kind="other")
    assert e["ap"] == "AP-2 (..:3c:ef)"
    assert e["status_codes"] == ["31: refused: the access point requires Protected Management Frames (PMF)"]
    assert "the access point refused the association" in e["hints"]


def test_an_access_point_missing_from_the_scan_still_gets_a_label():
    d = join_diag.Diag()
    e = _record(d, JOURNAL_WRONG_KEY)
    assert e["ap"] == "AP-1 (..:01:9b)"


def test_the_journal_is_read_from_the_attempts_start():
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return _ok(cmd, "")

    join_diag.Diag().record(run, since=1_700_000_123.9, key_mgmt="sae", pmf="required",
                            ok=False, kind="wrong-password", detail="", ssid=SSID, psk=PSK)
    assert seen["cmd"][seen["cmd"].index("--since") + 1] == "@1700000122"


# ─── through the join: every attempt, in order ───────────────────────────


class DiagRadio(Radio):
    """The matrix model, with the BSSID scan and a journal per attempt."""

    def __init__(self, journal, **kw):
        super().__init__(**kw)
        self.journal_text = journal

    def __call__(self, cmd, **kw):
        cmd = list(cmd)
        if "BSSID,SSID,SECURITY,CHAN,FREQ,SIGNAL" in cmd:
            return _ok(cmd, SCAN)
        if cmd[0] == "journalctl" and "--since" in cmd and cmd[cmd.index("--since") + 1].startswith("@"):
            return _ok(cmd, self.journal_text)
        return super().__call__(cmd, **kw)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(pm.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    monkeypatch.setattr(pm, "_WIFI_SCAN_POLL_SEC", 0.0)
    monkeypatch.setattr(pm, "_WIFI_SCAN_WAIT_SEC", 0.0)
    pm.JOIN_DIAG.reset()


def test_the_card_shows_both_attempts_of_a_mixed_network_in_order():
    """What Kamron's card could not show: wpa-psk with PMF off was tried
    FIRST (and that is the error the page showed), then sae."""
    r = DiagRadio(JOURNAL_WRONG_KEY, security="WPA2 WPA3", router=("wpa-psk", "sae"),
                  psk="the-other-ap-key")
    ok, err = pm.apply_wifi(SSID, PSK, None, False, 30.0, run=r)
    assert ok is False
    card = pm.JOIN_DIAG.card()
    assert [(a["key_mgmt"], a["pmf"]) for a in card["join_attempts"]] == [
        ("wpa-psk", "disable"), ("wpa-psk", "optional"), ("sae", "required")]
    assert all(a["outcome"] == "wrong-password" for a in card["join_attempts"])
    assert len(card["join_scan"]) == 2
    assert PSK not in json.dumps(card) and SSID not in json.dumps(card)


def test_a_join_that_works_is_recorded_too():
    r = DiagRadio("", security="WPA2", router=("wpa-psk",))
    ok, _ = pm.apply_wifi(SSID, PSK, None, False, 30.0, run=r)
    assert ok is True
    assert pm.JOIN_DIAG.card()["join_attempts"][-1]["outcome"] == "joined"


# ─── the form keeps what was chosen ──────────────────────────────────────


def test_the_network_list_starts_at_choose_your_network():
    page = portal_pages.render_form(networks=["SmartLife-0000", SSID], profiles=["xvf3800_usb"])
    sel = page[page.index('<select id="ssid"'):page.index("</select>")]
    assert sel.index("Choose your network") < sel.index("SmartLife-0000")
    assert '<option value="" selected>Choose your network' in sel
    assert ' selected>SmartLife' not in sel


def test_the_list_is_strongest_first():
    rows = [{"ssid": "Weak", "signal": "20"}, {"ssid": SSID, "signal": "70"},
            {"ssid": "Mid", "signal": "50"}, {"ssid": SSID, "signal": "30"}]
    assert portal_pages.summarize_networks(rows) == [SSID, "Mid", "Weak"]


def test_the_last_choice_is_preselected():
    page = portal_pages.render_form(networks=["SmartLife-0000", SSID], profiles=["xvf3800_usb"],
                                    ssid=SSID, room="office", url="ws://192.168.1.20:6370")
    assert f'<option value="{SSID}" selected>' in page
    assert '<option value="" selected>Choose' not in page
    assert '<option value="office" selected>' in page
    assert 'value="ws://192.168.1.20:6370"' in page


def test_a_typed_network_and_room_come_back_typed():
    page = portal_pages.render_form(networks=["A"], profiles=["x"], ssid="Hidden Net",
                                    room=None, room_custom="Back Porch")
    assert 'name="ssid_custom"' in page and 'value="Hidden Net"' in page
    assert 'value="Back Porch"' in page
    assert '<option value="" selected>Something else' in page


def test_after_a_failed_join_the_form_comes_back_filled_in(portal):  # noqa: F811
    portal.networks = ["SmartLife-0000", SSID]
    fields = {"ssid": SSID, "psk": "a-wrong-password", "room": "office",
              "url": "ws://192.168.1.20:6370"}
    status, _ = _post(portal, "/provision", fields)
    assert status == 200
    # The join failed; the state machine re-exposes the same transport.
    _s, body, _h = _get(portal, "/")
    assert f'<option value="{SSID}" selected>' in body
    assert '<option value="office" selected>' in body
    assert "a-wrong-password" not in body


def test_a_refused_form_comes_back_filled_in(portal):  # noqa: F811
    portal.networks = ["SmartLife-0000", SSID]
    status, body = _post(portal, "/provision", {"ssid": SSID, "psk": "short",
                                                "room": "", "room_custom": "Back Porch"})
    assert status == 400
    assert f'<option value="{SSID}" selected>' in body and 'value="Back Porch"' in body
    assert 'value="short"' not in body


def test_choosing_nothing_is_said(portal):  # noqa: F811
    portal.networks = ["SmartLife-0000", SSID]
    status, body = _post(portal, "/provision", {"ssid": "", "psk": PSK, "room": "office"})
    assert status == 400 and "Choose your Wi-Fi network" in body


# ─── the password, byte for byte, from the phone to nmcli ────────────────

# Shaped like Kamron's: about 33 characters, spaces (one leading, one
# trailing, a double), commas, full stops, digits.
SPACEY = " Our house, 42 Elm St.  2nd floor. "


def test_a_password_with_spaces_and_punctuation_reaches_nmcli_exactly(portal):  # noqa: F811
    portal.networks = [SSID]
    status, _ = _post(portal, "/provision", {"ssid": SSID, "psk": SPACEY, "room": "office"})
    assert status == 200
    payload = portal._queue.get(timeout=5)
    assert payload["wifi"]["psk"] == SPACEY                  # form -> payload, untouched
    from satellite import provisioning_protocol as proto
    doc = proto.build_provision(nonce="n" * 32, room_id="office", domovoi_url="",
                                sat_type="voice", device_profile="xvf3800_usb",
                                pairing_token="t", wifi_ssid=SSID, wifi_psk=SPACEY)
    assert json.loads(json.dumps(doc))["payload"]["wifi"]["psk"] == SPACEY   # JSON round trip
    r = DiagRadio("", security="WPA2 WPA3", router=("wpa-psk", "sae"), psk=SPACEY,
                  reports_sae=False)
    ok, err = pm.apply_wifi(SSID, SPACEY, None, False, 30.0, run=r)
    assert ok is True, err
    argv = r.argv[0]
    assert argv[argv.index("wifi-sec.psk") + 1] == SPACEY    # argv, no shell, no strip


# ─── the diagnostic pin ──────────────────────────────────────────────────


def test_a_pinned_join_names_the_access_point_then_lets_it_roam():
    calls = []

    class Pinned(DiagRadio):
        def __call__(self, cmd, **kw):
            calls.append(list(cmd))
            return super().__call__(cmd, **kw)

    pm.set_join_pin("A8:42:A1:77:01:9B", "bg")
    try:
        r = Pinned("", security="WPA2", router=("wpa-psk",))
        ok, _ = pm.apply_wifi(SSID, PSK, None, False, 30.0, run=r)
        assert ok is True
        add = dict(zip(r.argv[0][3::2], r.argv[0][4::2]))
        assert add["802-11-wireless.bssid"] == SECOND and add["802-11-wireless.band"] == "bg"
        clear = [c for c in calls if c[1:3] == ["connection", "modify"]]
        assert clear and clear[0][-4:] == ["802-11-wireless.bssid", "", "802-11-wireless.band", ""]
    finally:
        pm.set_join_pin(None, None)


def test_a_bad_pin_is_ignored():
    pm.set_join_pin("not-a-mac", "6ghz")
    assert pm.JOIN_PIN == {}
