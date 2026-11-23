"""Root's own copy of the satellite code, the XVF3800 wrapper, and the
switch that moves a card onto them (security review 2026-11-23, M1).

Pins: the wrapper accepts every operation the satellite makes and nothing
else; the sudoers a card gets names the wrapper, never ``xvf_host`` or
``ALL``; the provisioning unit runs root's copy with the system Python and
names no path in the account's home; the switch is all or nothing (undone
in reverse on any failure) and changes nothing the second time; root's copy
takes an update only when the server's signature checks against the
root-owned pin and every file is the signed one; and the support-session
qualification check, unchanged, fails the old layout and passes the
switched one. Fakes for the file system's root-only parts and systemctl.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from satellite import code_sync, leds, mic_dsp, root_code, server_identity, support_shell, xvf_guard
from satellite.tests.test_signed_manifests import _envelope, _server

REPO = Path(__file__).resolve().parents[2]
SAT = REPO / "satellite"
TEMPLATES = REPO / "domovoi" / "satellite_media" / "templates"
USER = "alex"
HOME = f"/home/{USER}"

# The provisioning unit and sudoers a card prepared before 1.5.0 carries.
OLD_UNIT = f"""[Unit]
Description=Domovoi satellite USB adoption mode
Before=domovoi-satellite.service

