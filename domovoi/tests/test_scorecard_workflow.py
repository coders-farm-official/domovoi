""".github/workflows/scorecard.yml: OpenSSF Scorecard, published.

Pure file checks, no network, no DB. Every action pinned by commit SHA;
read-all at the top and only the job's two writes (code scanning, and the
OIDC token scorecard.dev checks a published result against); no secret, no
personal access token; and the rules scorecard.dev holds a published
result's workflow to (ossf/scorecard-action's README, "Workflow
Restrictions"), so a change that breaks them fails here rather than on
main.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PATH = REPO / ".github" / "workflows" / "scorecard.yml"
TEXT = PATH.read_text(encoding="utf-8")
DOC = yaml.safe_load(TEXT)
#: The actions scorecard.dev accepts in the job that publishes.
APPROVED = {"actions/checkout", "actions/upload-artifact", "github/codeql-action/upload-sarif",
            "ossf/scorecard-action", "step-security/harden-runner"}


def test_it_runs_on_main_weekly_and_on_branch_protection_changes():
    on = DOC.get("on", DOC.get(True))  # YAML 1.1 reads a bare `on` as true
    assert on["push"] == {"branches": ["main"]}
    assert on["schedule"] and "branch_protection_rule" in on
    assert set(on) == {"push", "schedule", "branch_protection_rule"}, "no pull_request or dispatch trigger"


def test_permissions_are_the_documented_minimum():
    assert DOC["permissions"] == "read-all"
    (job,) = DOC["jobs"].values()
    assert job["permissions"] == {"security-events": "write", "id-token": "write"}


def test_no_secret_and_no_token_but_the_run_s_own():
    assert "secrets." not in TEXT
    assert not re.search(r"repo_token|GITHUB_PAT|SCORECARD_TOKEN|github-token", TEXT)


def test_every_step_is_an_approved_action_pinned_by_sha():
    (job,) = DOC["jobs"].values()
    used = set()
    for step in job["steps"]:
        assert "run" not in step, "scorecard.dev takes only its approved actions in this job"
        ref = step["uses"]
        name, _, sha = ref.partition("@")
        assert re.fullmatch(r"[0-9a-f]{40}", sha), ref
        used.add(name)
    assert used <= APPROVED
    assert {"ossf/scorecard-action", "github/codeql-action/upload-sarif"} <= used
    for line in TEXT.splitlines():
        if "uses:" in line:
            assert re.search(r"#\s*v\d+\.\d+\.\d+\s*$", line), line


def test_it_publishes_and_uploads_the_sarif():
    (job,) = DOC["jobs"].values()
    steps = {s["uses"].split("@")[0]: s for s in job["steps"]}
    sc = steps["ossf/scorecard-action"]["with"]
    assert sc["publish_results"] is True and sc["results_format"] == "sarif"
    assert steps["github/codeql-action/upload-sarif"]["with"]["sarif_file"] == sc["results_file"]
    assert steps["actions/checkout"]["with"]["persist-credentials"] is False


def test_the_publishing_rules_hold():
    assert "env" not in DOC and "defaults" not in DOC
    (job,) = DOC["jobs"].values()
    for key in ("env", "defaults", "container", "services", "environment"):
        assert key not in job, key
    assert str(job["runs-on"]).startswith("ubuntu-")


def test_the_readme_shows_the_badge():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert 'href="https://scorecard.dev/viewer/?uri=github.com/coders-farm-official/domovoi"' in text
    assert "ossf-scorecard/github.com/coders-farm-official/domovoi" in text
