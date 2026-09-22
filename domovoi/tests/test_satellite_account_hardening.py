"""The satellite service account's root access, as the prepared card
renders it (SAT-2).

Every script root runs on a prepared satellite is root's own file, the
account is not in the sudo group, and the sudoers.d file stage 1 renders is
the whole of its root access. These tests read the rendered templates the
way the device will run them; the end-to-end check (`sudo -n true` fails
as the service account) runs on the container satellite.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi.satellite_media import overlay

REPO = Path(__file__).resolve().parents[2]

# The complete set of commands the account may run as root. A line in the
# rendered sudoers that is not one of these is a regression.
EXPECTED_HELPERS = {
    "/usr/sbin/wpa_cli -i wlan0 reassociate",
    "/usr/bin/nmcli device connect wlan0",
    "/usr/bin/systemctl --no-block restart domovoi-satellite.service",
    "/usr/bin/systemctl --no-block restart domovoi-kiosk.service",
    "/usr/local/sbin/domovoi-apply-payload",
    "/usr/local/sbin/domovoi-sync-time",
    "/opt/xvf3800/xvf_host",
}


def _firstrun() -> str:
    return overlay.render_firstrun("domovoi", "xvf3800_usb", "voice", "portal", "US")


def _stage2() -> str:
    return overlay.render_stage2("domovoi")


def _status_helper() -> str:
    return overlay.render_status_helper("xvf3800_usb")


def _code_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


# ─── group membership ─────────────────────────────────────────────────────


def test_stage_one_creates_the_account_outside_the_sudo_group():
    script = _firstrun()
    line = next(ln for ln in _code_lines(script) if "usermod -aG" in ln)
    groups = line.split("usermod -aG ", 1)[1].split()[0].split(",")
    assert "sudo" not in groups
    for needed in ("audio", "video", "plugdev", "gpio", "spi", "i2c"):
        assert needed in groups, needed


@pytest.mark.parametrize("render", [_firstrun, _stage2])
def test_both_stages_remove_sudo_membership_and_mask_the_pi_nopasswd_dropin(render):
    """Pi OS's first-boot user setup runs between the two stages and would
    otherwise hand the account blanket sudo; each stage takes it back."""
    code = "\n".join(_code_lines(render()))
    assert 'gpasswd -d "$SAT_USER" sudo' in code
    assert ">/etc/sudoers.d/010_pi-nopasswd" in code
    assert "chmod 0440 /etc/sudoers.d/010_pi-nopasswd" in code
    # and it is called, not merely defined
    assert re.search(r"^harden_account$", code, re.M), "harden_account is never invoked"


def test_the_hardening_is_not_behind_a_skip_marker():
    """A skip-marked step runs once per card; this has to win on every
    boot that re-enters the bootstrap."""
    script = _firstrun()
    defn = script.index("harden_account() {")
    call = script.index("\nharden_account\n")
    assert call > defn
    # No `skip` guard between the definition and the call.
    assert "skip " not in script[defn:call]


# ─── the sudoers file ─────────────────────────────────────────────────────


def test_the_rendered_sudoers_grants_exactly_the_named_helpers():
    sudoers = overlay.render_template("sudoers.tmpl", {"USER": "domovoi"})
    granted = set()
    for line in _code_lines(sudoers):
        user, _, rest = line.partition(" ")
        assert user == "domovoi", line
        assert rest.startswith("ALL=(root) NOPASSWD: "), line
        cmd = rest[len("ALL=(root) NOPASSWD: "):]
        assert cmd.startswith("/"), f"relative command in sudoers: {line}"
        granted.add(cmd)
    assert granted == EXPECTED_HELPERS


def test_the_sudoers_carries_no_blanket_grant():
    sudoers = overlay.render_template("sudoers.tmpl", {"USER": "domovoi"})
    for forbidden in ("%sudo", "ALL=(ALL", "NOPASSWD: ALL", "NOPASSWD:ALL", "ALL=ALL"):
        assert forbidden not in sudoers, forbidden


def test_every_sudoers_target_is_a_root_owned_path():
    """The account can edit anything under its home; a sudoers line pointing
    there would be a line the account could rewrite."""
    for cmd in EXPECTED_HELPERS:
        binary = shlex.split(cmd)[0]
        assert binary.startswith(("/usr/", "/opt/")), binary
        assert "/home/" not in binary


# ─── root never executes what the account can edit ────────────────────────


def test_stage_two_is_installed_as_a_root_helper_and_the_unit_runs_it_from_there():
    script = _firstrun()
    code = "\n".join(_code_lines(script))
    assert 'install -m 0755 "$PAYDIR/bootstrap/stage2.sh" /usr/local/sbin/domovoi-stage2' in code
    assert "scripts/stage2.sh" not in code
    assert 'chown "$SAT_USER:$SAT_USER" "$HOME_DIR/domovoi/satellite/scripts' not in code
    unit = (overlay.TEMPLATES_DIR / "domovoi-bootstrap.service").read_text(encoding="utf-8")
    exec_line = next(ln for ln in unit.splitlines() if ln.startswith("ExecStart="))
    assert exec_line == "ExecStart=/usr/local/sbin/domovoi-stage2"
    assert "@HOME@" not in exec_line


def test_stage_two_only_runs_the_venv_python_as_the_service_account():
    """Every venv invocation in stage 2 goes through runuser; the one that
    did not (the device-info update) uses the OS's python3 instead."""
    for line in _code_lines(_stage2()):
        if '"$VENVPY"' in line:
            assert "as_user" in line, line
    assert 'python3 - "$BOOT/domovoi/device-info.json"' in _stage2()


