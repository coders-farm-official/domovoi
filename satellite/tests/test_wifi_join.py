"""Joining the customer's Wi-Fi after the setup portal comes down.

Two hardware failures live in this file, a year apart, with one cause.

The first: the join ran the instant the setup AP was torn down, while
NetworkManager's scan cache was empty - it had been hosting, not scanning -
and `nmcli device wifi connect` refuses an SSID it cannot see. The three
retries were back-to-back with no rescan, so all failed identically, and the
failure was reported as "wrong password?" with nmcli's real reason
discarded. The answer was to rescan first, and to keep nmcli's words.

The second, on Kamron's third satellite: rescanning is not reliable either,
because the radio is STILL hosting the setup AP while the portal renders the
result. After 12 s of fruitless scanning the join went ahead blind - and
`connect ... password ...` asks NM to INFER the security type from a scan
that never happened, so it wrote a profile with a PSK and no key-mgmt:

    802-11-wireless-security.key-mgmt: property is missing.

reported to a customer whose password was right. The answer is this file's
subject: build the profile explicitly, name the key-mgmt, and stop caring
whether the scan worked. What the scan is still good for is telling WPA2
from WPA3-only, which saves an attempt but is no longer load-bearing.
"""

from __future__ import annotations

import subprocess

import pytest

from satellite import provisioning_mode as pm

PSK = "hunter2hunter2"
SSID = "Kamber Wifi 2.0"

SECRETS = (
    "Error: Connection activation failed: (7) Secrets were required, "
    "but not provided."
)
NOT_FOUND = "Error: No network with SSID '%s' found." % SSID


def _esc(value: str) -> str:
    """What `nmcli -t` does to a colon inside a field."""
    return value.replace("\\", "\\\\").replace(":", "\\:")


