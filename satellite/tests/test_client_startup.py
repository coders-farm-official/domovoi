"""How the satellite client is allowed to give up.

The unit is ``Restart=on-failure``, which makes the exit code the difference
between a satellite that keeps trying and one that simply stops existing.

Found on hardware: a wake-word model that failed to load set the shutdown
event and returned, ``main()`` returned 0, and systemd — correctly — did not
restart a clean exit. The device died once, silently, holding whatever
colour the setup indicator had last written straight to the LED ring. From
the outside it looked like a satellite that had been approved and then
vanished: nothing in ``active_rooms``, nothing in the core's log, and no
way in without a keyboard on a board whose only data port had the
microphone in it.
"""

from __future__ import annotations

import inspect

from satellite import client


def test_a_requested_shutdown_still_exits_zero():
    """Ctrl-C and SIGTERM are not failures; systemd must not fight them."""
    src = inspect.getsource(client.main)
    assert "return 0" in src


def test_a_startup_failure_exits_non_zero_so_systemd_retries():
    src = inspect.getsource(client.main)
    assert "fatal_error" in src
    assert "return 1" in src
    # The non-zero return has to be GATED on the failure flag - returning 1
    # unconditionally would make an operator's clean stop look like a crash.
    assert src.index("fatal_error") < src.index("return 1")


def test_the_wake_model_failure_sets_the_flag():
    """This is the path that was exiting 0."""
    src = inspect.getsource(client.Satellite._mic_thread_run)
    assert "fatal_error" in src
    assert "shutdown_event.set()" in src
    assert src.index("fatal_error") < src.index("shutdown_event.set()")


def test_the_flag_starts_clear():
    src = inspect.getsource(client.Satellite.__init__)
    assert "self.fatal_error" in src


def test_the_ring_says_something_before_the_process_goes():
    """The LED thread dies with the process, so the last write is what stays
    on the hardware. It should be the failure, not a stale setup colour."""
    src = inspect.getsource(client.Satellite._mic_thread_run)
    assert '_setup_status("startup-failed")' in src


# ─── recovering the Wi-Fi link ────────────────────────────────────────────
#
# Found on hardware: a portal-onboarded satellite lost its address entirely
# (`hostname -I` empty) and never came back. Its only recovery was
# `wpa_cli reassociate` — a wpa_supplicant tool — while the device is
# NetworkManager end to end: the setup AP is `nmcli device wifi hotspot`
# and the join is `nmcli device wifi connect`. wpa_cli cannot reach an
# NM-driven supplicant, so the watchdog was a no-op on exactly the units
# the portal creates.


def test_networkmanager_is_tried_before_wpa_cli():
    src = inspect.getsource(client.Satellite._reassociate_wifi)
    assert "_nm_manages_wifi" in src
    assert "_reconnect_wifi_nm" in src
    # NM first: on an NM device the wpa_cli call below fails every time.
    # Compared against the INVOCATION, not the name - the docstring
    # mentions wpa_cli while explaining why NM comes first.
    assert src.index("_nm_manages_wifi") < src.index("/usr/sbin/wpa_cli")


def test_an_unmanaged_device_still_uses_wpa_cli():
    """Hand-built satellites on plain wpa_supplicant must keep working."""
    src = inspect.getsource(client.Satellite._nm_manages_wifi)
    assert "unmanaged" in src
    assert inspect.getsource(client.Satellite._reassociate_wifi).count("wpa_cli") >= 1


def test_the_nmcli_recovery_is_allowlisted():
    """It runs under `sudo -n`, so without the sudoers line it silently
    fails the same way wpa_cli did."""
    from domovoi.satellite_media import overlay

    sudoers = overlay.render_template("sudoers.tmpl", {"USER": "domovoi"})
    assert "/usr/bin/nmcli device connect wlan0" in sudoers
    src = inspect.getsource(client.Satellite._reconnect_wifi_nm)
    assert '"/usr/bin/nmcli"' in src
    assert '"sudo", "-n"' in src


# ─── saying "I can't reach the server" ────────────────────────────────────
#
# Found on hardware: a satellite lost its Wi-Fi and spent an hour
# reconnecting into nothing. The dashboard said "waiting", the ring held a
# stale "awaiting approval" violet, and the client logged reconnects - three
# surfaces, none of which said "no network". The ring is the only one that
# still works when the network is the broken thing.


def test_an_unreachable_server_reaches_the_ring():
    src = inspect.getsource(client.Satellite.run)
    assert '_setup_status("no-server")' in src
    assert "_UNREACHABLE_AFTER" in src


def test_a_reachable_server_resets_it():
    """A session the core REJECTS still proves the core is reachable, and it
    has its own colour - so it must not count toward this."""
    src = inspect.getsource(client.Satellite.run)
    reset = src.index("unreachable = 0", src.index("await self._run_session()"))
    bump = src.index("unreachable += 1")
    assert reset < bump, "the success path must clear the counter"


def test_it_fires_once_not_every_retry():
    """Each call is a sudo + process spawn, and an outage can last hours on a
    board that cannot spare either."""
    src = inspect.getsource(client.Satellite.run)
    assert "unreachable == _UNREACHABLE_AFTER" in src


def test_a_brief_blip_stays_quiet():
    assert client._UNREACHABLE_AFTER >= 3


def test_the_state_exists_on_both_boards():
    from domovoi.satellite_media import overlay
    from satellite import leds

    helper = overlay.render_template(
        "domovoi-status.tmpl", {"HOME": "/home/domovoi", "ANNOUNCE": "1"}
    )
    assert "  no-server)" in helper
    assert "no-server" in leds.SETUP_COLORS


def test_no_server_is_not_confusable_with_another_state():
    """The whole point is a colour that means one thing."""
    from satellite import leds

    colors = leds.SETUP_COLORS
    assert colors["no-server"] != colors["setting-up"]
    assert colors["no-server"] != colors["startup-failed"]
    assert colors["no-server"] != colors["awaiting-approval"]
