""".github/workflows/codeql.yml: CodeQL (SAST), and the hash-pinned CI extras.

Pure file checks, no network, no DB. Actions pinned by commit SHA, the
token scoped (read-all at the top, one write in the job), no secret, and
vendored code kept out of the analysis.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
TEXT = (REPO / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8")
DOC = yaml.safe_load(TEXT)
CONFIG = yaml.safe_load((REPO / ".github" / "codeql" / "codeql-config.yml").read_text(encoding="utf-8"))


def test_every_action_is_pinned_by_sha_and_codeql_matches_scorecard():
    refs = re.findall(r"uses:\s*(\S+)\s*(#.*)?", TEXT)
    assert refs
    for ref, comment in refs:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), ref
        assert re.match(r"#\s*v\d+\.\d+\.\d+", comment), ref
    scorecard = (REPO / ".github" / "workflows" / "scorecard.yml").read_text(encoding="utf-8")
    sha = re.search(r"codeql-action/upload-sarif@([0-9a-f]{40})", scorecard).group(1)
    for ref, _ in refs:
        if ref.startswith("github/codeql-action/"):
            assert ref.endswith("@" + sha), "same codeql-action release as scorecard.yml"


def test_permissions_are_scoped():
    assert DOC["permissions"] == "read-all"
    (job,) = DOC["jobs"].values()
    assert job["permissions"] == {"security-events": "write", "actions": "read", "contents": "read"}


def test_triggers_and_languages():
    on = DOC.get("on", DOC.get(True))
    assert on["push"] == {"branches": ["main"]} and on["schedule"]
    assert set(on) <= {"push", "pull_request", "schedule"}
    (job,) = DOC["jobs"].values()
    assert set(job["strategy"]["matrix"]["language"]) == {"python", "javascript-typescript", "actions"}
    init = next(s for s in job["steps"] if "codeql-action/init" in s.get("uses", ""))
    assert init["with"]["build-mode"] == "none"
    assert init["with"]["config-file"] == "./.github/codeql/codeql-config.yml"


def test_no_secret_and_no_persisted_credentials():
    assert "secrets." not in TEXT and "GITHUB_TOKEN" not in TEXT
    checkout = next(s for s in DOC["jobs"]["analyze"]["steps"] if "checkout" in s.get("uses", ""))
    assert checkout["with"]["persist-credentials"] is False


def test_vendored_code_is_excluded():
    ignored = CONFIG["paths-ignore"]
    assert "web/static/vendor" in ignored and "android/native" in ignored


def test_ci_extras_are_exact_and_hashed():
    req = (REPO / ".github" / "ci-extra-requirements.txt").read_text(encoding="utf-8")
    body = req.replace("\\\n", " ")
    body = "\n".join(ln for ln in body.splitlines() if not ln.startswith("#"))
    pins = [ln for ln in body.splitlines() if ln.strip()]
    assert {ln.split("==")[0] for ln in pins} == {"scipy", "feedparser", "sgmllib3k"}
    for ln in pins:
        assert re.match(r"^\w+==\d+(\.\d+)*\s+--hash=sha256:[0-9a-f]{64}", ln), ln
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "--require-hashes -r .github/ci-extra-requirements.txt" in ci
    assert not re.search(r'pip install "', ci)