def test_the_led_fallback_drops_to_the_service_account_when_root():
    """domovoi-status is called by three root programs and by the client.
    The 2-Mics ring is driven by leds.py out of the account's venv and code
    tree, so a root caller has to become that account first."""
    helper = _status_helper()
    assert "runuser -u \"$SAT_USER\"" in helper
    assert "-m satellite.leds" in helper
    assert 'SAT_USER="@USER@"' in helper
    # The root branch is gated on the effective uid, and the user branch
    # still exists for the client.
    assert '[ "$(id -u)" = "0" ] && AS_ROOT=1' in helper
    root_branch = helper.split('if [ "$AS_ROOT" = "1" ]; then\n    runuser', 1)
    assert len(root_branch) == 2, "the LED fallback has no root branch"


def test_stage_one_renders_the_user_into_the_status_helper():
    """The helper's @USER@ is rendered by stage 1's sed, in both places it
    installs the helper; an unrendered placeholder is a runuser of nobody."""
    script = _firstrun()
    pattern = (
        'sed -e "s|@HOME@|$HOME_DIR|g" -e "s|@USER@|$SAT_USER|g" \\\n'
        '{indent}"$PAYDIR/system/domovoi-status" >/usr/local/sbin/domovoi-status'
    )
    rendered = script.count(pattern.format(indent="    ")) + script.count(
        pattern.format(indent="      ")
    )
    assert rendered == 2, script.count("/usr/local/sbin/domovoi-status")


def test_root_logs_the_indicator_to_its_own_file():
    """A root process appending to a file inside the account's home is a
    write that account can redirect, so root keeps its own log."""
    helper = _status_helper()
    assert "SLOG=/var/log/domovoi-setup-status.log" in helper
    assert 'SLOG="@HOME@/.domovoi/setup-status.log"' in helper


def test_stage_one_does_not_copy_the_deb_cache_into_the_home_directory():
    """apply-payload installs from the root-owned unpacked payload; a copy
    the account owned would be packages it can edit and root installs."""
    code = "\n".join(_code_lines(_firstrun()))
    assert "deb_cache" not in code
    assert '"$PAYDIR/debs"' not in code or 'cp -r "$PAYDIR/debs" "$HOME_DIR' not in code


# ─── the scripts still parse ──────────────────────────────────────────────


def test_every_root_helper_is_valid_shell(tmp_path):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash available to parse with")
    bodies = {
        "firstrun.sh": _firstrun(),
        "stage2.sh": _stage2(),
        "domovoi-status": _status_helper(),
        "domovoi-apply-payload": (REPO / "satellite" / "scripts" / "domovoi-apply-payload")
        .read_text(encoding="utf-8"),
    }
    for name, body in bodies.items():
        path = tmp_path / name
        path.write_text(body, encoding="utf-8", newline="\n")
        r = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True)
        assert r.returncode == 0, f"{name}: {r.stderr.strip()}"


# ─── the docs say what the templates do ───────────────────────────────────


def test_no_doc_claims_least_privilege_for_the_sudoers_lines():
    """The lines grant named helpers, not least privilege; the docs used to
    say otherwise. Any doc making the claim again fails here."""
    for doc in (REPO / "satellite" / "PROVISIONING.md", REPO / "docs" / "SECURITY_PRIVACY.md"):
        text = doc.read_text(encoding="utf-8", errors="replace").lower()
        assert "least-privilege" not in text and "least privilege" not in text, doc


def test_the_docs_state_the_real_posture():
    sec = (REPO / "docs" / "SECURITY_PRIVACY.md").read_text(encoding="utf-8", errors="replace")
    assert "not in the\n`sudo` group" in sec or "not in the `sudo` group" in sec
    assert "010_pi-nopasswd" in sec
    assert "/usr/local/sbin/domovoi-stage2" in sec
    assert "re-prepped and re-flashed" in sec
    # and the residue is named rather than hidden
    assert "domovoi-provisioning.service" in sec
    prov = (REPO / "satellite" / "PROVISIONING.md").read_text(encoding="utf-8", errors="replace")
    assert "`sudo -n true` fails" in prov