[Service]
Type=simple
User=root
Environment=HOME={HOME}
ExecStart={HOME}/satellite-venv/bin/python -m satellite.provisioning_mode
WorkingDirectory={HOME}/domovoi
Restart=on-failure
"""
OLD_STATUS = """#!/bin/bash
XVF=/opt/xvf3800/xvf_host
xvf() {
  [ -x "$XVF" ] || return 1
  "$XVF" "$@" >/dev/null 2>&1 && return 0
  sudo -n "$XVF" "$@" >/dev/null 2>&1
}
"""
OLD_SUDOERS = f"""# Domovoi satellite sudoers set
{USER} ALL=(root) NOPASSWD: /usr/sbin/wpa_cli -i wlan0 reassociate
{USER} ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block restart domovoi-satellite.service
{USER} ALL=(root) NOPASSWD: /usr/local/sbin/domovoi-reboot ""
# The XVF3800's LED ring is driven over USB, which needs root.
{USER} ALL=(root) NOPASSWD: /opt/xvf3800/xvf_host
"""


# ─── the wrapper ──────────────────────────────────────────────────────────


def _status_ops() -> list[list[str]]:
    """Every xvf call the setup indicator (domovoi-status) can make."""
    text = (TEMPLATES / "domovoi-status.tmpl").read_text(encoding="utf-8")
    ops = []
    for color in set(re.findall(r"COLOR=(0x[0-9a-fA-F]+)", text)):
        ops.append(["led_color", color])
    for speed in set(re.findall(r"SPEED=(\d+)", text)):
        ops.append(["led_speed", speed])
    for effect in set(re.findall(r"EFFECT=(\d+)", text)):
        ops.append(["led_effect", effect])
    assert ops
    return ops


def _led_ops() -> list[list[str]]:
    """Every xvf_host call the LED driver makes, captured from the driver."""
    calls: list[list[str]] = []
    drv = leds._WS2812XvfDriver.__new__(leds._WS2812XvfDriver)
    drv._prefix = []
    drv._brightness = 255
    drv._run = lambda *a: calls.append(list(a))  # type: ignore[method-assign]
    for state in drv._STATES:
        for support in (False, True):
            drv.render_state(state, support=support)
    for b in (0, 16, 200, 255):
        calls.append(["led_brightness", str(b)])
    assert {c[0] for c in calls} >= {"led_effect", "led_color", "led_speed", "led_doa_color"}
    return calls


def _dsp_ops() -> list[list[str]]:
    ops = [[p] for p in mic_dsp.PARAMS]                    # the reads
    for rows in mic_dsp.PRESETS.values():
        ops += [[p, *vals] for p, vals in rows]
    for v in ([1.0], [1000.0], [0.15], [0.5], [1e-05], [31.6, 2.0]):   # a factory restore
        ops += [[p, *mic_dsp._fmt(v)] for p in mic_dsp.PARAMS]
    return ops


def test_the_wrapper_allows_every_operation_the_satellite_makes():
    for argv in _led_ops() + _status_ops() + _dsp_ops():
        assert xvf_guard.check(argv) == argv, argv


def test_the_wrappers_dsp_parameters_are_mic_dsps():
    assert tuple(mic_dsp.PARAMS) == xvf_guard.DSP_PARAMS


@pytest.mark.parametrize("argv", [
    [], ["--help"], ["-l", "/tmp/evil.so", "led_effect", "1"],
    ["SAVE_CONFIGURATION"], ["SAVE_CONFIGURATION", "1"], ["pp_agcgain", "1"],
    ["led_effect", "5"], ["led_effect", "1", "2"], ["led_effect"], ["led_effect", "01"],
    ["led_effect", "1\n"], ["led_effect", " 1"], ["led_effect", "1", "--config", "/tmp/x"],
    ["led_brightness", "256"], ["led_brightness", "-1"], ["led_brightness", "007"],
    ["led_speed", "1e3"], ["led_color", "red"], ["led_color", "0xff00ff\n"],
    ["led_color", "0xff00ff0"], ["led_color", "ff00ff"], ["led_color", "0xff00ff", "0x000000"],
    ["led_doa_color", "0x000000"], ["led_doa_color", "0x000000", "0x00ff00", "0x0000ff"],
    ["PP_AGCGAIN", "--load", "x.so"], ["PP_AGCGAIN", "-1"], ["PP_AGCGAIN", "1;id"],
    ["PP_AGCGAIN", "$(id)"], ["PP_AGCGAIN", "1 2"], ["PP_AGCGAIN", "nan"], ["PP_AGCGAIN", "0x10"],
    ["PP_AGCGAIN", "1" * 30], ["PP_MIN_NS", *["1"] * 9], ["PP_AGCGAIN", "/tmp/x"],
])
def test_the_wrapper_refuses_anything_else(argv):
    with pytest.raises(xvf_guard.Refused):
        xvf_guard.check(argv)
    ran = []
    assert xvf_guard.main(argv, execv=lambda *a: ran.append(a)) == 2
    assert ran == []


def test_the_wrapper_runs_the_tool_with_a_clean_environment(tmp_path, monkeypatch):
    tool = tmp_path / "xvf3800" / "xvf_host"
    tool.parent.mkdir()
    tool.write_text("")
    monkeypatch.setattr(xvf_guard, "tool_is_roots", lambda t: None)
    monkeypatch.setattr(xvf_guard.os, "chdir", lambda p: None)
    ran = []
    assert xvf_guard.main(["led_color", "0x00a8ff"], tool=tool,
                          execv=lambda *a: ran.append(a)) == 3
    path, argv, env = ran[0]
    assert path == str(tool) and argv == [str(tool), "led_color", "0x00a8ff"]
    assert set(env) == {"PATH", "LANG"}


def test_the_wrapper_will_not_run_a_tool_that_is_not_roots(tmp_path):
    tool = tmp_path / "xvf_host"
    tool.write_text("")
    assert xvf_guard.tool_is_roots(tool) is not None
    assert xvf_guard.tool_is_roots(tmp_path / "missing") is not None
    ran = []
    assert xvf_guard.main(["led_effect", "0"], tool=tool, execv=lambda *a: ran.append(a)) == 3
    assert ran == []


def test_the_wrapper_is_self_contained():
    src = (SAT / "xvf_guard.py").read_text(encoding="utf-8")
    assert src.startswith("#!/usr/bin/python3 -I\n") and "\r" not in src
    assert "from satellite" not in src and "import satellite" not in src


def test_the_client_tries_the_wrapper_before_the_tool(tmp_path):
    wrapper = tmp_path / "domovoi-xvf"
    assert xvf_guard.sudo_prefixes("/opt/xvf3800/xvf_host", wrapper) == [
        ["sudo", "-n", "/opt/xvf3800/xvf_host"]]
    wrapper.write_text("")
    assert xvf_guard.sudo_prefixes("/opt/xvf3800/xvf_host", wrapper) == [
        ["sudo", "-n", str(wrapper)], ["sudo", "-n", "/opt/xvf3800/xvf_host"]]


def test_mic_dsp_goes_through_the_wrapper(tmp_path, monkeypatch):
    wrapper = tmp_path / "domovoi-xvf"
    wrapper.write_text("")
    monkeypatch.setattr(xvf_guard, "WRAPPER", wrapper)
    calls = []

    def runner(argv):
        calls.append(argv)
        if argv[:3] != ["sudo", "-n", str(wrapper)]:
            return 1, ""
        xvf_guard.check(argv[3:])
        return 0, f"{argv[3]} 1\n"

    dsp = mic_dsp.XvfDsp("/opt/xvf3800/xvf_host", runner=runner, state_file=tmp_path / "s.json")
    assert dsp.apply("C")["ok"]
    assert all(c[:3] == ["sudo", "-n", str(wrapper)] for c in calls[1:])


# ─── sudoers ──────────────────────────────────────────────────────────────


def _rules(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def _listing(sudoers: str, user: str = USER) -> str:
    """``sudo -n -l`` as the account would see it for this file."""
    lines = [f"User {user} may run the following commands on sat:"]
    for ln in _rules(sudoers):
        lines.append("    (root) NOPASSWD: " + ln.split("NOPASSWD: ", 1)[1])
    return "\n".join(lines) + "\n"


def test_a_new_cards_sudoers_names_only_the_wrapper():
    from domovoi.satellite_media import overlay

    text = overlay.render_template("sudoers.tmpl", {}).replace("@USER@", USER)
    rules = _rules(text)
    assert f"{USER} ALL=(root) NOPASSWD: /usr/local/sbin/domovoi-xvf" in rules
    assert not any("xvf_host" in r for r in rules)
    assert not any(root_code.loose_rule(r, USER) for r in rules)
    assert root_code.desired_sudoers(text, USER) == text       # nothing to switch
    assert support_shell.sudo_rule_takes_free_args(_listing(text)) is None


def test_the_switch_takes_out_xvf_host_and_every_all_rule():
    old = OLD_SUDOERS + f"{USER} ALL=(ALL:ALL) NOPASSWD: ALL\n{USER}  ALL = (root) ALL\n"
    new = root_code.desired_sudoers(old, USER)
    rules = _rules(new)
    assert rules == [
        f"{USER} ALL=(root) NOPASSWD: /usr/sbin/wpa_cli -i wlan0 reassociate",
        f"{USER} ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block restart domovoi-satellite.service",
        f'{USER} ALL=(root) NOPASSWD: /usr/local/sbin/domovoi-reboot ""',
        f"{USER} ALL=(root) NOPASSWD: /usr/local/sbin/domovoi-xvf",
    ]
    assert root_code.desired_sudoers(new, USER) == new
    assert support_shell.sudo_rule_takes_free_args(_listing(OLD_SUDOERS)) is not None
    assert support_shell.sudo_rule_takes_free_args(_listing(new)) is None


# ─── the unit ─────────────────────────────────────────────────────────────


def _template() -> str:
    return (SAT / root_code.UNIT_TEMPLATE).read_text(encoding="utf-8")


def _service(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            out[k] = v
    return out


def test_the_root_unit_runs_roots_copy_with_the_system_python():
    unit = root_code.render_unit(_template(), USER)
    svc = _service(unit)
    assert svc["User"] == "root"
    assert svc["ExecStart"] == "/usr/bin/python3 -E -s -m satellite.root_entry"
    assert svc["WorkingDirectory"] == "/usr/local/lib/domovoi/root-code"
    assert svc["Environment"] == f"DOMOVOI_SATELLITE_USER={USER}"
    code = "\n".join(ln for ln in unit.splitlines() if not ln.startswith("#"))
    assert "@" not in code and HOME not in code and "satellite-venv" not in code
    with pytest.raises(root_code.RootCodeError):
        root_code.render_unit(_template(), "root")
    with pytest.raises(root_code.RootCodeError):
        root_code.render_unit(_template(), "alex/../x")


def test_the_module_root_runs_is_in_roots_copy_and_self_contained():
    """Root imports only stdlib modules beside it; nothing in the copy
    reaches for the account's venv (requests, numpy) on the way in."""
    for name in ("root_code.py", "root_entry.py"):
        src = (SAT / name).read_text(encoding="utf-8")
        assert "import requests" not in src and "satellite-venv" not in src.split('"""', 2)[2]


