"""The tooling around the bash harnesses: the --case/--list filter they
share (scripts/linux/tests/case-filter.sh) and the container runner
(scripts/dev/run-linux-harness.sh with scripts/dev/linux-harness.Dockerfile).

Everything here is quick: the filter is exercised on a stand-in list of
cases, never a real one, and nothing starts Docker.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from domovoi.tests.test_apply_update_script import BASH, requires_bash

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "scripts" / "linux" / "tests"
CASE_FILTER = TESTS_DIR / "case-filter.sh"
HARNESSES = {
    "apply-update": TESTS_DIR / "test-apply-update.sh",
    "provision": TESTS_DIR / "test-provision.sh",
    "install-update-unit": TESTS_DIR / "test-install-update-unit.sh",
}
RUNNER = REPO_ROOT / "scripts" / "dev" / "run-linux-harness.sh"
DOCKERFILE = REPO_ROOT / "scripts" / "dev" / "linux-harness.Dockerfile"


def test_files_are_lf_only():
    for path in (CASE_FILTER, RUNNER, DOCKERFILE):
        assert b"\r" not in path.read_bytes(), f"{path.name} has CR line endings"


@requires_bash
def test_scripts_parse():
    for path in (CASE_FILTER, RUNNER):
        proc = subprocess.run([BASH, "-n", str(path)], capture_output=True, text=True)
        assert proc.returncode == 0, f"{path.name}: {proc.stderr}"


def _cases_in(harness: Path) -> list[str]:
    """The CASES=( ... ) list of a harness, as written."""
    m = re.search(r"^CASES=\(\n(.*?)^\)", harness.read_text(encoding="utf-8"), re.M | re.S)
    assert m, f"no CASES list in {harness.name}"
    return m.group(1).split()


def test_every_harness_uses_the_shared_filter():
    for name, path in HARNESSES.items():
        text = path.read_text(encoding="utf-8")
        assert '. "$HERE/case-filter.sh"' in text, name
        assert 'harness_parse_args "$@"' in text, name
        assert "harness_select" in text, name
        # Every case in the list is defined, and the list has no repeats.
        cases = _cases_in(path)
        assert len(cases) == len(set(cases)), name
        for c in cases:
            assert re.search(rf"^{c}\(\) {{", text, re.M), f"{name}: {c} listed but not defined"


# ─── the filter, on a stand-in list ──────────────────────────────────────────

_DRIVER = r"""
set -uo pipefail
. "$1"; shift
CASES=(case_alpha case_beta_one case_beta_two case_gamma)
harness_parse_args "$@"
harness_select
printf '%s\n' ${SELECTED_CASES[@]+"${SELECTED_CASES[@]}"}
echo "note:$(harness_selection_note)"
"""


def _filter(*args: str, only: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "ONLY"}
    if only is not None:
        env["ONLY"] = only
    return subprocess.run(
        [BASH, "-c", _DRIVER, "driver", CASE_FILTER.as_posix(), *args],
        capture_output=True, text=True, env=env,
    )


def _selected(proc: subprocess.CompletedProcess) -> list[str]:
    assert proc.returncode == 0, proc.stderr
    return [line for line in proc.stdout.splitlines() if not line.startswith("note:")]


@requires_bash
def test_no_option_selects_every_case_and_says_nothing_extra():
    proc = _filter()
    assert _selected(proc) == ["case_alpha", "case_beta_one", "case_beta_two", "case_gamma"]
    assert "note:\n" in proc.stdout


@requires_bash
def test_case_by_name_with_or_without_the_prefix_and_by_glob():
    proc = _filter("--case", "gamma", "--case", "case_alpha", "--case=beta_*", "--case", "alpha")
    # Run order is the harness's, and a case picked twice runs once.
    assert _selected(proc) == ["case_alpha", "case_beta_one", "case_beta_two", "case_gamma"]
    assert "note:\n" in proc.stdout  # all four: a full run, said as one
    proc = _filter("--case", "beta_t*")
    assert _selected(proc) == ["case_beta_two"]
    assert "note: (1 of 4 cases selected)" in proc.stdout


@requires_bash
def test_only_still_narrows():
    assert _selected(_filter("--case", "beta_*", only="one$")) == ["case_beta_one"]


@requires_bash
@pytest.mark.parametrize("args", [["--case", "nope"], ["--case", "beta_*", "--case", "z*"], ["--case", "alpha_x"]])
def test_a_case_that_matches_nothing_is_an_error(args):
    proc = _filter(*args)
    assert proc.returncode == 2
    assert "no case matches" in proc.stderr
    assert "case_" not in proc.stdout  # nothing ran


@requires_bash
@pytest.mark.parametrize("args", [["--bogus"], ["--case"], ["--case", ""], ["alpha"]])
def test_usage_errors(args):
    proc = _filter(*args)
    assert proc.returncode == 2, proc.stdout
    assert proc.stderr.strip()


@requires_bash
def test_list_prints_names_in_order_and_exits():
    proc = _filter("--list")
    assert proc.returncode == 0
    assert proc.stdout.split() == ["alpha", "beta_one", "beta_two", "gamma"]


@requires_bash
def test_a_real_harness_lists_its_cases():
    # The cheapest of the three to start under Git Bash.
    path = HARNESSES["install-update-unit"]
    proc = subprocess.run([BASH, str(path), "--list"], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == [c.removeprefix("case_") for c in _cases_in(path)]
    proc = subprocess.run([BASH, str(path), "--case", "no_such_case"], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 2
    assert "no case matches" in proc.stderr


# ─── the container runner ────────────────────────────────────────────────────


@requires_bash
@pytest.mark.parametrize(
    ("args", "says"),
    [
        (["nonsense"], "unknown harness"),
        (["all", "--case", "x"], "run that harness instead of all"),
        (["apply-update", "provision"], "one harness at a time"),
        (["--frobnicate", "apply-update"], "unknown option"),
        ([], "Usage"),
    ],
)
def test_runner_usage_errors_before_any_docker(args, says):
    proc = subprocess.run([BASH, str(RUNNER), *args], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2
    assert says in proc.stderr


@requires_bash
def test_runner_help():
    proc = subprocess.run([BASH, str(RUNNER), "--help"], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0
    for word in ("apply-update", "provision", "install-update-unit", "--case", "--list", "--src", "--results"):
        assert word in proc.stdout


def test_the_image_is_pinned():
    """Base by digest, packages from a fixed snapshot, each at a version."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    base = re.search(r"^ARG BASE=(\S+)$", text, re.M)
    assert base and re.fullmatch(r"ubuntu:26\.04@sha256:[0-9a-f]{64}", base.group(1))
    assert re.search(r"^ARG SNAPSHOT=\d{8}T\d{6}Z$", text, re.M)
    assert text.count('--snapshot "$SNAPSHOT"') == 2  # the index and the downloads
    install = text[text.index("install -y --no-install-recommends --snapshot"):]
    install = install[: install.index("&&")]
    packages = re.findall(r"^\s+([a-z0-9.+-]+)(=\S+)?\s*\\?$", install, re.M)
    assert packages, install
    for name, version in packages:
        assert version, f"{name} is not pinned to a version"


def test_runner_never_mounts_the_tree():
    """The tree goes in as a tar stream; the container gets no bind mount and
    no network, so it can neither write to the caller's checkout nor reach out."""
    text = RUNNER.read_text(encoding="utf-8")
    run = text[text.index("docker run"):]
    run = run[: run.index('<"$WORK/tree.tar"')]
    assert " -v " not in run and "--volume" not in run and "--mount" not in run
    assert "--network none" in run
