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
    # release.yml starts from nothing at all (`permissions: {}`) and grants
    # per job; every other workflow declares a read-only block at the top.
    if path.name == "release.yml":
        assert perms == {}, "release.yml must grant nothing at the top level"
    elif path.name in ("scorecard.yml", "codeql.yml"):
        # Scorecard's documented minimum: read-all at the top (no write
        # there, which publishing refuses), the job's two writes below.
        assert perms == "read-all", "scorecard.yml reads at the top level and writes nowhere there"
        perms = {}
    else:
        assert isinstance(perms, dict) and perms, f"{path.name} has no top-level permissions block"
    for job_name, job in doc.get("jobs", {}).items():
        perms_job = job.get("permissions", perms)
        assert isinstance(perms_job, dict), f"{path.name}:{job_name} permissions must be a mapping"
        for scope, level in {**perms, **perms_job}.items():
            # The one write anywhere: release.yml's `release` job, which runs
            # in the approval-gated `release` environment, may write contents
            # (tag, release, stable). Nothing else, and nowhere else.
            if (path.name, job_name, scope, level) == ("release.yml", "release", "contents", "write"):
                assert job.get("environment") in ("release", {"name": "release"}), \
                    "release.yml's writing job must run in the release environment"
                continue
            # And release.yml's `attest` job, the build provenance: an OIDC
            # token for Sigstore and the attestation stored. It has no
            # environment and no secret (test_release_provenance.py).
            if (path.name, job_name, scope, level) in (("release.yml", "attest", "id-token", "write"),
                                                       ("release.yml", "attest", "attestations", "write")):
                assert "environment" not in job, "the attest job must stay outside the release environment"
                continue
            # And scorecard.yml's one job: code scanning upload, and the OIDC
            # token scorecard.dev checks a published result against.
            if (path.name, job_name, scope, level) in (("scorecard.yml", "analysis", "security-events", "write"),
                                                       ("scorecard.yml", "analysis", "id-token", "write")):
                assert "environment" not in job
                continue
            # And codeql.yml's one job: the code scanning upload.
            if (path.name, job_name, scope, level) == ("codeql.yml", "analyze", "security-events", "write"):
                assert "environment" not in job
                continue
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


def test_the_ruff_job_lints_with_a_hash_pinned_ruff() -> None:
    """OpenSSF's static-analysis criteria: CI runs a linter that fails on any
    finding, installed from an exact, hash-checked pin, with a read-only token."""
    doc = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = doc["jobs"]["ruff"]
    assert job["permissions"] == {"contents": "read"}
    assert "needs" not in job and "if" not in job, "the lint runs on every push"
    runs = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert "pip install --require-hashes -r .github/ci-ruff-requirements.txt" in runs
    assert re.search(r"^\s*ruff check --no-fix \.\s*$", runs, re.M)
    assert "ruff format" not in runs
    pins = (REPO_ROOT / ".github" / "ci-ruff-requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^ruff==\d+\.\d+\.\d+ \\$", pins, re.M)
    assert re.search(r"--hash=sha256:[0-9a-f]{64}", pins)


def test_pyproject_configures_ruff_without_style_rules() -> None:
    import tomllib

    cfg = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["ruff"]
    selected = cfg["lint"]["select"]
    assert "F" in selected and "E9" in selected
    assert not any(code in ("E", "W", "I", "N", "D", "UP") for code in selected)
    assert "web/static/vendor" in cfg["extend-exclude"]


def _pins(name: str) -> dict[str, str]:
    """``{package: version}`` of a hash-pinned requirements file, every pin
    with at least one sha256."""
    body = (REPO_ROOT / ".github" / name).read_text(encoding="utf-8").replace("\\\n", " ")
    pins = {}
    for line in body.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        m = re.match(r"^([\w.-]+)==(\d+(?:\.\d+)*)\s+--hash=sha256:[0-9a-f]{64}", line)
        assert m, f"{name}: {line}"
        pins[m.group(1)] = m.group(2)
    return pins


def test_the_test_tools_and_the_fuzzer_are_hash_pinned_and_installed() -> None:
    """Scorecard's Pinned-Dependencies: the coverage and property-test tools
    and Atheris come from exact, hash-checked pins."""
    assert set(_pins("ci-test-tools-requirements.txt")) == {"hypothesis", "sortedcontainers", "coverage", "pytest-cov"}
    assert set(_pins("ci-fuzz-requirements.txt")) == {"atheris"}
    jobs = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]

    def runs(job: str) -> str:
        return "\n".join(str(s.get("run", "")) for s in jobs[job]["steps"])

    assert "--require-hashes -r .github/ci-test-tools-requirements.txt" in runs("python")
    assert "--cov --cov-report=" in runs("python")
    assert "coverage_summary.py" in runs("coverage")
    assert "--require-hashes -r .github/ci-fuzz-requirements.txt" in runs("fuzz")
    assert "scripts/dev/fuzz.py" in runs("fuzz") and "-max_total_time=" in runs("fuzz")
    # Report-only and random: neither gates `ci passed` (docs/CONTRIBUTING.md, "CI").
    assert not {"coverage", "fuzz"} & set(jobs["ci-ok"]["needs"])
    assert "python" in jobs["ci-ok"]["needs"], "the deterministic property tests do gate it"


def test_scorecard_sees_the_fuzzer() -> None:
    """OpenSSF Scorecard's Fuzzing check finds Python fuzzing by a *.py file
    with a line matching ``import atheris`` (checks/raw/fuzzing.go)."""
    text = (REPO_ROOT / "scripts" / "dev" / "fuzz.py").read_text(encoding="utf-8")
    assert re.search(r"^\s*import atheris\s*$", text, re.M)