# ─── the switch ───────────────────────────────────────────────────────────


class FakeOps(root_code.Ops):
    """systemctl/visudo/the system Python recorded; the release link a
    dict (a copy of the release, so reading through it works anywhere)."""

    def __init__(self, fail: str | None = None):
        super().__init__(run=self._run, chown_root=lambda p: None, link=self._link,
                         readlink=self._readlink, unlink=self._unlink)
        self.fail = fail
        self.calls: list[list[str]] = []
        self.links: dict[Path, Path] = {}

    def _run(self, argv, **kw):
        self.calls.append(list(argv))
        tool = Path(argv[0]).name
        if self.fail == tool or (self.fail == "daemon-reload" and argv[-1] == "daemon-reload"):
            return SimpleNamespace(returncode=1, stdout="", stderr="no")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def _link(self, target: Path, link: Path) -> None:
        if link.exists():
            shutil.rmtree(link)
        shutil.copytree(target, link)
        self.links[link] = target

    def _readlink(self, link: Path):
        return self.links.get(link)

    def _unlink(self, link: Path) -> None:
        if link.exists():
            shutil.rmtree(link)
        self.links.pop(link, None)


def _files(version: str = "1") -> dict[str, bytes]:
    return {
        "__init__.py": b"",
        "root_entry.py": b"# entry\n",
        "root_code.py": b"# root code\n",
        "provisioning_mode.py": f"VERSION = {version!r}\n".encode(),
        "xvf_guard.py": (SAT / "xvf_guard.py").read_bytes(),
        root_code.UNIT_TEMPLATE: _template().encode(),
    }


