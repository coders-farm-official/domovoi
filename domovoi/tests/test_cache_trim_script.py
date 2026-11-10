"""The file-cache trim's logic (scripts/linux/cache-trim.py, 2026-11-10).

Inside the Windows installer's WSL VM, old file reads filled 51 GB of a
63 GB VM; the root-owned trim drops the clean page cache above a limit.
These cover the parts that decide anything: the WSL rule, the default
limit, the operator file's validation (root's alone, strict keys, bounds,
never a link), the decision, and one whole run against a fake /proc.

No DB, no root: the script takes a fake /proc and status path.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "linux" / "cache-trim.py"
GIB = 1024 ** 3

_spec = importlib.util.spec_from_file_location("domovoi_cache_trim_script", SCRIPT)
ct = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(ct)


def meminfo(total_gb: float, cached_gb: float, *, buffers_gb=0.0, shmem_gb=0.0, mapped_gb=0.0) -> str:
    kb = lambda g: int(g * 1024 * 1024)  # noqa: E731
    return (
        f"MemTotal:       {kb(total_gb)} kB\nMemFree:        1000 kB\n"
        f"Buffers:        {kb(buffers_gb)} kB\nCached:         {kb(cached_gb)} kB\n"
        f"Shmem:          {kb(shmem_gb)} kB\nMapped:         {kb(mapped_gb)} kB\nHugePages_Total:       0\n"
    )


# ─── what the cache is ───────────────────────────────────────────────────


def test_cache_is_the_health_rows_figure_and_mapped_pages_cannot_be_freed() -> None:
    f = ct.cache_figures(ct.parse_meminfo(meminfo(63, 50, buffers_gb=2, shmem_gb=1, mapped_gb=9)))
    assert f["total"] == 63 * GIB
    assert f["cache"] == 51 * GIB              # Cached + Buffers - Shmem
    assert f["droppable"] == 42 * GIB          # less what programs have mapped


def test_figures_never_go_negative() -> None:
    f = ct.cache_figures(ct.parse_meminfo(meminfo(8, 1, shmem_gb=2, mapped_gb=5)))
    assert f["cache"] == 0 and f["droppable"] == 0


# ─── WSL ──────────────────────────────────────────────────────────────────


def _proc(tmp_path: Path, *, osrelease: str | None, version: str | None = None) -> Path:
    proc = tmp_path / "proc"
    (proc / "sys" / "kernel").mkdir(parents=True, exist_ok=True)
    if osrelease is not None:
        (proc / "sys" / "kernel" / "osrelease").write_text(osrelease)
    if version is not None:
        (proc / "version").write_text(version)
    return proc


@pytest.mark.parametrize(("osrelease", "want"), [
    ("6.6.87.2-microsoft-standard-WSL2\n", True),
    ("4.4.0-19041-Microsoft\n", True),
    ("6.8.0-48-generic\n", False),
])
def test_wsl_is_told_by_the_kernel(tmp_path, osrelease, want) -> None:
    assert ct.is_wsl(str(_proc(tmp_path, osrelease=osrelease))) is want


def test_proc_version_is_only_the_fallback(tmp_path) -> None:
    # The builder's name in /proc/version must not count while osrelease answers.
    proc = _proc(tmp_path, osrelease="6.8.0-generic\n", version="Linux ... (builder@microsoft.example) ...")
    assert ct.is_wsl(str(proc)) is False
    proc2 = _proc(tmp_path / "b", osrelease=None, version="Linux version 5.15.153.1-microsoft-standard-WSL2")
    assert ct.is_wsl(str(proc2)) is True
    assert ct.is_wsl(str(tmp_path / "nowhere")) is False


# ─── the default limit ────────────────────────────────────────────────────


@pytest.mark.parametrize(("total_gb", "limit_gb"), [(10, 4), (16, 4), (32, 4), (40, 4), (63, 6.3), (128, 12.8)])
def test_default_limit_is_4_gib_or_a_tenth(total_gb, limit_gb) -> None:
    assert ct.default_limit(int(total_gb * GIB)) == pytest.approx(limit_gb * GIB, rel=1e-6)


# ─── the operator file ────────────────────────────────────────────────────


@pytest.mark.parametrize(("text", "want"), [
    ("", {}),
    ("# nothing\n\n", {}),
    ("enabled=on\n", {"enabled": "on"}),
    ("enabled = off   # not here\nlimit_gb=12\n", {"enabled": "off", "limit_gb": 12}),
    ("limit_gb=1\n", {"limit_gb": 1}),
    ("limit_gb=1024\n", {"limit_gb": 1024}),
    ("enabled=auto\n", {"enabled": "auto"}),
])
def test_override_accepts_only_its_two_keys(text, want) -> None:
    assert ct.parse_override(text) == (want, None)


@pytest.mark.parametrize("text", [
    "enabled=yes\n", "enabled=on\nenabled=off\n", "limit_gb=0\n", "limit_gb=1025\n",
    "limit_gb=-3\n", "limit_gb=8.5\n", "limit_gb=8GB\n", "limit_gb=$(reboot)\n", "limit_gb=\n",
    "limit_gb=00000008\n", "LIMIT_GB=8\n", "limit=8\n", "just words\n", "limit_gb=٨\n",
])
def test_override_anything_wrong_makes_the_whole_file_absent(text) -> None:
    got, why = ct.parse_override(text)
    assert got == {} and why


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes and links")
def test_override_must_be_roots_alone_and_never_a_link(tmp_path) -> None:
    me = (0, os.geteuid())
    d = tmp_path / "etc"
    d.mkdir(mode=0o755)
    f = d / "cache-trim.conf"
    f.write_text("limit_gb=8\n")
    f.chmod(0o644)
    assert ct.read_override(str(f), me) == ({"limit_gb": 8}, None)
    # Somebody else can write it (or its folder): not believed.
    f.chmod(0o664)
    assert ct.read_override(str(f), me)[1]
    f.chmod(0o644)
    d.chmod(0o777)
    assert ct.read_override(str(f), me)[1]
    d.chmod(0o755)
    # Owned by someone who isn't root (here: not the stand-in uid either).
    assert ct.read_override(str(f), (0,))[1] if os.geteuid() != 0 else True
    # A link is never followed.
    secret = tmp_path / "secret"
    secret.write_text("limit_gb=2\n")
    link = d / "link.conf"
    link.symlink_to(secret)
    got, why = ct.read_override(str(link), me)
    assert got == {} and why
    # A FIFO doesn't block it.
    fifo = d / "fifo.conf"
    os.mkfifo(fifo)
    got, why = ct.read_override(str(fifo), me)
    assert got == {} and "plain file" in why
    # Too big.
    f.write_text("#" * 2000 + "\n")
    assert "larger" in ct.read_override(str(f), me)[1]
    # None at all: the defaults, with nothing to say.
    assert ct.read_override(str(d / "missing.conf"), me) == ({}, None)


# ─── the decision ─────────────────────────────────────────────────────────


def _decide(*, total=63, cache=51, droppable=None, wsl=True, override=None, update=False,
            last=None, now=10_000.0):
    figures = {"total": int(total * GIB), "cache": int(cache * GIB),
               "droppable": int((cache if droppable is None else droppable) * GIB)}
    return ct.decide(figures=figures, wsl=wsl, override=override or {}, update_running=update,
                     last_trim_at=last, now=now)


def test_the_measured_house_is_trimmed() -> None:
    d = _decide()
    assert d["enabled"] is True and d["why_enabled"] == "wsl"
    assert d["limit"] == pytest.approx(6.3 * GIB, rel=1e-6) and d["limit_source"] == "default"
    assert d["action"] == "trim"


def test_off_on_a_dedicated_linux_box_unless_the_operator_says_on() -> None:
    assert _decide(wsl=False)["action"] == "off"
    d = _decide(wsl=False, override={"enabled": "on"})
    assert d["action"] == "trim" and d["why_enabled"] == "operator"
    assert _decide(override={"enabled": "off"})["action"] == "off"


def test_the_operator_limit_wins() -> None:
    d = _decide(override={"limit_gb": 60})
    assert d["limit"] == 60 * GIB and d["limit_source"] == "operator" and d["action"] == "under"


def test_mapped_pages_never_trigger_a_trim() -> None:
    # 12 GB of cache, 9 of it an AI model in use: only 3 could go, under 6.3.
    assert _decide(cache=12, droppable=3)["action"] == "under"


def test_never_during_an_update_or_restart() -> None:
    d = _decide(update=True)
    assert d["action"] == "skip" and "update" in d["why"]


def test_at_most_once_per_interval() -> None:
    assert _decide(last=10_000 - 30)["action"] == "skip"
    assert _decide(last=10_000 - 130)["action"] == "trim"
    # A clock that went back doesn't block it forever.
    assert _decide(last=10_000 + 5000)["action"] == "trim"


def test_too_little_to_free_is_not_a_trim() -> None:
    assert _decide(override={"limit_gb": 1}, cache=1.3, droppable=0.25)["action"] == "under"


# ─── one whole run ───────────────────────────────────────────────────────


def _run(tmp_path: Path, monkeypatch, before: str, after: str | None = None, *, wsl=True,
         systemctl_state="inactive", dry_run=False, now=1_800_000_000.0):
    proc = _proc(tmp_path, osrelease="6.6.87.2-microsoft-standard-WSL2" if wsl else "6.8.0-generic")
    (proc / "meminfo").write_text(before)
    status = tmp_path / "state" / "status.json"
    dropped: list[bool] = []

    def drop(proc_root: str) -> None:
        dropped.append(True)
        if after is not None:
            (proc / "meminfo").write_text(after)

    monkeypatch.setattr(ct, "drop_page_cache", drop)
    monkeypatch.setattr(ct, "update_running", lambda systemctl: systemctl_state == "activating")
    rc = ct.run(proc_root=str(proc), status_path=str(status), override_path=str(tmp_path / "none.conf"),
                root_uids=(0,), systemctl="systemctl", dry_run=dry_run, clock=lambda: now)
    doc = json.loads(status.read_text()) if status.exists() else None
    return rc, doc, dropped


def test_a_run_trims_logs_and_records_before_and_after(tmp_path, monkeypatch, capsys) -> None:
    rc, doc, dropped = _run(tmp_path, monkeypatch, meminfo(63, 51), meminfo(63, 1.2))
    assert rc == 0 and dropped == [True]
    assert doc["action"] == "trim" and doc["enabled"] is True
    t = doc["last_trim"]
    assert t["before_bytes"] == 51 * GIB
    assert t["after_bytes"] == pytest.approx(1.2 * GIB, rel=1e-3)
    assert t["freed_bytes"] == pytest.approx(49.8 * GIB, rel=1e-3)
    assert t["at"] == "2027-01-15T08:00:00Z"
    out = capsys.readouterr().out
    assert "trimmed to 1.2 GB" in out and "freed 50 GB" in out


def test_a_second_run_keeps_the_last_trim_and_waits(tmp_path, monkeypatch) -> None:
    _run(tmp_path, monkeypatch, meminfo(63, 51), meminfo(63, 1.2))
    rc, doc, dropped = _run(tmp_path, monkeypatch, meminfo(63, 30), now=1_800_000_030.0)
    assert dropped == [] and doc["action"] == "skip"
    assert doc["last_trim"]["before_bytes"] == 51 * GIB


def test_a_run_on_plain_linux_records_and_does_nothing(tmp_path, monkeypatch) -> None:
    rc, doc, dropped = _run(tmp_path, monkeypatch, meminfo(63, 51), wsl=False)
    assert rc == 0 and dropped == [] and doc["action"] == "off" and doc["enabled"] is False
    assert doc["last_trim"] is None


def test_a_run_skips_while_the_update_unit_runs(tmp_path, monkeypatch) -> None:
    rc, doc, dropped = _run(tmp_path, monkeypatch, meminfo(63, 51), systemctl_state="activating")
    assert dropped == [] and doc["action"] == "skip"


def test_a_dry_run_changes_nothing(tmp_path, monkeypatch) -> None:
    rc, doc, dropped = _run(tmp_path, monkeypatch, meminfo(63, 51), dry_run=True)
    assert rc == 0 and dropped == [] and doc is None


def test_the_unit_runs_roots_copy_in_isolated_mode() -> None:
    unit = (REPO_ROOT / "scripts" / "linux" / "units" / "domovoi-cache-trim.service").read_text()
    assert "ExecStart=/usr/bin/python3 -I -S /usr/local/lib/domovoi-cache-trim/cache-trim.py\n" in unit
    assert "Type=oneshot" in unit and "StateDirectory=domovoi-cache-trim" in unit
    assert "User=" not in unit
    timer = (REPO_ROOT / "scripts" / "linux" / "units" / "domovoi-cache-trim.timer").read_text()
    assert "OnUnitActiveSec=2min" in timer and "WantedBy=timers.target" in timer


def test_the_script_uses_the_standard_library_only() -> None:
    # Run with -I -S: no site-packages, so no third-party import may sneak in.
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert names <= {"__future__", "argparse", "json", "os", "stat", "subprocess", "sys", "time"}, names
