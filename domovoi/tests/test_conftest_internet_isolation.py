"""The suite can't be flipped by the internet answer at IMPORT time (D21).

The autouse fixture pins ``egress.policy()`` to unset for every test, but
``domovoi.config`` builds ``settings`` once, when conftest first imports
it, from the process environment and ``domovoi/.env``. An answer found
there would derive every follower from it and, under never, set
``HF_HUB_OFFLINE=1`` for the whole pytest process — and ft-based harnesses
write ``INTERNET_ACCESS=never`` into the ``.env`` of the worktree they
test. conftest therefore drops an exported answer and shadows the ``.env``
with an empty one while settings are built.

The child pytest below gets ``INTERNET_ACCESS=never`` in its environment,
the same shadowing path a stray ``.env`` takes, and must come up unset.
DB-free.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROBE_FLAG = "DOMOVOI_CONFTEST_INTERNET_PROBE"
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(os.environ.get(PROBE_FLAG) != "1", reason="run by the test below, in a child pytest")
def test_probe_the_import_time_settings_are_unanswered() -> None:
    from domovoi.config import settings

    assert settings.internet_access == ""
    assert not any(k.upper() == "INTERNET_ACCESS" for k in os.environ)
    assert os.environ.get("HF_HUB_OFFLINE", "") == ""


def test_an_answer_in_the_environment_does_not_reach_the_suite() -> None:
    env = dict(os.environ)
    env[PROBE_FLAG] = "1"
    env["INTERNET_ACCESS"] = "never"
    env.pop("HF_HUB_OFFLINE", None)
    # The parent's conftest already rewrote DATABASE_URL to its *_test lane;
    # hand that over as is (the probe opens no connection).
    env["TEST_DATABASE_URL"] = os.environ["DATABASE_URL"]
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            f"{Path(__file__).relative_to(REPO_ROOT).as_posix()}::"
            "test_probe_the_import_time_settings_are_unanswered",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    assert "1 passed" in proc.stdout, proc.stdout[-2000:]
