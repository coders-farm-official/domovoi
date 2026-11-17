"""Friend Houses' firewall script (scripts/linux/fh-firewall.sh), run
against a fake iptables that keeps its rules in a file, and the core's
reading of the state file it writes (friend_houses.service.firewall_state).

The real kernel rules were proven separately in a Docker-in-Docker network
namespace (internet open; the host's ports and a LAN machine blocked; open
again after `remove`). This pins the script's logic: it fills its own
chain from scratch, inserts each jump once, survives a flush of
DOCKER-USER (what a Docker restart does), and `check` tells the truth.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "linux" / "fh-firewall.sh"

posix_bash = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="runs the bash script with a fake iptables (POSIX)",
)

# A tiny iptables: rules are lines "CHAIN<TAB>spec" in $FAKE_IPT_DB, chains
# are lines "#CHAIN". Enough of -N -F -X -A -I -C -D -S -L for the script.
FAKE_IPTABLES = r'''#!/usr/bin/env bash
db=$FAKE_IPT_DB
touch "$db"
args=()
for a in "$@"; do [ "$a" = -w ] || args+=("$a"); done
op=${args[0]}; chain=${args[1]:-}
rest=("${args[@]:2}")
case $op in -I) rest=("${rest[@]:1}") ;; esac
spec="${rest[*]}"
has_chain() { grep -qxF "#$1" "$db" || [ "$1" = INPUT ] || [ "$1" = FORWARD ]; }
case $op in
  -N) has_chain "$chain" && exit 1; echo "#$chain" >>"$db" ;;
  -F) has_chain "$chain" || exit 1; grep -v -P "^\Q$chain\E\t" "$db" >"$db.t"; mv "$db.t" "$db" ;;
  -X) grep -vxF "#$chain" "$db" >"$db.t"; mv "$db.t" "$db" ;;
  -A|-I) has_chain "$chain" || exit 1; printf '%s\t%s\n' "$chain" "$spec" >>"$db" ;;
  -C) grep -qxF "$(printf '%s\t%s' "$chain" "$spec")" "$db" ;;
  -D) line=$(printf '%s\t%s' "$chain" "$spec")
      grep -qxF "$line" "$db" || exit 1
      awk -v l="$line" 'BEGIN{d=0} { if (!d && $0==l) {d=1; next} print }' "$db" >"$db.t"; mv "$db.t" "$db" ;;
  -S) has_chain "$chain" || exit 1; grep -P "^\Q$chain\E\t" "$db" || true ;;
  -L) exit 0 ;;
  *) exit 2 ;;
esac
'''


@pytest.fixture
def fake(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    ipt = bindir / "iptables"
    ipt.write_text(FAKE_IPTABLES, encoding="utf-8", newline="\n")
    ipt.chmod(0o755)
    db = tmp_path / "rules"
    state = tmp_path / "state"
    env = {**os.environ, "PATH": f"{bindir}:{os.environ.get('PATH', '')}",
           "FAKE_IPT_DB": str(db), "FH_FIREWALL_STATE_DIR": str(state),
           "IP6TABLES": "ip6tables-not-installed", "FH_FIREWALL_ROUNDS": "1", "FH_FIREWALL_EVERY": "0"}

    def run(*args):
        return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)

    def rules():
        return db.read_text(encoding="utf-8").splitlines() if db.exists() else []

    return run, rules, state, db


@posix_bash
def test_apply_fences_the_network_and_writes_the_state_file(fake) -> None:
    run, rules, state, _db = fake
    assert run("check").returncode == 1
    assert run("apply").returncode == 0
    lines = rules()
    assert "INPUT\t-i domovoi-fh0 -j DROP" in lines
    assert "DOCKER-USER\t-s 172.31.254.0/24 -i domovoi-fh0 -j DOMOVOI-FH" in lines
    # FORWARD too: Docker's nftables backend hooks no DOCKER-USER (R6 FH-14).
    assert "FORWARD\t-s 172.31.254.0/24 -i domovoi-fh0 -j DOMOVOI-FH" in lines
    for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16"):
        assert f"DOMOVOI-FH\t-d {net} -j DROP" in lines
    chain = [line for line in lines if line.startswith("DOMOVOI-FH\t")]
    assert chain[-1] == "DOMOVOI-FH\t-j RETURN"  # drops first, then back to Docker's rules
    assert run("check").returncode == 0
    text = (state / "applied").read_text()
    assert "subnet=172.31.254.0/24" in text and "bridge=domovoi-fh0" in text
    assert "checked=" in text


@posix_bash
def test_watch_puts_back_what_another_tool_flushed(fake) -> None:
    # R6 FH-14: the state file was written once and never refreshed, so a
    # flush by another tool still read "in place" on the page.
    run, rules, state, db = fake
    run("apply")
    kept = [line for line in rules() if not line.startswith("FORWARD" + chr(9))]
    db.write_text("".join(line + chr(10) for line in kept))
    assert run("check").returncode == 1
    assert run("watch").returncode == 0  # one round in tests (FH_FIREWALL_ROUNDS)
    assert run("check").returncode == 0


@posix_bash
def test_apply_twice_changes_nothing(fake) -> None:
    run, rules, _state, _db = fake
    run("apply")
    first = sorted(rules())
    run("apply")
    assert sorted(rules()) == first


@posix_bash
def test_a_docker_restart_flush_is_repaired_by_the_next_apply(fake) -> None:
    run, rules, _state, db = fake
    run("apply")
    # What Docker does to its own chain on a restart: emptied.
    kept = [line for line in rules() if not line.startswith("DOCKER-USER" + chr(9))]
    db.write_text("".join(line + chr(10) for line in kept))
    assert run("check").returncode == 1
    run("apply")
    assert run("check").returncode == 0


@posix_bash
def test_remove_takes_everything_out(fake) -> None:
    run, rules, state, _db = fake
    run("apply")
    assert run("remove").returncode == 0
    assert [line for line in rules() if "domovoi-fh0" in line or line.startswith("DOMOVOI-FH")] == []
    assert not (state / "applied").exists()
    assert run("check").returncode == 1


def test_usage() -> None:
    if sys.platform == "win32" or shutil.which("bash") is None:
        pytest.skip("bash")
    out = subprocess.run(["bash", str(SCRIPT), "sideways"], capture_output=True, text=True)
    assert out.returncode == 2 and "usage" in out.stderr


# ─── what the core reads ─────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="the core reports 'unknown' off Linux")
def test_the_core_reads_the_state_file(tmp_path, monkeypatch) -> None:
    from domovoi.friend_houses import service as svc

    path = tmp_path / "applied"
    monkeypatch.setattr(svc, "FIREWALL_STATE", path)
    assert svc.firewall_state() == "missing"
    import time as _time

    path.write_text("version=1\nsubnet=172.31.254.0/24\nbridge=domovoi-fh0\n")
    assert svc.firewall_state() == "missing"  # no watcher's time: nobody keeps it true
    path.write_text(f"version=1\nsubnet=172.31.254.0/24\nbridge=domovoi-fh0\nchecked={int(_time.time())}\n")
    assert svc.firewall_state() == "in_place"
    path.write_text(f"version=1\nsubnet=172.31.254.0/24\nbridge=domovoi-fh0\nchecked={int(_time.time()) - 3600}\n")
    assert svc.firewall_state() == "missing"  # stale (R6 FH-14)
    path.write_text("version=1\nsubnet=10.9.9.0/24\nbridge=other\n")
    assert svc.firewall_state() == "missing"  # not this network's rules
    assert svc.status()["firewall"] == "missing"


def test_the_subnet_and_bridge_agree_everywhere() -> None:
    from domovoi.friend_houses import tor_runtime as rt

    script = SCRIPT.read_text(encoding="utf-8")
    assert f"SUBNET={rt.NETWORK_SUBNET}" in script
    assert f"BRIDGE={rt.NETWORK_BRIDGE}" in script
    unit = (SCRIPT.parent / "units" / "domovoi-fh-firewall.service").read_text(encoding="utf-8")
    assert "ExecStartPre=/usr/local/lib/domovoi-fh-firewall/fh-firewall.sh apply" in unit
    assert "ExecStart=/usr/local/lib/domovoi-fh-firewall/fh-firewall.sh watch" in unit
    assert "Before=docker.service" in unit and "After=docker.service" not in unit  # R6 FH-14
    assert "PartOf=docker.service" in unit and "WantedBy=docker.service" in unit
