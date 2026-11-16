""".github/workflows/osv.yml: OSV-Scanner over the lock files.

Pure file checks, no network, no DB. The scanner binary is pinned by version
and SHA-256 and every action by commit SHA; the token is read-only and there
is no secret; the job scans the lock files it should (every one that exists,
named in the workflow) and fails on a vulnerability; and every accepted
advisory in osv-scanner.toml has a reason and an expiry date that is not too
far away.
"""

from __future__ import annotations

import datetime
import re
import tomllib
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
TEXT = (REPO / ".github" / "workflows" / "osv.yml").read_text(encoding="utf-8")
DOC = yaml.safe_load(TEXT)
CONFIG = tomllib.loads((REPO / "osv-scanner.toml").read_text(encoding="utf-8"))
#: Every file the blocking step has to name.
BLOCKING = [
    "requirements.lock",
    "requirements-linux-py314.lock",
    "requirements-linux-py314-no-deps.lock",
    "satellite/locks/pc-*.txt",
    "scripts/g2p/requirements-train.txt",
    "scripts/wake_word/docker/requirements.txt",
    "windows/installer/art/requirements.txt",
]


def _steps():
    return DOC["jobs"]["scan"]["steps"]


def test_it_runs_on_main_on_changes_daily_and_by_hand():
    on = DOC.get("on", DOC.get(True))  # YAML 1.1 reads a bare `on` as true
    assert on["push"] == {"branches": ["main"]}
    assert on["schedule"] and re.fullmatch(r"\d+ \d+ \* \* \*", on["schedule"][0]["cron"]), "daily"
    assert "workflow_dispatch" in on
    paths = on["pull_request"]["paths"]
    assert ".github/workflows/osv.yml" in paths and "osv-scanner.toml" in paths
    assert "requirements*.lock" in paths and "satellite/locks/**" in paths


def test_token_is_read_only_and_no_secret():
    assert DOC["permissions"] == {"contents": "read"}
    assert DOC["jobs"]["scan"]["permissions"] == {"contents": "read"}
    assert "secrets." not in TEXT and "GITHUB_TOKEN" not in TEXT
    checkout = next(s for s in _steps() if "checkout" in s.get("uses", ""))
    assert checkout["with"]["persist-credentials"] is False


def test_every_action_is_pinned_by_sha():
    refs = re.findall(r"uses:\s*(\S+)\s*(#.*)?", TEXT)
    assert refs
    for ref, comment in refs:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), ref
        assert re.match(r"#\s*v\d+\.\d+\.\d+", comment), ref


def test_the_scanner_binary_is_pinned_by_version_and_sha256():
    env = DOC["env"]
    assert re.fullmatch(r"v\d+\.\d+\.\d+", env["OSV_SCANNER_VERSION"])
    assert re.fullmatch(r"[0-9a-f]{64}", env["OSV_SCANNER_SHA256"].split()[0])
    install = next(s for s in _steps() if "Install" in s.get("name", ""))["run"]
    assert "sha256sum -c -" in install
    assert install.index("sha256sum -c -") < install.index("chmod +x"), "checked before it can run"
    assert "releases/download/${OSV_SCANNER_VERSION}/" in install
    assert "osv-scanner@" not in TEXT and "go install" not in TEXT


def test_the_blocking_scan_names_every_lock_and_fails_on_a_finding():
    scan = next(s for s in _steps() if s.get("name", "").startswith("Scan the lock files"))
    run = scan["run"]
    for f in BLOCKING:
        assert f in run, f
    assert "--config osv-scanner.toml" in run and "--no-resolve" in run
    assert "|| true" not in run and not scan.get("continue-on-error")
    assert scan.get("if") is None


def test_what_the_scan_names_exists():
    for f in BLOCKING:
        assert list(REPO.glob(f)), f
    for f in ("android/gradle/verification-metadata.xml", "scripts/wake_word/docker/constraints.txt"):
        assert (REPO / f).is_file(), f


def test_the_android_and_constraints_report_does_not_fail_the_job():
    info = next(s for s in _steps() if s.get("name", "").startswith("Report"))
    assert info["continue-on-error"] is True
    assert "verification-metadata.xml:android/gradle/verification-metadata.xml" in info["run"]
    assert "constraints.txt" in info["run"] and "GITHUB_STEP_SUMMARY" in info["run"]


def test_every_accepted_vulnerability_has_a_reason_and_a_near_expiry():
    entries = CONFIG.get("IgnoredVulns", [])
    today = datetime.date.today()
    for e in entries:
        assert re.fullmatch(r"(GHSA(-[0-9a-z]{4}){3}|PYSEC-\d{4}-\d+|CVE-\d{4}-\d+)", e["id"]), e
        assert len(e["reason"]) >= 40, e["id"]
        until = e["ignoreUntil"]
        assert isinstance(until, datetime.date), e["id"]
        assert until <= today + datetime.timedelta(days=75), f"{e['id']}: expiry is more than about two months out"
