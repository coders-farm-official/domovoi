"""The CI workflows build from pinned code with the least token they need
(OPS-13).

Pure file checks: no network, never skips.

* Every ``uses:`` names a full commit SHA, with the release it was as a
  comment, so a moved or compromised tag can't change what builds the APK.
* Each workflow declares ``permissions:`` and grants nothing beyond reads.
* Checkout does not leave the token in the workspace's git config.
* The Android README says the CI APK is debug-signed and how to build a
  release-signed one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<ref>\S+)(?P<rest>.*)$")


def _ids(paths):
    return [p.name for p in paths]


def test_there_are_workflows_to_check() -> None:
    assert WORKFLOWS


@pytest.mark.parametrize("path", WORKFLOWS, ids=_ids(WORKFLOWS))
def test_every_action_is_pinned_by_commit_sha_with_its_release(path: Path) -> None:
    found = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        m = USES_LINE.match(line)
        if not m:
            continue
        found += 1
        ref = m.group("ref")
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), f"{path.name}: {line.strip()}"
        assert re.search(r"#\s*v\d+\.\d+\.\d+", m.group("rest")), f"{path.name}: no version comment: {line.strip()}"
    assert found, f"{path.name} uses no actions?"


@pytest.mark.parametrize("path", WORKFLOWS, ids=_ids(WORKFLOWS))
def test_permissions_are_declared_and_read_only(path: Path) -> None:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    perms = doc.get("permissions")
    assert isinstance(perms, dict) and perms, f"{path.name} has no top-level permissions block"
    for job_name, job in doc.get("jobs", {}).items():
        perms_job = job.get("permissions", perms)
        assert isinstance(perms_job, dict), f"{path.name}:{job_name} permissions must be a mapping"
        for scope, level in {**perms, **perms_job}.items():
            assert level in ("read", "none"), f"{path.name}:{job_name} grants {scope}: {level}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=_ids(WORKFLOWS))
def test_checkout_does_not_persist_the_token(path: Path) -> None:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for job in doc.get("jobs", {}).values():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                assert (step.get("with") or {}).get("persist-credentials") is False


def test_android_readme_explains_the_debug_signed_ci_apk() -> None:
    text = (REPO_ROOT / "android" / "README.md").read_text(encoding="utf-8")
    assert "debug-signed" in text
    assert "assembleRelease" in text and "apksigner" in text
