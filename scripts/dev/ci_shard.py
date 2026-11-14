"""Split the pytest suite's files into N shards for CI (.github/workflows/ci.yml).

    python scripts/dev/ci_shard.py 3 6     # the files of shard 3 of 6, one per line
    python scripts/dev/ci_shard.py --summary 6   # every shard's file count and weight

Every test file under the ``testpaths`` in pyproject.toml (pytest's default
``test_*.py`` / ``*_test.py``) lands in exactly one shard. A file's weight
is its size in bytes, a rough stand-in for how long it runs; the files go,
heaviest first, to the lightest shard so far (ties: the lower shard, then
the path), so a given tree always splits the same way on every machine.
Whole files, never single tests: a module's fixtures and its xdist_group
stay together.

Standard library only (no pytest-split: it needs a durations file kept up
to date by hand).
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_files(repo: Path = REPO) -> list[Path]:
    """Every test file pytest would collect from the testpaths, repo-relative."""
    conf = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    paths = conf["tool"]["pytest"]["ini_options"]["testpaths"]
    found: set[Path] = set()
    for tp in paths:
        root = repo / tp
        for pattern in ("test_*.py", "*_test.py"):
            for f in root.rglob(pattern):
                if "__pycache__" in f.parts or "node_modules" in f.parts:
                    continue
                found.add(f.relative_to(repo))
    return sorted(found, key=lambda p: p.as_posix())


def shards(files: list[Path], n: int, repo: Path = REPO) -> list[list[Path]]:
    """``files`` split into ``n`` lists of about equal weight (bytes)."""
    if n < 1:
        raise ValueError("need at least one shard")
    weighted = sorted(((repo / f).stat().st_size, f.as_posix(), f) for f in files)
    weighted.sort(key=lambda t: (-t[0], t[1]))
    loads = [0] * n
    out: list[list[Path]] = [[] for _ in range(n)]
    for size, _, f in weighted:
        i = min(range(n), key=lambda k: (loads[k], k))
        loads[i] += size
        out[i].append(f)
    return [sorted(s, key=lambda p: p.as_posix()) for s in out]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--summary", action="store_true", help="print each shard's file count and weight")
    ap.add_argument("shard", type=int, nargs="?", help="which shard, 1-based")
    ap.add_argument("total", type=int, help="how many shards")
    args = ap.parse_args(argv)
    if args.total < 1:
        ap.error("total must be at least 1")
    parts = shards(test_files(), args.total)
    if args.summary:
        for i, part in enumerate(parts, 1):
            weight = sum((REPO / f).stat().st_size for f in part)
            print(f"shard {i}/{args.total}: {len(part)} files, {weight:,} bytes")
        return 0
    if args.shard is None or not 1 <= args.shard <= args.total:
        ap.error(f"shard must be between 1 and {args.total}")
    for f in parts[args.shard - 1]:
        print(f.as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