class FakeNM:
    """A NetworkManager with a scan cache, a profile store and one AP.

    Models the whole surface apply_wifi drives - `device wifi list`,
    `connection show`, `connection add`, `connection delete`,
    `connection up` - so a test can ask what profile was built and what the
    device would have done with it. There is no wireless hardware on this
    box and there never will be in CI.

    The AP answers on its own terms: `accepts` is the set of key-mgmt values
    it will associate with, and it does not care in the slightest what was
    in the scan cache when the profile was made. That last part is the
    whole point - the old code did.
    """

    def __init__(
        self,
        ssid: str = SSID,
        *,
        security: str = "WPA2",
        visible_after_scans: int = 2,
        accepts: tuple[str, ...] = ("wpa-psk",),
        psk: str = PSK,
        password_ok: bool = True,
        hidden_ap: bool = False,
        up_error: str | None = None,
        add_error: str | None = None,
        echo_psk_in_errors: bool = False,
        timeout_on: str | None = None,
        stale: tuple[tuple[str, str], ...] = (),
    ):
        self.ssid = ssid
        self.security = security
        self.visible_after = visible_after_scans
        self.accepts = set(accepts)
        self.psk = psk
        self.password_ok = password_ok
        self.hidden_ap = hidden_ap
        self.up_error = up_error
        self.add_error = add_error
        self.echo = echo_psk_in_errors
        self.timeout_on = timeout_on
        self.scans = 0
        self.connected: str | None = None
        self.calls: list[list[str]] = []
        self.adds: list[dict[str, object]] = []
        self.deleted: list[str] = []
        self.ups: list[list[str]] = []
        self.profiles: dict[str, dict[str, object]] = {}
        self._seq = 0
        for name, key_mgmt in stale:
            # A profile left by an earlier attempt: right ssid, whatever
            # key-mgmt that attempt managed (often none at all), and a
            # passphrase nobody can vouch for.
            self._store(name, ssid, key_mgmt, hidden=False, psk="stale")

    # ─── store ───────────────────────────────────────────────────────────

    def _store(self, name, ssid, key_mgmt, *, hidden, psk) -> str:
        self._seq += 1
        uuid = f"uuid-{self._seq}"
        self.profiles[uuid] = {
            "name": name, "ssid": ssid, "key_mgmt": key_mgmt,
            "hidden": hidden, "psk": psk,
        }
        return uuid

    @property
    def profiles_for_ssid(self) -> list[dict[str, object]]:
        return [p for p in self.profiles.values() if p["ssid"] == self.ssid]

    # ─── results ─────────────────────────────────────────────────────────

    @staticmethod
    def _ok(cmd, out: str = "", *, text: bool = False):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=out if text else out.encode(),
            stderr="" if text else b"",
        )

    def _fail(self, cmd, rc: int, msg: str, *, text: bool = False):
        if self.echo:
            msg = f"{msg} (psk={self.psk})"
        return subprocess.CompletedProcess(
            cmd, rc, stdout="" if text else b"",
            stderr=msg if text else msg.encode(),
        )

    # ─── the run() callable apply_wifi is given ──────────────────────────

    def __call__(self, cmd, **kw):
        cmd = list(cmd)
        self.calls.append(cmd)
        joined = " ".join(cmd)
        text = bool(kw.get("text"))
        if self.timeout_on and self.timeout_on in joined:
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout") or 30)

        if "device wifi list" in joined:
            self.scans += 1
            rows = ["Neighbour:WPA2"]
            if self.scans >= self.visible_after and not self.hidden_ap:
                rows.append(f"{_esc(self.ssid)}:{self.security}")
            return self._ok(cmd, "\n".join(rows) + "\n", text=text)

        if "UUID,TYPE" in cmd:
            rows = [f"{u}:802-11-wireless" for u in self.profiles]
            return self._ok(cmd, "".join(r + "\n" for r in rows), text=text)

        if "802-11-wireless.ssid" in cmd and "show" in cmd:
            prof = self.profiles.get(cmd[-1])
            if prof is None:
                return self._fail(cmd, 10, "Error: unknown connection.", text=text)
            return self._ok(
                cmd, f"802-11-wireless.ssid:{_esc(str(prof['ssid']))}\n", text=text
            )

        if cmd[1:3] == ["connection", "add"]:
            if self.add_error is not None:
                return self._fail(cmd, 2, self.add_error, text=text)
            args = dict(zip(cmd[3::2], cmd[4::2]))
            self.adds.append(args)
            uuid = self._store(
                args.get("con-name", ""), args.get("ssid", ""),
                args.get("wifi-sec.key-mgmt", ""),
                hidden=args.get("802-11-wireless.hidden") == "yes",
                psk=args.get("wifi-sec.psk", ""),
            )
            return self._ok(
                cmd, f"Connection '{args.get('con-name')}' ({uuid}) added.\n",
                text=text,
            )

        if cmd[1:3] == ["connection", "delete"]:
            self.deleted.append(cmd[-1])
            self.profiles.pop(cmd[-1], None)
            return self._ok(cmd, text=text)

        if cmd[1:3] == ["connection", "up"]:
            self.ups.append(cmd[3:])
            return self._up(cmd, text=text)

        return self._ok(cmd, text=text)

    def _up(self, cmd, *, text: bool):
        if cmd[3] == "uuid":
            prof = self.profiles.get(cmd[4])
        else:
            prof = next(
                (p for p in self.profiles.values() if p["name"] == cmd[4]), None
            )
        if self.up_error is not None:
            return self._fail(cmd, 4, self.up_error, text=text)
        if prof is None:
            return self._fail(cmd, 10, "Error: unknown connection.", text=text)
        if not prof["key_mgmt"]:
            # Exactly what the hardware said, and the reason this file
            # exists. No profile we build can produce it any more.
            return self._fail(
                cmd, 1,
                "Error: 802-11-wireless-security.key-mgmt: property is missing.",
                text=text,
            )
        if prof["key_mgmt"] not in self.accepts:
            return self._fail(cmd, 4, SECRETS, text=text)
        if not self.password_ok or prof["psk"] != self.psk:
            return self._fail(cmd, 4, SECRETS, text=text)
        if self.hidden_ap and not prof["hidden"]:
            return self._fail(
                cmd, 4,
                "Error: Connection activation failed: (53) The Wi-Fi network "
                "could not be found.",
                text=text,
            )
        self.connected = str(prof["name"])
        return self._ok(cmd, text=text)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(pm.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    monkeypatch.setattr(pm, "_WIFI_SCAN_POLL_SEC", 0.0)


@pytest.fixture
def blind(monkeypatch):
    """No waiting around for a scan that is not going to happen."""
    monkeypatch.setattr(pm, "_WIFI_SCAN_WAIT_SEC", 0.0)


def _join(nm, *, ssid=SSID, psk=PSK, hidden=False, country=None):
    return pm.apply_wifi(ssid, psk, country, hidden, 30.0, run=nm)


def _km(add: dict) -> str:
    return str(add.get("wifi-sec.key-mgmt", ""))


# ─── the bug: a blind join builds a valid profile ─────────────────────────


def test_a_blind_join_names_the_security_type_and_works(blind):
    """Kamron's case, exactly: the SSID is not in the scan cache, because
    wlan0 is still hosting the setup AP. The old code joined anyway and let
    NM infer - there was nothing to infer from, so the profile had a PSK and
    no key-mgmt and activation failed on a correct password."""
    nm = FakeNM(visible_after_scans=999)          # never visible
    ok, err = _join(nm)
    assert ok is True and err is None
    assert len(nm.adds) == 1
    assert _km(nm.adds[0]) == "wpa-psk"
    assert nm.connected == SSID
    # And the inference form is gone for good.
    assert not any(c[1:4] == ["device", "wifi", "connect"] for c in nm.calls)


def test_the_error_the_hardware_showed_can_no_longer_be_produced(blind):
    """`key-mgmt: property is missing` is what the fake returns for a
    profile with no key-mgmt. Every profile this code builds has one, on
    every path, so the message is unreachable."""
    for hidden in (False, True):
        for security in (None, "", "WPA2", "WPA2 WPA3", "WPA3"):
            nm = FakeNM(
                visible_after_scans=999, security=security or "",
                accepts=("wpa-psk", "sae"), hidden_ap=hidden,
            )
            ok, err = _join(nm, hidden=hidden)
            assert ok is True, (hidden, security, err)
            assert all(_km(a) for a in nm.adds)
            assert "key-mgmt" not in (err or "")


def test_the_scan_still_runs_first_when_it_can():
    """The scan is not load-bearing any more, but it is not pointless: it
    is where the security type comes from. It must still happen before the
    profile is built, not after."""
    nm = FakeNM(visible_after_scans=2)
    ok, _ = _join(nm)
    assert ok and nm.scans >= 2
    first_add = next(i for i, c in enumerate(nm.calls) if c[1:3] == ["connection", "add"])
    scans_before = sum(1 for c in nm.calls[:first_add] if "device wifi list" in " ".join(c))
    assert scans_before == nm.scans


def test_a_scanned_wpa2_network_is_joined_as_wpa_psk():
    nm = FakeNM(security="WPA2", visible_after_scans=1, accepts=("wpa-psk",))
    ok, err = _join(nm)
    assert ok is True and err is None
    assert [_km(a) for a in nm.adds] == ["wpa-psk"]


def test_a_mixed_wpa2_wpa3_network_is_joined_as_wpa_psk():
    """`wpa-psk` is what joins a mixed-mode AP; `sae` would be a second
    choice for no reason."""
    nm = FakeNM(security="WPA2 WPA3", visible_after_scans=1, accepts=("wpa-psk",))
    ok, err = _join(nm)
    assert ok is True and err is None
    assert [_km(a) for a in nm.adds] == ["wpa-psk"]


def test_a_scanned_wpa3_only_network_is_joined_as_sae():
    nm = FakeNM(security="WPA3", visible_after_scans=1, accepts=("sae",))
    ok, err = _join(nm)
    assert ok is True and err is None
    assert [_km(a) for a in nm.adds] == ["sae"]


def test_a_scan_that_says_sae_in_words_is_understood():
    nm = FakeNM(security="SAE", visible_after_scans=1, accepts=("sae",))
    ok, _ = _join(nm)
    assert ok and [_km(a) for a in nm.adds] == ["sae"]


def test_a_wpa3_only_network_joined_blind_falls_back_to_sae(blind):
    """No scan, so wpa-psk is tried first - and when the AP will only do
    SAE, the fallback happens here rather than on the customer's screen."""
    nm = FakeNM(visible_after_scans=999, accepts=("sae",))
    ok, err = _join(nm)
    assert ok is True and err is None
    assert [_km(a) for a in nm.adds] == ["wpa-psk", "sae"]
    # One profile left standing, the one that works.
    assert len(nm.profiles_for_ssid) == 1
    assert nm.profiles_for_ssid[0]["key_mgmt"] == "sae"


def test_a_hidden_wpa3_network_falls_back_to_sae_too():
    """The hidden path had the same inference hole and gets the same
    treatment, fallback included."""
    nm = FakeNM(hidden_ap=True, accepts=("sae",))
    ok, err = _join(nm, hidden=True)
    assert ok is True and err is None
    assert [_km(a) for a in nm.adds] == ["wpa-psk", "sae"]
    assert all(a.get("802-11-wireless.hidden") == "yes" for a in nm.adds)


def test_a_failure_that_is_not_about_security_is_not_retried_as_sae(blind):
    """The fallback is for an AP that would not associate. A timeout is not
    that, and spending the whole join timeout twice over on it would just
    make the portal slower to tell the truth."""
    nm = FakeNM(visible_after_scans=999, up_error="Error: Timeout 90 sec expired.")
    ok, err = _join(nm)
    assert ok is False
    assert [_km(a) for a in nm.adds] == ["wpa-psk"]
    assert "Timeout 90 sec expired" in err


def test_a_hidden_network_is_not_waited_for():
    """It never appears in a scan, by definition."""
    nm = FakeNM(hidden_ap=True, accepts=("wpa-psk",))
    ok, _ = _join(nm, hidden=True)
    assert ok is True
    assert nm.scans == 0
    assert nm.adds[0].get("802-11-wireless.hidden") == "yes"


def test_the_profile_is_pinned_to_the_ssid_and_the_radio(blind):
    nm = FakeNM(visible_after_scans=999)
    _join(nm)
    add = nm.adds[0]
    assert add["type"] == "wifi"
    assert add["ssid"] == SSID
    assert add["con-name"] == SSID
    assert add["ifname"] == pm._WIFI_IFACE
    assert add["connection.autoconnect"] == "yes"
    assert add["wifi-sec.psk"] == PSK


def test_an_ssid_with_a_colon_is_read_back_correctly(blind):
    """`nmcli -t` escapes a colon inside a field. Splitting on every colon
    made `Guest:5G` three fields and matched no profile - which would have
    left a stale one in place and lost the security flags."""
    nm = FakeNM("Guest:5G", visible_after_scans=1, security="WPA3", accepts=("sae",))
    ok, err = pm.apply_wifi("Guest:5G", PSK, None, False, 30.0, run=nm)
    assert ok is True and err is None
    assert [_km(a) for a in nm.adds] == ["sae"]        # the scan was parsed
    _join(nm, ssid="Guest:5G")                        # and so is the profile
    assert len(nm.profiles_for_ssid) == 1


# ─── what an earlier failed attempt left behind ───────────────────────────


def test_a_stale_profile_with_no_key_mgmt_is_replaced_not_inherited(blind):
    """The state Kamron's device is in right now. `nmcli device wifi
    connect` REUSES a saved profile matching the ssid, so the broken one
    the first attempt wrote made every later attempt fail identically no
    matter what was typed."""
    nm = FakeNM(visible_after_scans=999, stale=((SSID, ""),))
    stale_uuid = next(iter(nm.profiles))
    ok, err = _join(nm)
    assert ok is True and err is None
    assert stale_uuid in nm.deleted
    assert len(nm.profiles_for_ssid) == 1
    assert nm.profiles_for_ssid[0]["key_mgmt"] == "wpa-psk"


def test_the_duplicate_profiles_of_earlier_attempts_all_go(blind):
    """Matched on the ssid property, not the name: `Kamber Wifi 2.0 1` is
    just as able to be picked up as the one without the suffix."""
    nm = FakeNM(
        visible_after_scans=999,
        stale=((SSID, ""), (f"{SSID} 1", "wpa-psk"), (f"{SSID} 2", "")),
    )
    stale = list(nm.profiles)
    ok, _ = _join(nm)
    assert ok is True
    assert set(stale) <= set(nm.deleted)
    assert len(nm.profiles_for_ssid) == 1


def test_a_profile_for_another_network_is_left_alone(blind):
    nm = FakeNM(visible_after_scans=999)
    keep = nm._store("Neighbour", "Neighbour", "wpa-psk", hidden=False, psk="x")
    ok, _ = _join(nm)
    assert ok is True
    assert keep not in nm.deleted
    assert keep in nm.profiles


def test_repeated_attempts_do_not_pile_up_profiles(blind):
    """Three submissions of the same form used to leave `Kamber Wifi 2.0`,
    `Kamber Wifi 2.0 1`, `Kamber Wifi 2.0 2`."""
    nm = FakeNM(visible_after_scans=999)
    for _ in range(3):
        ok, _ = _join(nm)
        assert ok is True
        assert len(nm.profiles_for_ssid) == 1


def test_a_failed_attempt_leaves_nothing_for_the_next_one_to_inherit(blind):
    """A profile that cannot associate is not left behind with autoconnect
    on, so the retry in the caller starts from nothing either way."""
    nm = FakeNM(visible_after_scans=999, accepts=())
    ok, _ = _join(nm)
    assert ok is False
    assert nm.profiles_for_ssid == []


def test_a_second_attempt_succeeds_after_a_failed_first_one(blind):
    """The acceptance criterion: attempt two does not fail because of what
    attempt one left behind."""
    nm = FakeNM(visible_after_scans=999, accepts=(), psk=PSK)
    assert _join(nm)[0] is False
    nm.accepts = {"wpa-psk"}
    ok, err = _join(nm)
    assert ok is True and err is None
    assert len(nm.profiles_for_ssid) == 1


# ─── the words the customer sees ──────────────────────────────────────────


def test_nmclis_own_reason_is_kept(blind):
    """Reporting every failure as 'wrong password?' sent people re-typing a
    password that was right. The real reason has to reach the log and the
    portal's error."""
    nm = FakeNM(visible_after_scans=999, up_error=NOT_FOUND)
    ok, err = _join(nm)
    assert not ok
    assert "No network with SSID" in err
    assert "wrong password" not in err


def test_a_genuinely_wrong_password_still_says_so():
    nm = FakeNM(visible_after_scans=1, password_ok=False)
    ok, err = _join(nm, psk="definitely-not-it")
    assert not ok
    assert "Secrets were required" in err


def test_the_reason_reported_is_the_first_candidates(blind):
    """Both key-mgmt attempts fail on a wrong password. What the customer
    needs to read is the wpa-psk attempt's reason - their AP speaks PSK -
    not whatever the sae attempt happened to say."""
    nm = FakeNM(visible_after_scans=999, accepts=())
    ok, err = _join(nm)
    assert not ok
    assert "Secrets were required" in err
    assert "sae" not in err.lower()
    assert SSID in err


def test_a_profile_that_cannot_even_be_created_says_why(blind):
    nm = FakeNM(
        visible_after_scans=999,
        add_error="Error: Failed to add 'wifi' connection: no such device.",
    )
    ok, err = _join(nm)
    assert not ok
    assert "no such device" in err
    assert nm.ups == []


def test_a_timeout_is_reported_as_a_timeout(blind):
    nm = FakeNM(visible_after_scans=999, timeout_on="connection add")
    ok, err = _join(nm)
    assert not ok
    assert "timed out" in err


# ─── the passphrase ───────────────────────────────────────────────────────


def test_the_psk_never_reaches_the_error_or_the_log(caplog):
    nm = FakeNM(visible_after_scans=1, password_ok=False)
    with caplog.at_level("WARNING"):
        _, err = pm.apply_wifi(SSID, "s3cret-pw-999", None, False, 30.0, run=nm)
    assert "s3cret-pw-999" not in (err or "")
    assert "s3cret-pw-999" not in caplog.text


def test_an_nmcli_that_echoes_the_password_back_does_not_leak_it(blind, caplog):
    """nmcli normally says nothing about the value of wifi-sec.psk. This
    test does not depend on that staying true, because the one place a
    CORRECT password must never appear is the screen we show the customer
    and the log we ask them to send us."""
    nm = FakeNM(visible_after_scans=999, accepts=(), echo_psk_in_errors=True)
    with caplog.at_level(0):
        ok, err = _join(nm)
    assert not ok
    assert PSK not in (err or "")
    assert PSK not in caplog.text
    # The rest of nmcli's sentence survives the scrubbing.
    assert "Secrets were required" in err
    assert pm._PSK_PLACEHOLDER in err


def _psk_leak_scenarios(monkeypatch, tmp_path, secret):
    """Every path apply_wifi can take, each as (label, callable)."""
    conf = tmp_path / "wpa_supplicant.conf"
    conf.write_text("update_config=1\n", encoding="utf-8")

    def no_nmcli():
        monkeypatch.setattr(pm.shutil, "which", lambda n: None)
        monkeypatch.setattr(pm, "WPA_SUPPLICANT_CONF", conf)
        return pm.apply_wifi(SSID, secret, None, False, 0.01, run=FakeNM())

    return [
        ("blind join that works",
         lambda: pm.apply_wifi(SSID, secret, None, False, 30.0,
                               run=FakeNM(visible_after_scans=999, psk=secret))),
        ("blind join, wrong password, both key-mgmt",
         lambda: pm.apply_wifi(SSID, secret, None, False, 30.0,
                               run=FakeNM(visible_after_scans=999, accepts=()))),
        ("wpa3 fallback",
         lambda: pm.apply_wifi(SSID, secret, None, False, 30.0,
                               run=FakeNM(visible_after_scans=999,
                                          accepts=("sae",), psk=secret))),
        ("hidden network",
         lambda: pm.apply_wifi(SSID, secret, None, True, 30.0,
                               run=FakeNM(hidden_ap=True, accepts=()))),
        ("stale profile replaced",
         lambda: pm.apply_wifi(SSID, secret, None, False, 30.0,
                               run=FakeNM(visible_after_scans=999,
                                          stale=((SSID, ""),), psk=secret))),
        ("nmcli echoes the password in its error",
         lambda: pm.apply_wifi(SSID, secret, None, False, 30.0,
                               run=FakeNM(visible_after_scans=999, accepts=(),
                                          psk=secret, echo_psk_in_errors=True))),
        ("nmcli cannot create the profile",
         lambda: pm.apply_wifi(SSID, secret, None, False, 30.0,
                               run=FakeNM(visible_after_scans=999,
                                          add_error="Error: bad value."))),
        ("the add times out",
         lambda: pm.apply_wifi(SSID, secret, None, False, 30.0,
                               run=FakeNM(visible_after_scans=999,
                                          timeout_on="connection add"))),
        ("the activation times out",
         lambda: pm.apply_wifi(SSID, secret, None, False, 30.0,
                               run=FakeNM(visible_after_scans=999,
                                          timeout_on="connection up"))),
        ("no nmcli at all", no_nmcli),
    ]


def test_no_path_through_apply_wifi_lets_the_psk_out(monkeypatch, tmp_path, caplog):
    """The regression guard for the rule, rather than a careful reading of
    it: walk every path and look for the passphrase in the returned error,
    in every log record (at every level, message AND arguments), and in any
    exception that escapes. `subprocess.TimeoutExpired` is the sharp one -
    it stringifies the command it was given, and that command carries
    `wifi-sec.psk <psk>`.
    """
    monkeypatch.setattr(pm, "_WIFI_SCAN_WAIT_SEC", 0.0)
    secret = "correct-horse-battery"
    for label, call in _psk_leak_scenarios(monkeypatch, tmp_path, secret):
        caplog.clear()
        with caplog.at_level(0):
            try:
                _, err = call()
            except BaseException as e:          # noqa: BLE001 - that is the test
                raise AssertionError(
                    f"{label}: {type(e).__name__} escaped apply_wifi"
                    + (" WITH THE PSK IN IT" if secret in str(e) else "")
                ) from None
        assert secret not in (err or ""), f"{label}: psk in the error"
        assert secret not in caplog.text, f"{label}: psk in the log"
        for rec in caplog.records:
            assert secret not in rec.getMessage(), f"{label}: psk in a message"
            assert secret not in repr(rec.args), f"{label}: psk in log args"


# ─── the retry loop in the caller, unchanged ──────────────────────────────


def test_retries_are_paused_not_back_to_back(monkeypatch, tmp_path):
    """Three identical attempts in the same instant fail identically."""
    pauses: list[float] = []
    monkeypatch.setattr(pm.time, "sleep", lambda s: pauses.append(s))
    monkeypatch.setattr(pm, "apply_wifi", lambda *a, **k: (False, "no"))
    monkeypatch.setattr(pm, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(pm, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(pm, "PAIRING_TOKEN_PATH", tmp_path / "pairing_token")
    monkeypatch.setattr(pm, "EXAMPLE_CONFIG", tmp_path / "config.toml.example")
    monkeypatch.setattr(pm, "give_to_satellite_user", lambda p: True)
    (tmp_path / "config.toml.example").write_text(
        "[satellite]\nroom_id = 'x'\n", encoding="utf-8"
    )

    class T:
        def clear_provision(self) -> None:
            pass

    payload = {
        "room_id": "office", "domovoi_url": "ws://x:6370",
        "device_profile": "xvf3800_usb", "pairing_token": "t",
        "wifi": {"ssid": "HomeNet", "psk": "p"},
    }
    ok, _ = pm.apply_provision(
        payload, transport=T(), wifi_attempts=3, wifi_join_timeout=1.0
    )
    assert not ok
    # Two pauses between three attempts, none after the last.
    assert pauses.count(pm._WIFI_RETRY_PAUSE_SEC) == 2