@pytest.fixture
def card(tmp_path):
    """An old card's root-owned files, under a temp root."""
    layout = root_code.Layout(
        lib_dir=tmp_path / "usr/local/lib/domovoi",
        unit=tmp_path / "etc/systemd/system/domovoi-provisioning.service",
        sudoers=tmp_path / "etc/sudoers.d/domovoi-satellite",
        wrapper=tmp_path / "usr/local/sbin/domovoi-xvf",
        state_dir=tmp_path / "var/lib/domovoi",
        status=tmp_path / "usr/local/sbin/domovoi-status",
    )
    layout.status.parent.mkdir(parents=True)
    layout.status.write_text(OLD_STATUS, encoding="utf-8")
    layout.unit.parent.mkdir(parents=True)
    layout.unit.write_text(OLD_UNIT, encoding="utf-8")
    layout.sudoers.parent.mkdir(parents=True)
    layout.sudoers.write_text(OLD_SUDOERS, encoding="utf-8")
    layout.state_dir.mkdir(parents=True)
    return layout


def _snapshot(layout: root_code.Layout) -> dict[str, object]:
    return {
        "unit": layout.unit.read_text(encoding="utf-8"),
        "sudoers": layout.sudoers.read_text(encoding="utf-8"),
        "wrapper": layout.wrapper.exists(),
        "status": layout.status.read_text(encoding="utf-8"),
        "root_code": layout.root_code.exists(),
        "releases": sorted(p.name for p in layout.releases.iterdir()) if layout.releases.exists() else [],
    }


