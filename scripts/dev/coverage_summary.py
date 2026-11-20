"""The test coverage of one combined coverage data file, as Markdown: the
total, then a line per package (CI's job summary; docs/CONTRIBUTING.md,
"Coverage").

    python -m coverage combine coverage-data/          # the shards' files
    python scripts/dev/coverage_summary.py .coverage >>"$GITHUB_STEP_SUMMARY"

A package is a file's folder, two levels deep (``domovoi/handlers``,
``web/backend``, ``plugins/radio``); the files directly under ``domovoi/``
are ``domovoi``. Report only: it never fails on a low number.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import PurePosixPath


def package_of(path: str) -> str:
    parts = PurePosixPath(path.replace("\\", "/")).parts[:-1]
    return "/".join(parts[:2]) or "(root)"


def summarize(data_file: str) -> str:
    import coverage

    cov = coverage.Coverage(data_file=data_file)
    cov.load()
    lines: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # package -> [statements, missed]
    for f in cov.get_data().measured_files():
        try:
            _name, statements, _excluded, missing, _text = cov.analysis2(f)
        except coverage.CoverageException:  # source not there (a generated file)
            continue
        key = package_of(f)
        lines[key][0] += len(statements)
        lines[key][1] += len(missing)
    total = sum(s for s, _ in lines.values())
    missed = sum(m for _, m in lines.values())
    pct = 100.0 * (total - missed) / total if total else 0.0
    out = [
        "### Test coverage",
        "",
        f"**{pct:.1f}%** of {total:,} lines of the shipped Python code ran under the suite "
        "(line coverage, all shards combined; report only, no threshold).",
        "",
        "| Package | Lines | Covered | % |",
        "|---|---:|---:|---:|",
    ]
    for key, (s, m) in sorted(lines.items(), key=lambda kv: (-kv[1][0], kv[0])):
        if s:
            out.append(f"| `{key}` | {s:,} | {s - m:,} | {100.0 * (s - m) / s:.1f} |")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    print(summarize(argv[0] if argv else ".coverage"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
