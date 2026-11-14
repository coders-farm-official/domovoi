"""How much of the PC Domovoi may use (domovoi/host_limits.py, 2026-11-10).

The Windows install's ceiling: DomovoiHost reports the PC and the limits
into a root-owned file; the core writes a bounded request for it. Covers
the report's trust rule and field checks, the request's bounds and shape,
the "still needs a restart" reading, and both HTTP hops' tiers.

No DB.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from domovoi import host_limits as hl

REPORT = {
    "schema": 1, "ram_gb": 128, "logical_processors": 32,
    "memory_gb": {"min": 8, "max": 124, "default": 32, "chosen": None, "in_file": 32},
    "processors": {"min": 2, "max": 32, "default": 32, "chosen": None, "in_file": None},
    "request": "none", "request_problem": None, "restart": "none",
    "written_at": "2026-11-10T05:00:00Z",
}


@pytest.fixture
def host(tmp_path, monkeypatch):
    report = tmp_path / "var" / "lib" / "domovoi-windows" / "limits.json"
    report.parent.mkdir(parents=True)
    req_dir = tmp_path / "home" / ".domovoi" / "hardware_limits"
    monkeypatch.setenv("DOMOVOI_HOST_LIMITS_REPORT", str(report))
    monkeypatch.setenv("DOMOVOI_HOST_LIMITS_DIR", str(req_dir))
    monkeypatch.setattr(hl, "_root_only", lambda st: True)

    def write(doc=REPORT) -> None:
        report.write_text(json.dumps(doc) if isinstance(doc, dict) else doc, encoding="utf-8")

    write()
    return {"report": report, "write": write, "req": req_dir / "request.json"}


# ─── the report ──────────────────────────────────────────────────────────


def test_the_report_is_read_and_checked(host) -> None:
    r = hl.read_report()
    assert r["ram_gb"] == 128 and r["logical_processors"] == 32
    assert r["memory_gb"] == {"min": 8, "max": 124, "default": 32, "chosen": None, "in_file": 32}
    assert r["restart"] == "none" and r["reported_at"] == "2026-11-10T05:00:00Z"


def test_no_report_means_not_the_windows_install(host) -> None:
    host["report"].unlink()
    assert hl.read_report() is None
    assert hl.status()["available"] is False


def test_a_report_not_roots_alone_is_not_believed(host, monkeypatch) -> None:
    monkeypatch.setattr(hl, "_root_only", lambda st: False)
    assert hl.read_report() is None


@pytest.mark.parametrize("bad", [
    "not json", "[]", {**REPORT, "schema": 2}, {**REPORT, "ram_gb": "128"},
    {**REPORT, "memory_gb": {"min": 50, "max": 8}}, {**REPORT, "processors": None},
    {**REPORT, "logical_processors": True}, "x" * 9000,
])
def test_a_report_in_another_shape_is_ignored(host, bad) -> None:
    host["write"](bad)
    assert hl.read_report() is None


def test_unknown_words_in_a_report_are_dropped(host) -> None:
    host["write"]({**REPORT, "request": "<b>hi</b>", "restart": "maybe", "request_problem": "y" * 500})
    r = hl.read_report()
    assert r["request"] == "none" and r["restart"] == "none" and r["request_problem"] is None


# ─── the request ─────────────────────────────────────────────────────────


def test_save_writes_the_bounded_request_for_domovoihost(host) -> None:
    out = hl.save(24, 8)
    doc = json.loads(host["req"].read_text(encoding="ascii"))
    assert doc["v"] == 1 and doc["memory_gb"] == 24 and doc["processors"] == 8
    assert doc["restart_id"] is None
    assert set(doc) == {"v", "memory_gb", "processors", "restart_id", "at"}
    assert len(host["req"].read_bytes()) < 2048          # DomovoiHost reads at most 2 KiB
    # DomovoiHost hasn't taken it yet (its report still says automatic).
    assert out["waiting_for_host"] is True


def test_automatic_is_null(host) -> None:
    hl.save(None, None)
    doc = json.loads(host["req"].read_text())
    assert doc["memory_gb"] is None and doc["processors"] is None


def test_restart_now_carries_a_fresh_id_each_time(host) -> None:
    hl.save(24, None, restart_now=True, update_running=lambda: False)
    a = json.loads(host["req"].read_text())["restart_id"]
    hl.save(24, None, restart_now=True, update_running=lambda: False)
    b = json.loads(host["req"].read_text())["restart_id"]
    assert len(a) == 32 and int(a, 16) >= 0 and a != b


@pytest.mark.parametrize(("mem", "cpu", "words"), [
    (7, None, "memory_gb must be 8 to 124"),
    (125, None, "memory_gb must be 8 to 124"),
    (None, 1, "processors must be 2 to 32"),
    (None, 33, "processors must be 2 to 32"),
    (True, None, "whole number"),
    (24.5, None, "whole number"),
])
def test_outside_this_pcs_bounds_is_refused(host, mem, cpu, words) -> None:
    with pytest.raises(hl.Refused, match=words):
        hl.save(mem, cpu)
    assert not host["req"].exists()


def test_no_report_no_request(host) -> None:
    host["report"].unlink()
    with pytest.raises(hl.Refused, match="isn't the Windows install"):
        hl.save(24, None)


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX modes")
def test_the_request_is_the_service_users_alone(host) -> None:
    hl.save(24, None)
    assert host["req"].stat().st_mode & 0o777 == 0o600
    assert host["req"].parent.stat().st_mode & 0o777 == 0o700


# ─── what the VM runs on ─────────────────────────────────────────────────


@pytest.mark.parametrize(("file_gb", "running", "same"), [
    (64, 62.6, True), (32, 31.2, True), (8, 7.6, True), (24, 23.4, True),
    (32, 62.6, False), (24, 31.3, False), (64, 31.3, False), (None, 31.3, True), (32, None, True),
])
def test_memory_matches_a_cap_a_little_under(file_gb, running, same) -> None:
    assert hl.memory_matches(file_gb, running) is same


def test_pending_until_the_vm_runs_on_the_new_size(host) -> None:
    report = {**hl.read_report()}
    report["memory_gb"] = {**report["memory_gb"], "chosen": 24, "in_file": 24}
    report["processors"] = {**report["processors"], "chosen": 8, "in_file": 8}
    s = hl.status(report=report, running={"memory_gb": 62.6, "processors": 32}, request=None)
    assert s["available"] is True and s["pending"] is True
    s = hl.status(report=report, running={"memory_gb": 23.4, "processors": 8}, request=None)
    assert s["pending"] is False
    assert s["warn_below_gb"] == 16 and s["training_gb"] == 24


def test_automatic_processors_are_every_core(host) -> None:
    s = hl.status(report=hl.read_report(), running={"memory_gb": 31.3, "processors": 32}, request=None)
    assert s["pending"] is False


def test_a_restart_is_refused_while_an_update_runs(host) -> None:
    """Review 2026-11-10: a limits restart stops the units, which would cut
    an update (or the dashboard's Restart) off. DomovoiHost checks too."""
    with pytest.raises(hl.Busy, match="update or restart of Domovoi is running"):
        hl.save(24, None, restart_now=True, update_running=lambda: True)
    assert not host["req"].exists()
    # Saving the limits without a restart is fine meanwhile.
    hl.save(24, None, update_running=lambda: True)
    assert json.loads(host["req"].read_text())["restart_id"] is None
    hl.save(24, None, restart_now=True, update_running=lambda: False)
    assert json.loads(host["req"].read_text())["restart_id"]


def test_waiting_clears_when_the_host_read_a_clamped_request(host) -> None:
    """The host clamps (its bounds can be tighter than this report's), so
    the numbers never match; it echoes the request's `at` instead."""
    hl.save(124, None)
    at = json.loads(host["req"].read_text())["at"]
    host["write"]({**REPORT, "memory_gb": {**REPORT["memory_gb"], "chosen": 123}, "request": "clamped",
                   "request_at": at})
    s = hl.status(running={"memory_gb": 31.3, "processors": 32})
    assert s["waiting_for_host"] is False
    host["write"]({**REPORT, "request_at": "2026-01-01T00:00:00Z"})
    assert hl.status(running={"memory_gb": 31.3, "processors": 32})["waiting_for_host"] is True


def test_a_done_restart_id_is_taken_back(host) -> None:
    """Once the VM runs on what .wslconfig says and the host has read the
    request, its restart id goes, so it can never fire later."""
    hl.save(32, None, restart_now=True, update_running=lambda: False)
    doc = json.loads(host["req"].read_text())
    host["write"]({**REPORT, "memory_gb": {**REPORT["memory_gb"], "chosen": 32}, "request_at": doc["at"]})
    hl.status(running={"memory_gb": 31.3, "processors": 32})          # not pending, read: cleared
    after = json.loads(host["req"].read_text())
    assert after["restart_id"] is None and after["at"] == doc["at"] and after["memory_gb"] == 32
    # Still pending (the VM runs on 62 GB): kept.
    hl.save(32, None, restart_now=True, update_running=lambda: False)
    doc = json.loads(host["req"].read_text())
    host["write"]({**REPORT, "memory_gb": {**REPORT["memory_gb"], "chosen": 32}, "request_at": doc["at"]})
    hl.status(running={"memory_gb": 62.6, "processors": 32})
    assert json.loads(host["req"].read_text())["restart_id"] == doc["restart_id"]


def test_the_busy_restart_state_is_read(host) -> None:
    host["write"]({**REPORT, "restart": "busy"})
    assert hl.read_report()["restart"] == "busy"


def test_waiting_for_host_clears_once_it_reports_the_choice(host) -> None:
    hl.save(24, None)
    report = hl.read_report()
    report["memory_gb"] = {**report["memory_gb"], "chosen": 24}
    s = hl.status(report=report, running={"memory_gb": 31.3, "processors": 32})
    assert s["waiting_for_host"] is False


# ─── the HTTP hops ───────────────────────────────────────────────────────


def test_both_hops_are_admin_gated() -> None:
    from domovoi.main import app as core_app
    from web.backend.main import app as web_app

    from domovoi.tests.route_walk import iter_route_contexts

    def deps(app, path, method):
        for r in iter_route_contexts(app.routes):
            if getattr(r, "path", None) == path and method in (getattr(r, "methods", None) or set()):
                return {d.call.__name__ for d in r.dependant.dependencies}
        raise AssertionError(f"no {method} {path}")

    assert "require_admin_read" in deps(core_app, "/v1/admin/host-limits", "GET")
    assert "require_admin_mutation" in deps(core_app, "/v1/admin/host-limits", "POST")
    assert "require_admin_read" in deps(web_app, "/api/models/host-limits", "GET")
    assert "require_admin_mutation" in deps(web_app, "/api/models/host-limits", "POST")


@pytest.mark.asyncio
async def test_the_core_endpoint_refuses_out_of_bounds_with_400(host, monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from domovoi.main import app as core_app
    from domovoi.tests.auth_testkit import bearer, install_fake_db

    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    monkeypatch.setattr(hl, "_update_running", lambda: True)
    async with AsyncClient(transport=ASGITransport(app=core_app, raise_app_exceptions=False),
                           base_url="http://test") as c:
        r = await c.post("/v1/admin/host-limits", json={"memory_gb": 24, "restart_now": True},
                         headers=bearer("admin-token"))
        assert r.status_code == 409 and "update or restart" in r.json()["detail"]
        monkeypatch.setattr(hl, "_update_running", lambda: False)
        r = await c.post("/v1/admin/host-limits", json={"memory_gb": 4}, headers=bearer("admin-token"))
        assert r.status_code == 400 and "8 to 124" in r.json()["detail"]
        r = await c.post("/v1/admin/host-limits", json={"memory_gb": 24, "restart_now": True},
                         headers=bearer("admin-token"))
        assert r.status_code == 200 and r.json()["available"] is True
        r = await c.post("/v1/admin/host-limits", json={"memory_gb": "lots"}, headers=bearer("admin-token"))
        assert r.status_code == 422
        r = await c.get("/v1/admin/host-limits")
        assert r.status_code in (401, 403)
    assert json.loads(host["req"].read_text())["restart_id"]


def test_the_windows_side_reads_the_same_paths() -> None:
    cs = (Path(__file__).resolve().parents[2] / "windows" / "common" / "HardwareLimits.cs").read_text(encoding="utf-8")
    assert '"/.domovoi/hardware_limits/request.json"' in cs and hl.REQUEST_NAME == "request.json"
    assert 'ReportDir + "/limits.json"' in cs and hl.REPORT_FILE == "/var/lib/domovoi-windows/limits.json"
    assert "MaxRequestBytes = 2048" in cs
    assert "MemoryFloorGb = 8" in cs and "ProcessorsFloor = 2" in cs