def test_the_switch_moves_an_old_card_onto_roots_copy(card):
    ops = FakeOps()
    result = root_code.switch(_files(), USER, layout=card, ops=ops, source="running tree")
    assert result["changed"] == ["root's code copy", "the XVF3800 wrapper",
                                 "the provisioning unit", "sudoers", "the setup indicator"]
    status = card.status.read_text(encoding="utf-8")
    assert status.index("sudo -n /usr/local/sbin/domovoi-xvf") < status.index('sudo -n "$XVF"')
    assert root_code.desired_status(status) == status
    assert card.unit.read_text(encoding="utf-8") == root_code.render_unit(_template(), USER)
    assert card.wrapper.read_bytes() == (SAT / "xvf_guard.py").read_bytes()
    assert "xvf_host" not in "\n".join(_rules(card.sudoers.read_text(encoding="utf-8")))
    release = ops.links[card.root_code]
    assert release.parent == card.releases and release.name == result["release"]
    assert (card.root_code / "satellite" / "provisioning_mode.py").read_text() == "VERSION = '1'\n"
    smoke = next(c for c in ops.calls if c[0] == root_code.SYSTEM_PYTHON)
    assert smoke[:3] == ["/usr/bin/python3", "-E", "-s"]
    visudo = next(c for c in ops.calls if c[0].endswith("visudo"))
    assert visudo[1] == "-cf" and visudo[2].endswith(".domovoi-new")   # checked before it lands
    assert ops.calls[-1][-1] == "daemon-reload"
    assert json.loads(card.record.read_text())["release"] == result["release"]
    assert not list(card.unit.parent.glob("*.domovoi-new"))


def test_the_switch_is_idempotent(card):
    root_code.switch(_files(), USER, layout=card, ops=FakeOps())
    ops = FakeOps()
    ops.links = {card.root_code: card.releases / root_code.release_name(
        root_code._digests(_files()))}
    before = _snapshot(card)
    assert root_code.switch(_files(), USER, layout=card, ops=ops)["changed"] == []
    assert ops.calls == []
    assert _snapshot(card) == before


@pytest.mark.parametrize("fail", ["python3", "visudo", "daemon-reload"])
def test_a_failed_switch_leaves_the_card_as_it_was(card, fail):
    before = _snapshot(card)
    ops = FakeOps(fail=fail)
    with pytest.raises(root_code.RootCodeError, match="undone"):
        root_code.switch(_files(), USER, layout=card, ops=ops)
    assert _snapshot(card) == {**before, "releases": []}
    assert card.root_code not in ops.links
    assert not list(card.unit.parent.glob("*.domovoi-new"))
    assert not list(card.sudoers.parent.glob("*.domovoi-new"))
    assert not card.record.exists()
    if fail != "python3":
        assert ops.calls[-1][-1] == "daemon-reload"     # systemd re-reads the old unit


def test_a_failed_update_of_a_switched_card_goes_back_to_the_last_release(card):
    root_code.switch(_files("1"), USER, layout=card, ops=(ops := FakeOps()))
    first = ops.links[card.root_code]
    before = _snapshot(card)
    ops.fail = "daemon-reload"
    with pytest.raises(root_code.RootCodeError):
        root_code.switch(_files("2"), USER, layout=card, ops=ops)
    assert ops.links[card.root_code] == first
    assert (card.root_code / "satellite" / "provisioning_mode.py").read_text() == "VERSION = '1'\n"
    assert _snapshot(card) == before


def test_a_switch_without_the_wrapper_source_changes_nothing(card):
    files = _files()
    del files["xvf_guard.py"]
    before = _snapshot(card)
    with pytest.raises(root_code.RootCodeError):
        root_code.switch(files, USER, layout=card, ops=FakeOps())
    assert _snapshot(card) == before


# ─── the qualification check, unchanged, on both layouts ──────────────────


def _mine(path: Path) -> bool:
    return str(path).replace("\\", "/").startswith(HOME)


