""".github/workflows/sherpa-onnx-rebuild.yml: the committed sherpa-onnx
libraries rebuilt from their recipe and compared with SHA256SUMS.

Pure file checks: no network, never skips. The heavy rebuild runs weekly,
by hand, and only when the folder or the workflow changes, never on every
pull request.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PATH = REPO / ".github" / "workflows" / "sherpa-onnx-rebuild.yml"
FOLDER = "android/native/sherpa-onnx"


def _doc() -> dict:
    return yaml.safe_load(PATH.read_text(encoding="utf-8"))


def test_runs_weekly_by_hand_and_only_when_the_folder_changes() -> None:
    on = _doc()[True]  # YAML 1.1 reads the key `on` as True
    assert set(on) == {"schedule", "workflow_dispatch", "push", "pull_request"}
    assert on["schedule"] and on["schedule"][0]["cron"]
    for event in ("push", "pull_request"):
        assert on[event]["branches"] == ["main"]
        assert set(on[event]["paths"]) == {f"{FOLDER}/**", ".github/workflows/sherpa-onnx-rebuild.yml"}


def test_builds_every_committed_abi_with_the_no_docker_recipe() -> None:
    jobs = _doc()["jobs"]
    committed = {
        line.split()[1].split("/")[1]
        for line in (REPO / FOLDER / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        if " jniLibs/" in line
    }
    assert set(jobs["build"]["strategy"]["matrix"]["abi"]) == committed
    assert jobs["build"]["strategy"]["fail-fast"] is False
    runs = "\n".join(str(s.get("run", "")) for s in jobs["build"]["steps"])
    assert f"bash {FOLDER}/build-native.sh" in runs
    assert "docker" not in runs.lower()
    assert "sha256sum -c" in runs, "the NDK zip is checked against build-native.sh's pin"
    # The sources from the submodules (F-Droid's way); build.sh's container
    # build keeps cloning by pin, so the two ways are held to the same bytes.
    checkout = next(s for s in jobs["build"]["steps"] if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["submodules"] is True
    assert "SHERPA_SUBMODULES" not in runs and "SHERPA_SRC" not in runs


def test_compare_runs_after_failed_builds_and_reads_sha256sums() -> None:
    job = _doc()["jobs"]["compare"]
    assert job["needs"] == "build"
    assert "cancelled()" in job["if"]
    runs = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert f"{FOLDER}/SHA256SUMS" in runs and "sys.exit(0 if ok else 1)" in runs
