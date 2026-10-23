"""Every kind of home network the setup portal can be asked to join, and
what it says when it can't (2026-10-22).

Kamron reflashed his satellites (Pi Zero 2 W) and none would join
"Maple Wifi 2.0", a TP-Link in WPA2/WPA3 mixed mode
("WPA3-Personal+WPA2-PSK[AES]"). The portal said

    Couldn't join that network. Check the password and try again.
    (wifi join failed for 'Maple Wifi 2.0': Hint: use 'journalctl -xe
    NM_CONNECTION=<uuid> + NM_DEVICE=wlan0' to get more details.)

Two defects, both here:

1. The profile was key-mgmt=wpa-psk with PMF at NetworkManager's default.
   NM then offers wpa_supplicant `WPA-PSK WPA-PSK-SHA256 SAE` whenever the
   radio reports SAE/PMF/BIP, the supplicant picks SAE on a mixed router,
   and brcmfmac cannot finish SAE: "Secrets were required" with the right
   password. PMF off on the first wpa-psk attempt keeps SAE out.
2. The error kept nmcli's LAST line, which on a real nmcli is always the
   `Hint:`; the reason above it was lost, and because the hint matched no
   failure marker the sae fallback never ran either.

No wireless hardware here: :class:`Radio` models NetworkManager, the
router and the Pi's driver, including the driver's SAE defect.
"""

from __future__ import annotations

import subprocess

import pytest

from satellite import portal_pages
from satellite import provisioning_mode as pm
from satellite import provisioning_protocol as proto
from satellite import setup_report
from satellite import wifi_reasons as wr

PSK = "correct horse battery"
SSID = "Maple Wifi 2.0"
HINT = ("Hint: use 'journalctl -xe NM_CONNECTION=%s + NM_DEVICE=wlan0' "
        "to get more details.")
SECRETS = ("Error: Connection activation failed: Secrets were required, but "
           "not provided.")


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace(":", "\\:")


