"""CI's suite split (scripts/dev/ci_shard.py) and its fail-on-NEW-only
opt-in (domovoi/tests/known_failures.py, ``only_known``).

Pure file reads: no DB, never skips.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from domovoi.tests import known_failures

REPO_ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("ci_shard", REPO_ROOT / "scripts" / "dev" / "ci_shard.py")
ci_shard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci_shard)


def test_every_test_file_lands_in_exactly_one_shard() -> None:
    files = ci_shard.test_files()
    assert Path(__file__).relative_to(REPO_ROOT) in files
    assert any(f.as_posix().startswith("satellite/tests/") for f in files)
    assert any(f.as_posix().startswith("plugins/radio/tests/") for f in files)
    for n in (1, 4, 6):
        parts = ci_shard.shards(files, n)
        assert len(parts) == n
        flat = [f for part in parts for f in part]
        assert sorted(flat) == sorted(files), f"{n} shards drop or repeat a file"
        assert all(parts), f"an empty shard among {n}"


def test_the_split_is_the_same_every_time() -> None:
    files = ci_shard.test_files()
    assert ci_shard.shards(files, 6) == ci_shard.shards(list(reversed(files)), 6)


def test_the_shards_weigh_about_the_same() -> None:
    files = ci_shard.test_files()
    weights = [sum((REPO_ROOT / f).stat().st_size for f in part) for part in ci_shard.shards(files, 6)]
    heaviest = max((REPO_ROOT / f).stat().st_size for f in files)
    assert max(weights) - min(weights) <= heaviest


def _rep(nodeid: str) -> SimpleNamespace:
    return SimpleNamespace(nodeid=nodeid)


@pytest.mark.parametrize(
    "stats, expected",
    [
        ({}, False),  # nothing failed: nothing to excuse
        ({"failed": [_rep("a/test_x.py::test_known")]}, True),
        ({"error": [_rep("a/test_x.py::test_known@pg-roles")]}, True),
        ({"failed": [_rep("a/test_x.py::test_known"), _rep("a/test_x.py::test_new")]}, False),
        ({"failed": [_rep("a/test_y.py::anything")]}, True),
    ],
)
def test_only_known(stats: dict, expected: bool) -> None:
    entries = [
        known_failures.Known("a/test_x.py::test_known", "r", "abc"),
        known_failures.Known("a/test_y.py::*", "r", "abc"),
    ]
    assert known_failures.only_known(stats, entries) is expected