def _declares(card: root_code.Layout, tmp_path: Path) -> bool:
    model = tmp_path / "model"
    model.write_text("Raspberry Pi Zero 2 W Rev 1.0\x00")

    def run(argv, **kw):
        if argv == ["sudo", "-n", "-l"]:
            return SimpleNamespace(returncode=0, stdout=_listing(card.sudoers.read_text()))
        return SimpleNamespace(returncode=1)

    return support_shell.should_declare(
        is_pc=False, platform="linux", model_path=model, state_dir=card.state_dir, run=run,
        root_pin=lambda: True, watchdog=lambda: "/run/systemd/notify",
        root_path=lambda: support_shell.root_unit_runs_my_code((card.unit.parent,),
                                                               writable=_mine))


def test_the_check_fails_the_old_layout_and_passes_the_switched_one(card, tmp_path):
    assert support_shell.root_unit_runs_my_code((card.unit.parent,), writable=_mine)
    assert not _declares(card, tmp_path)
    root_code.switch(_files(), USER, layout=card, ops=FakeOps())
    assert support_shell.root_unit_runs_my_code((card.unit.parent,), writable=_mine) is None
    assert _declares(card, tmp_path)
    # Half a switch is not enough: the old sudoers line alone still fails it.
    card.sudoers.write_text(OLD_SUDOERS)
    assert not _declares(card, tmp_path)


# ─── updates to root's copy ───────────────────────────────────────────────


@pytest.fixture
def account(tmp_path, monkeypatch):
    home = tmp_path / "home" / USER
    tree = home / "domovoi" / "satellite"
    tree.mkdir(parents=True)
    (home / ".domovoi").mkdir()
    monkeypatch.setattr(root_code, "home_of", lambda u: home if u == USER else None)
    return home


def _publish(home: Path, files: dict[str, bytes], seed, public, *, serial: int) -> dict:
    tree = home / "domovoi" / "satellite"
    for rel, data in files.items():
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_bytes(data)
    manifest = {rel: hashlib.sha256(d).hexdigest() for rel, d in files.items()}
    doc = _envelope(seed, public, server_identity.CODE_CHANNEL, manifest, serial=serial)
    assert code_sync.save_envelope(doc, home / ".domovoi" / root_code.ENVELOPE_NAME)
    return doc


def _version(card: root_code.Layout) -> str:
    return (card.root_code / "satellite" / "provisioning_mode.py").read_text()


def test_root_takes_a_signed_update_and_only_the_signed_bytes(card, account):
    seed, public, fp = _server()
    ops = FakeOps()
    root_code.switch(_files("1"), USER, layout=card, ops=ops)
    _publish(account, _files("2"), seed, public, serial=5)
    result = root_code.refresh(USER, layout=card, ops=ops, pin=lambda: fp)
    assert result and result["changed"] == ["root's code copy"]
    assert _version(card) == "VERSION = '2'\n"
    # The same envelope again: nothing to do.
    assert root_code.refresh(USER, layout=card, ops=ops, pin=lambda: fp)["changed"] == []


def test_root_refuses_an_unsigned_tampered_or_older_update(card, account):
    seed, public, fp = _server()
    ops = FakeOps()
    root_code.switch(_files("1"), USER, layout=card, ops=ops)
    _publish(account, _files("2"), seed, public, serial=5)
    # The account edits a signed file after the sync: not the signed bytes.
    (account / "domovoi" / "satellite" / "provisioning_mode.py").write_text("import os  # mine\n")
    root_code.refresh(USER, layout=card, ops=ops, pin=lambda: fp)
    assert _version(card) == "VERSION = '1'\n"
    # A list signed by some other key.
    other_seed, other_public, _ = _server(7)
    _publish(account, _files("3"), other_seed, other_public, serial=6)
    root_code.refresh(USER, layout=card, ops=ops, pin=lambda: fp)
    assert _version(card) == "VERSION = '1'\n"
    # No root-owned pin: nothing can be checked, nothing comes in.
    _publish(account, _files("4"), seed, public, serial=7)
    root_code.refresh(USER, layout=card, ops=ops, pin=lambda: None)
    assert _version(card) == "VERSION = '1'\n"
    # Accepted at serial 7; then an older genuine list is replayed.
    root_code.refresh(USER, layout=card, ops=ops, pin=lambda: fp)
    assert _version(card) == "VERSION = '4'\n"
    _publish(account, _files("5"), seed, public, serial=6)
    root_code.refresh(USER, layout=card, ops=ops, pin=lambda: fp)
    assert _version(card) == "VERSION = '4'\n"