class Radio:
    """NetworkManager + one router + a Pi radio, as nmcli shows them.

    ``router`` is what the AP accepts: a set of ``wpa-psk`` / ``sae`` /
    ``open`` / ``owe``. ``pmf_required`` is a WPA2 router that insists on
    PMF. ``driver_sae`` says whether this radio can complete SAE (the Pi
    Zero 2 W cannot); ``reports_sae`` whether it CLAIMS it can, which is
    what makes NetworkManager add SAE to a wpa-psk profile whose PMF is not
    disabled.
    """

    def __init__(self, *, security="WPA2", router=("wpa-psk",), psk=PSK,
                 ssid=SSID, freq="2437 MHz", visible=True, hidden_ap=False,
                 pmf_required=False, driver_sae=False, reports_sae=True,
                 bands=("1",), up_error=None, journal=""):
        self.security, self.router, self.psk, self.ssid = security, set(router), psk, ssid
        self.freq, self.visible, self.hidden_ap = freq, visible, hidden_ap
        self.pmf_required, self.driver_sae, self.reports_sae = pmf_required, driver_sae, reports_sae
        self.bands, self.up_error, self.journal = bands, up_error, journal
        self.profiles: dict[str, dict[str, str]] = {}
        self.adds: list[dict[str, str]] = []
        self.argv: list[list[str]] = []
        self._n = 0

    # nmcli's last line on every activation failure, like the real one
    def _fail(self, cmd, msg, uuid="x"):
        return subprocess.CompletedProcess(
            cmd, 4, stdout=b"", stderr=(msg + "\n" + HINT % uuid + "\n").encode())

    @staticmethod
    def _ok(cmd, out="", text=False):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=out if text else out.encode(), stderr="" if text else b"")

    def __call__(self, cmd, **kw):
        cmd = list(cmd)
        text = bool(kw.get("text"))
        j = " ".join(cmd)
        if cmd[:2] == ["iw", "phy"]:
            out = "".join(f"\tBand {b}:\n\t\tCapabilities: 0x1\n" for b in self.bands)
            return self._ok(cmd, "Wiphy phy0\n" + out, text)
        if cmd[0] == "journalctl":
            return self._ok(cmd, self.journal, text)
        if "device wifi list" in j:
            rows = ["Neighbour:WPA2:2412 MHz"]
            if self.visible and not self.hidden_ap:
                rows.append(f"{_esc(self.ssid)}:{self.security}:{self.freq}")
            return self._ok(cmd, "\n".join(rows) + "\n", text)
        if "NAME,UUID" in cmd:
            return self._ok(cmd, "".join(f"{p['name']}:{u}\n" for u, p in self.profiles.items()), text)
        if "UUID,TYPE" in cmd:
            return self._ok(cmd, "".join(f"{u}:802-11-wireless\n" for u in self.profiles), text)
        if "802-11-wireless.ssid" in cmd and "show" in cmd:
            p = self.profiles.get(cmd[-1])
            return self._ok(cmd, f"802-11-wireless.ssid:{_esc(p['ssid'])}\n" if p else "", text)
        if cmd[1:3] == ["connection", "add"]:
            self.argv.append(cmd)
            args = dict(zip(cmd[3::2], cmd[4::2]))
            self.adds.append(args)
            self._n += 1
            uuid = f"{self._n:08d}-0000-4000-8000-000000000000"
            self.profiles[uuid] = {"name": args.get("con-name", ""), **args}
            return self._ok(cmd, f"Connection 'domovoi-home' ({uuid}) successfully added.\n")
        if cmd[1:3] == ["connection", "delete"]:
            self.profiles.pop(cmd[-1], None)
            return self._ok(cmd)
        if cmd[1:3] == ["connection", "up"]:
            return self._up(cmd, self.profiles.get(cmd[-1]), cmd[-1])
        return self._ok(cmd, "", text)

    def _offered(self, p) -> set[str]:
        """The AKMs NetworkManager hands wpa_supplicant for a profile
        (nm-supplicant-config.c, client mode)."""
        km, pmf = p.get("wifi-sec.key-mgmt"), p.get("wifi-sec.pmf", "default")
        if km is None:
            return {"open"}
        if km == "owe":
            return {"owe"}
        if km == "sae":
            return {"sae"}
        out = set()
        if pmf != "required":
            out.add("wpa-psk")
        if self.reports_sae and pmf != "disable":
            out.add("sae")
        return out

    def _up(self, cmd, p, uuid):
        if self.up_error:
            return self._fail(cmd, self.up_error, uuid)
        if p is None:
            return self._fail(cmd, "Error: unknown connection.", uuid)
        if self.hidden_ap and p.get("802-11-wireless.hidden") != "yes":
            return self._fail(cmd, "Error: Connection activation failed: The Wi-Fi "
                                   "network could not be found.", uuid)
        if self.ssid != p.get("ssid"):
            return self._fail(cmd, f"Error: No network with SSID '{p.get('ssid')}' found.", uuid)
        common = self._offered(p) & self.router
        if not common:
            return self._fail(cmd, SECRETS, uuid)
        # wpa_supplicant prefers SAE when both ends offer it.
        akm = "sae" if "sae" in common else sorted(common)[0]
        if akm == "sae" and not self.driver_sae:
            return self._fail(cmd, SECRETS, uuid)          # brcmfmac
        pmf = p.get("wifi-sec.pmf", "default")
        if akm == "wpa-psk" and self.pmf_required and pmf == "disable":
            return self._fail(cmd, SECRETS, uuid)
        if akm in ("wpa-psk", "sae") and p.get("wifi-sec.psk") != self.psk:
            return self._fail(cmd, SECRETS, uuid)
        return self._ok(cmd)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(pm.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    monkeypatch.setattr(pm, "_WIFI_SCAN_POLL_SEC", 0.0)
    monkeypatch.setattr(pm, "_WIFI_SCAN_WAIT_SEC", 0.0)


def _join(radio, *, ssid=SSID, psk=PSK, hidden=False):
    return pm.apply_wifi(ssid, psk, None, hidden, 30.0, run=radio)


def _plan(radio):
    return [(a.get("wifi-sec.key-mgmt"), a.get("wifi-sec.pmf")) for a in radio.adds]


# ─── today's failure ──────────────────────────────────────────────────────


def test_kambers_router_joins_on_the_first_attempt():
    """WPA2/WPA3 mixed, a Pi Zero 2 W that claims SAE and can't do it."""
    r = Radio(security="WPA2 WPA3", router=("wpa-psk", "sae"))
    ok, err = _join(r)
    assert ok is True, err
    assert _plan(r) == [("wpa-psk", "disable")]


def test_what_the_old_profile_did_on_that_router():
    """The profile the card built until today (PMF left at default) fails
    there with the right password - the model reproduces the bug."""
    r = Radio(security="WPA2 WPA3", router=("wpa-psk", "sae"))
    r.adds.append({})
    old = {"ssid": SSID, "wifi-sec.key-mgmt": "wpa-psk", "wifi-sec.psk": PSK}
    r.profiles["old"] = {"name": "domovoi-home", **old}
    res = r._up(["nmcli", "connection", "up", "uuid", "old"], r.profiles["old"], "old")
    assert res.returncode != 0 and b"Secrets were required" in res.stderr


def test_the_reason_is_the_error_line_not_the_hint():
    r = Radio(security="WPA2 WPA3", router=("wpa-psk", "sae"), psk="other-one!")
    ok, err = _join(r)
    assert ok is False
    assert "Hint" not in err and "journalctl" not in err
    assert "Secrets were required" in err
    assert wr.kind_of(err) == wr.WRONG_PASSWORD
    # ... and because the reason was read, the fallbacks ran.
    assert _plan(r) == [("wpa-psk", "disable"), ("wpa-psk", "optional"), ("sae", "required")]


def test_the_detail_helper_skips_the_hint():
    proc = subprocess.CompletedProcess([], 4, stdout=b"",
                                       stderr=(SECRETS + "\n" + HINT % "u").encode())
    assert pm._nmcli_detail(proc, PSK) == SECRETS


# ─── each kind of network: the nmcli commands, dry run ────────────────────


@pytest.mark.parametrize("security, router, kw, plan", [
    # WPA2-Personal (AES): PMF off, then PMF default for a router that
    # requires it.
    ("WPA2", ("wpa-psk",), {}, [("wpa-psk", "disable")]),
    ("WPA2", ("wpa-psk",), {"pmf_required": True},
     [("wpa-psk", "disable"), ("wpa-psk", "optional")]),
    # WPA2/WPA3 mixed with PMF required (a Wi-Fi 7 router), on a radio
    # that does not claim SAE: PMF off is refused, PMF optional joins.
    ("WPA2 WPA3", ("wpa-psk", "sae"), {"pmf_required": True, "reports_sae": False},
     [("wpa-psk", "disable"), ("wpa-psk", "optional")]),
    # WPA (TKIP) and WPA/WPA2 mixed: wpa-psk carries both.
    ("WPA1", ("wpa-psk",), {}, [("wpa-psk", "disable")]),
    ("WPA1 WPA2", ("wpa-psk",), {}, [("wpa-psk", "disable")]),
    # WPA2/WPA3 mixed (transition): the WPA2 side.
    ("WPA2 WPA3", ("wpa-psk", "sae"), {}, [("wpa-psk", "disable")]),
    # WPA3-only on a radio that can do SAE (Pi 4 / Pi 5 with SAE firmware).
    ("WPA3", ("sae",), {"driver_sae": True}, [("sae", "required")]),
    # Open, and Enhanced Open.
    ("", ("open",), {}, [(None, None)]),
    ("OWE", ("owe",), {}, [("owe", None)]),
])
def test_each_security_type_builds_the_profile_it_needs(security, router, kw, plan):
    r = Radio(security=security, router=router, **kw)
    ok, err = _join(r, psk="" if router in (("open",), ("owe",)) else PSK)
    assert ok is True, err
    assert _plan(r) == plan
    for argv in r.argv:
        assert argv[:5] == ["/usr/bin/nmcli", "connection", "add", "type", "wifi"]
        args = dict(zip(argv[3::2], argv[4::2]))
        assert args["ssid"] == SSID and args["con-name"] == "domovoi-home"
        assert args["ifname"] == "wlan0" and args["802-11-wireless.powersave"] == "2"
        if args.get("wifi-sec.key-mgmt") in ("wpa-psk", "sae"):
            assert args["wifi-sec.psk"] == PSK
        else:
            assert "wifi-sec.psk" not in args


def test_an_open_network_gets_no_security_block_even_if_a_password_was_typed():
    r = Radio(security="", router=("open",))
    ok, _ = _join(r, psk=PSK)
    assert ok is True
    assert not any(k.startswith("wifi-sec") for k in r.adds[0])


def test_wpa3_only_on_a_pi_zero_says_so_plainly():
    r = Radio(security="WPA3", router=("sae",), driver_sae=False)
    ok, err = _join(r)
    assert ok is False
    assert _plan(r) == [("sae", "required")]            # tried once
    assert wr.kind_of(err) == wr.WPA3_ONLY
    page = wr.PORTAL[wr.WPA3_ONLY]
    assert "WPA2/WPA3 mixed" in page and "Zero 2 W" in page


def test_wpa3_only_with_a_64_hex_key_is_refused_in_words():
    r = Radio(security="WPA3", router=("sae",))
    ok, err = _join(r, psk="ab" * 32)
    assert ok is False and r.adds == []
    assert wr.kind_of(err) == wr.WPA3_RAW_KEY


def test_a_64_hex_key_on_a_mixed_router_skips_sae():
    r = Radio(security="WPA2 WPA3", router=("wpa-psk", "sae"), psk="cd" * 32)
    ok, err = _join(r, psk="cd" * 32)
    assert ok is True, err
    assert _plan(r) == [("wpa-psk", "disable")]


@pytest.mark.parametrize("security", ["WPA2 802.1X", "WPA1 WPA2 802.1X", "WPA3 802.1X"])
def test_enterprise_is_not_attempted(security):
    r = Radio(security=security, router=())
    ok, err = _join(r)
    assert ok is False and r.adds == []
    assert wr.kind_of(err) == wr.ENTERPRISE
    assert "username" in wr.PORTAL[wr.ENTERPRISE]


def test_wep_is_not_attempted():
    r = Radio(security="WEP", router=())
    ok, err = _join(r)
    assert ok is False and r.adds == []
    assert wr.kind_of(err) == wr.WEP


def test_a_blank_password_for_a_secured_network_is_said_before_trying():
    r = Radio(security="WPA2")
    ok, err = _join(r, psk="")
    assert ok is False and r.adds == []
    assert wr.kind_of(err) == wr.NEEDS_PASSWORD


# ─── hidden, out of range, 5 GHz ─────────────────────────────────────────


def test_a_network_not_in_the_scan_is_probed_for_by_name():
    """The portal can't know a typed name is hidden; the join probes."""
    r = Radio(hidden_ap=True, router=("wpa-psk",))
    ok, err = _join(r)
    assert ok is True, err
    assert r.adds[0]["802-11-wireless.hidden"] == "yes"


def test_a_network_this_radio_cannot_hear_names_the_2_4_ghz_limit():
    """A Pi Zero 2 W (no 5 GHz band) and a network on 5 GHz only."""
    r = Radio(visible=False, up_error="Error: Connection activation failed: "
                                      "The Wi-Fi network could not be found.")
    ok, err = _join(r)
    assert ok is False
    assert wr.kind_of(err) == wr.NOT_FOUND_24
    assert "2.4 GHz" in wr.PORTAL[wr.NOT_FOUND_24]
    assert len(r.adds) == 1                               # not retried


def test_a_dual_band_radio_is_not_told_about_2_4_ghz():
    r = Radio(visible=False, bands=("1", "2"),
              up_error="Error: No network with SSID 'Maple Wifi 2.0' found.")
    ok, err = _join(r)
    assert wr.kind_of(err) == wr.NOT_FOUND
    # The network's name is out of the detail (it goes on the card).
    assert SSID not in wr.detail_of(err)


# ─── names and passwords, byte for byte ──────────────────────────────────


@pytest.mark.parametrize("ssid", [
    "Maple Wifi 2.0", " leading and trailing ", "café ☕ 5G", "a:b\\c",
    "x" * 32, "Guest's Wi-Fi", "$(reboot) `id` ; &&",
])
@pytest.mark.parametrize("psk", [
    "pass word with spaces", "quote\"s and 'single' and \\back", "x" * 63,
    "$HOME;rm -rf /", "0123456789abcdef" * 4,
])
def test_names_and_passwords_reach_nmcli_exactly(ssid, psk):
    r = Radio(ssid=ssid, psk=psk, security="WPA2", router=("wpa-psk",))
    ok, err = _join(r, ssid=ssid, psk=psk)
    assert ok is True, err
    argv = r.argv[0]
    assert argv[argv.index("ssid") + 1] == ssid
    assert argv[argv.index("wifi-sec.psk") + 1] == psk
    assert psk not in (err or "")


def test_a_name_wpa_cannot_carry_is_refused_not_mangled():
    for bad in ("x" * 33, "tab\there", 'quote"d', "{brace}"):
        with pytest.raises(proto.ProvisionInvalid):
            proto.validate_wifi_ssid(bad)


@pytest.mark.parametrize("psk, ok", [
    ("", True), ("1234567", False), ("12345678", True), ("x" * 63, True),
    ("x" * 64, False), ("ab" * 32, True), ("café-pass", False),
])
def test_the_portal_checks_the_password_shape(psk, ok):
    if ok:
        assert proto.validate_wifi_psk(psk, allow_open=True) == psk
    else:
        with pytest.raises(proto.ProvisionInvalid):
            proto.validate_wifi_psk(psk, allow_open=True)


def test_the_fallback_writes_an_open_network_without_a_key():
    block = proto.wpa_supplicant_network_block("Cafe", "")
    assert "\tkey_mgmt=NONE\n" in block and "psk=" not in block


# ─── what people read ────────────────────────────────────────────────────

# (Enterprise names a password only to say a username is needed too.)
NOT_THE_PASSWORD = (wr.NOT_FOUND, wr.NOT_FOUND_24, wr.TIMEOUT, wr.NO_ADDRESS,
                    wr.WEP, wr.RADIO, wr.OTHER)


@pytest.mark.parametrize("kind", NOT_THE_PASSWORD)
def test_only_a_password_problem_mentions_the_password(kind):
    assert "password" not in wr.PORTAL[kind].lower()
    assert "password" not in wr.CARD[kind].lower()


@pytest.mark.parametrize("text, kind", [
    (SECRETS + "\n" + HINT, wr.WRONG_PASSWORD),
    ("Error: Connection activation failed: (7) Secrets were required", wr.WRONG_PASSWORD),
    ("Error: Connection activation failed: 802.1X supplicant disconnected", wr.WRONG_PASSWORD),
    ("Error: Connection activation failed: 802.1X supplicant took too long to authenticate",
     wr.AUTH_TIMEOUT),
    ("Error: No network with SSID 'X' found.", wr.NOT_FOUND),
    ("Error: Connection activation failed: The Wi-Fi network could not be found", wr.NOT_FOUND),
    ("Error: Connection activation failed: IP configuration could not be reserved "
     "(no available address, timeout, etc.)", wr.NO_ADDRESS),
    ("Error: Timeout 90 sec expired.", wr.TIMEOUT),
    ("Error: Failed to add 'wifi' connection: no such device.", wr.RADIO),
    ("Error: something new", wr.OTHER),
])
def test_nmcli_failures_are_named(text, kind):
    assert wr.classify(text) == kind


def test_the_portal_and_the_card_say_the_same_thing_in_their_own_words():
    err = wr.tagged("wifi join failed for 'Maple Wifi 2.0'", wr.NOT_FOUND_24,
                    "Error: No network with SSID '<network>' found.")
    card = setup_report.card_error("wifi_failed", err)
    assert card.startswith(wr.CARD[wr.NOT_FOUND_24])
    assert "No network with SSID" in card and "Maple" not in card


def test_a_bracket_in_the_network_name_does_not_fool_the_tag():
    err = wr.tagged("wifi join failed for '[timeout] net'", wr.WRONG_PASSWORD, "Error: x")
    assert wr.kind_of(err) == wr.WRONG_PASSWORD
    assert wr.detail_of(err) == "Error: x"


def test_the_journal_lines_are_scrubbed():
    journal = (
        "device (wlan0): supplicant interface state: associating -> 4way_handshake\n"
        f"wlan0: CTRL-EVENT-SSID-TEMP-DISABLED id=0 ssid=\"{SSID}\" auth_failures=1 "
        "duration=10 reason=WRONG_KEY\n"
        "wlan0: Trying to associate with SSID 'Maple Wifi 2.0' bssid=aa:bb:cc:dd:ee:ff\n"
        "unrelated line\n"
    )
    r = Radio(security="WPA2", router=("wpa-psk",), psk="nope-nope", journal=journal)
    ok, _ = _join(r)
    assert ok is False
    log = " ".join(pm.LAST_JOIN_LOG)
    assert "reason=WRONG_KEY" in log
    assert SSID not in log and "aa:bb:cc" not in log and "unrelated" not in log
    assert PSK not in log


# ─── the setup page ──────────────────────────────────────────────────────


def test_the_form_offers_a_typed_name_and_an_optional_password():
    page = portal_pages.render_form(networks=["A", "B"], profiles=["xvf3800"])
    assert 'name="ssid_custom"' in page
    psk_input = page[page.index('id="psk"'):]
    assert "required" not in psk_input[:psk_input.index(">")]
