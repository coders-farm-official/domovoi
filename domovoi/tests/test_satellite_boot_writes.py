"""Stage 1, stage 2 and the root helpers write the card's FAT boot
partition only through boot_put (templates/boot-write.sh.tmpl).

2026-10-23: a card pulled after a failed Wi-Fi join held a
``domovoi/device-info.json`` Windows called "corrupted and unreadable".
Stage 1 rewrote that file in place (``json.dump(doc, open(p, "w"))``)
fifteen-odd times a first boot with nothing synced, and rewrote
cmdline.txt and appended to config.txt the same way - a torn copy of
either is a Pi that does not boot. The Python side of the same rule is
satellite/boot_write.py, guarded by satellite/tests/test_boot_write.py.

No DB, no hardware; the shell tests need a bash and skip without one.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from domovoi.satellite_media import overlay

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = overlay.TEMPLATES_DIR
SCRIPTS = REPO / "satellite" / "scripts"
HELPERS = overlay.boot_write_helpers()


# ─── the helpers are in both scripts, the same ────────────────────────────


def _render_both() -> dict[str, str]:
    return {
        "firstrun": overlay.render_firstrun("domo", "respeaker_2mic_hat_v2", "voice", "portal"),
        "stage2": overlay.render_stage2("domo"),
    }


def test_both_stages_carry_the_same_helpers_and_define_them_before_use():
    first_use = {"firstrun": "info bootstrapping stage1-start",
                 "stage2": 'boot_info "$BOOT/domovoi/device-info.json" active'}
    for name, body in _render_both().items():
        assert "@BOOT_WRITE@" not in body, name
        assert body.count(HELPERS) == 1, name
        assert body.index(HELPERS) < body.index(first_use[name]), name


# ─── the guard ────────────────────────────────────────────────────────────

# Names that hold a boot-partition path in these scripts.
_BOOT_TOKENS = ("$BOOT", "${BOOT", "/boot/firmware", "/boot/", '"$CMD"', "$OVL")
_SAFE = ("boot_put", "boot_copy", "boot_info")
_WRITE_CMDS = {"cp", "mv", "install", "ln", "rsync"}       # last argument is written
_ANY_ARG_CMDS = {"tee", "touch", "truncate"}               # every argument is written
_PY_WRITE = re.compile(r"""open\([^)]*,\s*["'][^"']*[wax+]|os\.replace\(|\.write_(text|bytes)\(|json\.dump\(""")


def _boot(word: str) -> bool:
    return any(t in word for t in _BOOT_TOKENS) or any(
        t.strip('"') in word for t in _BOOT_TOKENS)


def shell_violations(text: str, name: str) -> list[str]:
    out: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        n, line = i + 1, lines[i]
        i += 1
        if line.lstrip().startswith("#"):
            continue
        code = line
        if any(s in code for s in _SAFE):
            continue
        # A Python heredoc handed a boot path: its body may not write files.
        m = re.search(r"python3?\b.*<<-?\s*'?(\w+)'?", code)
        if m and _boot(code):
            end = m.group(1)
            while i < len(lines) and lines[i].strip() != end:
                if _PY_WRITE.search(lines[i]):
                    out.append(f"{name}:{i + 1} python heredoc writes a boot file")
                i += 1
            continue
        for r in re.finditer(r"(?<![0-9&<>])>>?\s*(\"[^\"]*\"|'[^']*'|[^\s;|&)]+)", code):
            if _boot(r.group(1)) and "/dev/null" not in r.group(1):
                out.append(f"{name}:{n} redirect into {r.group(1)}")
        for seg in re.split(r"\|\||&&|;|\||\(|\)|\{|\}", code):
            try:
                words = shlex.split(seg)
            except ValueError:
                words = seg.split()
            if not words:
                continue
            # Options, redirections and a line continuation are not operands.
            cmd, args = words[0], [w for w in words[1:]
                                   if not w.startswith("-") and w != "\\"
                                   and not re.match(r"^\d*>", w)]
            if cmd in _WRITE_CMDS and args and _boot(args[-1]):
                out.append(f"{name}:{n} {cmd} onto {args[-1]}")
            elif cmd in _ANY_ARG_CMDS and any(_boot(a) for a in args):
                out.append(f"{name}:{n} {cmd} on the boot partition")
            elif cmd == "sed" and "-i" in words and any(_boot(a) for a in args):
                out.append(f"{name}:{n} sed -i on the boot partition")
            elif cmd == "dd" and any(a.startswith("of=") and _boot(a) for a in args):
                out.append(f"{name}:{n} dd onto the boot partition")
    return out


def _shell_files() -> list[Path]:
    files = [p for p in sorted(TEMPLATES.glob("*.tmpl")) if p.name != "boot-write.sh.tmpl"]
    for p in sorted(SCRIPTS.iterdir()):
        if p.is_file() and p.suffix not in (".ps1", ".py", ".service"):
            head = p.read_text(encoding="utf-8", errors="replace")[:64]
            if head.startswith("#!") and "python" not in head.splitlines()[0]:
                files.append(p)
    return files


def test_no_script_writes_the_boot_partition_but_through_boot_put():
    files = _shell_files()
    assert any(p.name == "firstrun.sh.tmpl" for p in files)
    assert any(p.name == "domovoi-wifi-adapt" for p in files)
    offenders: list[str] = []
    for p in files:
        offenders += shell_violations(p.read_text(encoding="utf-8"), p.name)
    assert offenders == [], (
        "write to the boot partition (FAT, no journal) through boot_put / boot_copy / "
        "boot_info (templates/boot-write.sh.tmpl): " + "; ".join(offenders))


def test_the_guard_catches_the_writes_it_replaced():
    old = r'''
info() {
  python3 - "$BOOT/domovoi/device-info.json" "$1" "$2" "${3:-}" <<'EOF' || true
import json, sys
doc = {}
json.dump(doc, open(p, "w"), indent=2)
EOF
}
        cp "$PAYDIR"/dtbo/*.dtbo "$BOOT/overlays/" 2>/dev/null \
          || cp "$PAYDIR"/dtbo/*.dtbo /boot/firmware/overlays/ 2>/dev/null || true
            "$(basename x .dtbo)" >>"$BOOT/config.txt"
    [ -n "$NEW" ] && printf '%s\n' "$NEW" >"$CMD"
    sed -i 's/a/b/' /boot/firmware/config.txt
'''
    found = shell_violations(old, "old")
    assert len(found) == 6, found
    fine = r'''
  if [ -f "$BOOT/domovoi/ap.json" ]; then
    cp "$BOOT/domovoi/ap.json" "$HOME_DIR/.domovoi/ap.json"
  (cd "$BOOT/domovoi" && sha256sum -c payload.sha256) 2>/dev/null
  tar -xzf "$BOOT/domovoi/payload.tar.gz" -C "$PAYDIR"
    [ -n "$NEW" ] && printf '%s\n' "$NEW" | boot_put "$CMD"
'''
    assert shell_violations(fine, "fine") == []


# ─── the helpers, run ─────────────────────────────────────────────────────


@pytest.fixture
def sh(tmp_path):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash available to run the helpers")

    def run(body: str, *, mv: str | None = None) -> subprocess.CompletedProcess:
        script = tmp_path / "run.sh"
        # python3 is whatever runs these tests: on Windows the name is often
        # the Store's stub.
        py = Path(sys.executable).as_posix()
        prelude = f'python3() {{ "{py}" "$@"; }}\n'
        if mv:
            prelude += f"mv() {{ {mv}; }}\n"     # a function beats PATH
        script.write_text(prelude + HELPERS + "\n" + body + "\n", encoding="utf-8",
                          newline="\n")
        return subprocess.run([bash, str(script)], capture_output=True, text=True,
                              encoding="utf-8")

    return run


OLD = b"console=tty1 root=PARTUUID=ab-02\n"


def test_boot_put_writes_and_leaves_no_temporary(tmp_path, sh):
    target = tmp_path / "config.txt"
    target.write_bytes(b"dtparam=audio=on\n")
    t = target.as_posix()
    r = sh(f'{{ cat "{t}"; echo dtparam=spi=on; }} | boot_put "{t}"')
    assert r.returncode == 0, r.stderr
    assert target.read_bytes().splitlines() == [b"dtparam=audio=on", b"dtparam=spi=on"]
    assert not (tmp_path / "config.txt.tmp").exists()


def test_boot_put_leaves_an_unchanged_file_alone(tmp_path, sh):
    target = tmp_path / "cmdline.txt"
    target.write_bytes(OLD)
    before = target.stat().st_mtime_ns
    # An mv that fails proves nothing was renamed.
    r = sh(f'echo console=tty1 root=PARTUUID=ab-02 | boot_put "{target.as_posix()}" && echo same',
           mv="return 9")
    assert r.returncode == 0 and "same" in r.stdout, r.stderr
    assert target.stat().st_mtime_ns == before
    assert not (tmp_path / "cmdline.txt.tmp").exists()


def test_boot_put_refuses_an_empty_file(tmp_path, sh):
    target = tmp_path / "cmdline.txt"
    target.write_bytes(OLD)
    r = sh(f'false | boot_put "{target.as_posix()}"; echo "rc=$?"')
    assert "rc=1" in r.stdout
    assert target.read_bytes() == OLD
    assert not (tmp_path / "cmdline.txt.tmp").exists()


def test_a_failed_rename_leaves_the_old_file_and_no_temporary(tmp_path, sh):
    target = tmp_path / "cmdline.txt"
    target.write_bytes(OLD)
    r = sh(f'echo garbage | boot_put "{target.as_posix()}"; echo "rc=$?"', mv="return 9")
    assert "rc=1" in r.stdout, r.stdout + r.stderr
    assert target.read_bytes() == OLD
    assert not (tmp_path / "cmdline.txt.tmp").exists()


def test_a_crash_between_the_write_and_the_rename_leaves_the_old_file(tmp_path, sh):
    """The power cut: the shell dies after the temporary is written and
    synced, before the rename. The target is untouched, and the stray
    temporary is simply overwritten by the next write."""
    target = tmp_path / "cmdline.txt"
    target.write_bytes(OLD)
    t = target.as_posix()
    r = sh(f'echo new | boot_put "{t}"; echo survived', mv="kill -9 $$")
    assert "survived" not in r.stdout
    assert target.read_bytes() == OLD
    assert (tmp_path / "cmdline.txt.tmp").read_bytes().strip() == b"new"
    r = sh(f'echo newer | boot_put "{t}"')
    assert r.returncode == 0, r.stderr
    assert target.read_bytes().strip() == b"newer"
    assert not (tmp_path / "cmdline.txt.tmp").exists()


def test_boot_info_updates_the_report_and_keeps_the_rest(tmp_path, sh):
    target = tmp_path / "device-info.json"
    target.write_text(json.dumps({"domovoi_setup": 1, "nonce": "n1", "mac": "aa"}),
                      encoding="utf-8")
    r = sh(f'boot_info "{target.as_posix()}" bootstrapping python-venv "pip failed — retry"\n')
    assert r.returncode == 0, r.stderr
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert doc == {"domovoi_setup": 1, "nonce": "n1", "mac": "aa", "status": "bootstrapping",
                   "step": "python-venv", "error": "pip failed — retry"}
    r = sh(f'boot_info "{target.as_posix()}" active\n')
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert (doc["status"], doc["step"], doc["error"], doc["nonce"]) == ("active", None, None, "n1")


@pytest.mark.parametrize("raw", [b'{"domovoi_setup": 1, "status": "bootstr', b"\x00" * 726, b"[]"])
def test_boot_info_starts_over_from_a_damaged_report(tmp_path, sh, raw):
    target = tmp_path / "device-info.json"
    target.write_bytes(raw)
    r = sh(f'boot_info "{target.as_posix()}" bootstrapping extract\n')
    assert r.returncode == 0, r.stderr
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert doc["domovoi_setup"] == 1 and doc["step"] == "extract"


def test_the_rendered_cmdline_cleanup_still_strips_only_our_tokens(tmp_path, sh):
    """The real lines from the rendered stage 1, run against a realistic
    cmdline, now through boot_put."""
    body = overlay.render_firstrun("domo", "xvf3800_usb", "voice", "usb")
    start = body.index('  for CMD in "$BOOT/cmdline.txt"')
    loop = body[start:body.index("  done\n", start) + len("  done\n")]
    cmd = tmp_path / "cmdline.txt"
    cmd.write_text(
        "console=serial0,115200 console=tty1 root=PARTUUID=abcd-02 rootfstype=ext4 "
        "fsck.repair=yes rootwait modules-load=dwc2 "
        "systemd.run=/boot/firmware/domovoi/firstrun.sh "
        "systemd.run_success_action=reboot systemd.unit=kernel-command-line.target\n",
        encoding="utf-8", newline="\n",
    )
    r = sh(f'BOOT="{tmp_path.as_posix()}"\n' + loop)
    assert r.returncode == 0, r.stderr
    out = cmd.read_text(encoding="utf-8")
    assert "systemd." not in out
    assert out.count("\n") == 1 and out.endswith("\n")
    assert "modules-load=dwc2" in out and "root=PARTUUID=abcd-02" in out


# ─── the server reads a damaged report without falling over ───────────────


@pytest.mark.parametrize("raw", [
    b'{"domovoi_setup": 1, "nonce": "ab', b"\x00" * 726, b"\xff\xfe\x00garbage", b"",
])
def test_the_adoption_scan_survives_a_damaged_device_info(tmp_path, monkeypatch, raw):
    from web.backend import satellite_adoption as adoption

    vol = tmp_path / "card"
    vol.mkdir()
    (vol / "device-info.json").write_bytes(raw)
    monkeypatch.setenv("SATELLITE_ADOPTION_SCAN_DIRS", str(vol))
    adoption.invalidate_cache()
    try:
        assert adoption.scan_pending() == []       # no device, no exception
    finally:
        adoption.invalidate_cache()