def test_refresh_puts_back_a_loosened_sudoers_from_roots_own_copy(card, account):
    ops = FakeOps()
    root_code.switch(_files(), USER, layout=card, ops=ops)
    card.sudoers.write_text(OLD_SUDOERS)
    result = root_code.refresh(USER, layout=card, ops=ops, pin=lambda: None)
    assert result["changed"] == ["sudoers"]
    assert "xvf_host" not in "\n".join(_rules(card.sudoers.read_text()))


def test_the_old_card_switches_from_its_running_tree(card, account, monkeypatch):
    """provisioning_mode.main on an old card: the account's tree is what
    root is running, so that is what root's copy starts from."""
    monkeypatch.setattr(root_code, "user_of_home", lambda h: USER if h == str(account) else None)
    monkeypatch.setattr(root_code, "verified_manifest", lambda *a, **k: None)
    seen = {}
    monkeypatch.setattr(root_code, "collect_tree", lambda src: (seen.setdefault("src", src), _files())[1])
    ops = FakeOps()
    result = root_code.switch_running_card(layout=card, ops=ops, home=str(account))
    assert result and "the provisioning unit" in result["changed"]
    assert seen["src"] == SAT.resolve()
    assert root_code.switch_running_card(layout=card, ops=ops, home=str(account))["changed"] == []
    # Never for an account it can't name.
    assert root_code.switch_running_card(layout=card, ops=ops, home="/home/nobody") is None


def test_collect_tree_skips_tests_caches_and_links(tmp_path):
    src = tmp_path / "satellite"
    (src / "tests").mkdir(parents=True)
    (src / "__pycache__").mkdir()
    (src / "a.py").write_text("a")
    (src / "tests" / "t.py").write_text("t")
    (src / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"x")
    (src / "secret.env").write_text("no")
    assert root_code.collect_tree(src) == {"a.py": b"a"}


def test_the_client_keeps_the_envelope_it_accepted(tmp_path, monkeypatch):
    from satellite.tests.test_signed_manifests import BASE, _code_routes, _FakeRequests

    seed, public, fp = _server()
    body = b"print('hello')\n"
    manifest = {"client.py": hashlib.sha256(body).hexdigest()}
    monkeypatch.setattr(code_sync, "requests",
                        _FakeRequests(_code_routes(seed, public, manifest, {"client.py": body})))
    monkeypatch.setattr(server_identity, "FRESHNESS_SIDECAR", tmp_path / "fresh.json")
    keep = tmp_path / "kept.json"
    monkeypatch.setattr(code_sync, "ENVELOPE_PATH", keep)
    code_sync.sync_code(BASE, tmp_path / "satellite", frozenset({".py"}), {},
                        expected_fingerprint=fp)
    doc = json.loads(keep.read_text())
    assert doc["manifest"] == manifest and doc["signature_v2"]


def test_a_new_cards_setup_indicator_already_uses_the_wrapper():
    from domovoi.satellite_media import overlay

    helper = overlay.render_status_helper("xvf3800_usb")
    assert root_code.desired_status(helper) == helper
    body = helper[helper.index("xvf() {"):]
    body = body[:body.index("\n}\n")]
    assert body.index('sudo -n "$XVF_WRAPPER"') < body.index('sudo -n "$XVF"')
    assert "XVF_WRAPPER=/usr/local/sbin/domovoi-xvf" in helper
