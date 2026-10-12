"""Known failures, labelled in pytest's summary.

``known_failures.toml`` beside this file lists the tests that fail on
origin/main for a reason someone has already written down. At the end of a
run the conftest sorts every failed or errored test into *known* (on the
list) or *new* (not on it) and prints both, so "is any of this mine?" is
one glance instead of an A/B against main.

It only labels. A known failure still fails, still prints its traceback,
still counts in "N failed" and still makes the exit status non-zero;
nothing here skips, deselects or xfails anything.

Entries name pytest node ids as the summary prints them, relative to the
repo root (``domovoi/tests/test_x.py::test_y[param]``). ``*`` in an entry
matches any run of characters (a whole module: ``domovoi/tests/test_x.py::*``);
nothing else is special, so parametrize brackets are written as they are.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

LIST_FILE = Path(__file__).with_name("known_failures.toml")
REQUIRED = ("test", "reason", "since")


@dataclass(frozen=True)
class Known:
    test: str
    reason: str
    since: str

    def matches(self, nodeid: str) -> bool:
        if "*" not in self.test:
            return nodeid == self.test
        rx = ".*".join(re.escape(part) for part in self.test.split("*"))
        return re.fullmatch(rx, nodeid) is not None


def load(path: Path = LIST_FILE) -> list[Known]:
    """The list, checked: every entry needs ``test``, ``reason`` and
    ``since`` as non-empty strings, and nothing else."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown_top = set(data) - {"failure"}
    if unknown_top:
        raise ValueError(f"{path.name}: unknown top-level keys {sorted(unknown_top)} (entries are [[failure]] tables)")
    out = []
    for i, entry in enumerate(data.get("failure", []), 1):
        missing = [k for k in REQUIRED if not isinstance(entry.get(k), str) or not entry[k].strip()]
        extra = set(entry) - set(REQUIRED)
        if missing or extra:
            raise ValueError(
                f"{path.name}: [[failure]] #{i} ({entry.get('test', '?')}): "
                + (f"needs {missing} " if missing else "")
                + (f"has unknown keys {sorted(extra)}" if extra else "")
            )
        out.append(Known(entry["test"].strip(), entry["reason"].strip(), entry["since"].strip()))
    return out


def plain_nodeid(nodeid: str) -> str:
    """The node id without the ``@group`` pytest-xdist appends under
    ``--dist loadgroup`` (``test_x[a]@pg-roles`` → ``test_x[a]``). An ``@``
    inside a parametrize id is left alone: those ids end in ``]``."""
    return re.sub(r"@[\w.-]+$", "", nodeid)


def find(entries: Iterable[Known], nodeid: str) -> Known | None:
    nodeid = plain_nodeid(nodeid)
    for e in entries:
        if e.matches(nodeid):
            return e
    return None


def summarize(terminalreporter: Any, entries: list[Known] | None = None) -> None:
    """The "known failures" section, and the "N new failures" / "M known
    failures" parts of pytest's last line."""
    tr = terminalreporter
    entries = load() if entries is None else entries
    bad = [r for key in ("failed", "error") for r in tr.stats.get(key, [])]
    known: list[tuple[Any, Known]] = []
    new: list[Any] = []
    for rep in bad:
        e = find(entries, rep.nodeid)
        if e is None:
            new.append(rep)
        else:
            known.append((rep, e))
    # Entries that matched no failure but did match a test that passed:
    # fixed (or flaky), worth a look by whoever keeps the list.
    failing = [plain_nodeid(rep.nodeid) for rep in bad]
    passed_ids = [plain_nodeid(rep.nodeid) for rep in tr.stats.get("passed", [])]
    passed = [
        e for e in entries
        if not any(e.matches(n) for n in failing) and any(e.matches(n) for n in passed_ids)
    ]
    if not bad and not passed:
        return

    title = f"known failures: {len(new)} new, {len(known)} known"
    tr.write_sep("=", title, red=bool(new), yellow=not new, bold=True)
    for rep in new:
        tr.write_line(f"NEW    {plain_nodeid(rep.nodeid)}{_when(rep)}", red=True, bold=True)
    for rep, e in known:
        tr.write_line(f"known  {plain_nodeid(rep.nodeid)}{_when(rep)}  [since {e.since}] {e.reason}")
    for e in passed:
        tr.write_line(
            f"passed {e.test}  (listed as known since {e.since}; if it is fixed for good, "
            f"take it off {LIST_FILE.name})",
            green=True,
        )
    # Into pytest's last line, beside "N failed": a part for each, so the
    # count of new failures is there at a glance.
    if new:
        tr.stats["new failure" if len(new) == 1 else "new failures"] = new
    if known:
        tr.stats["known failure" if len(known) == 1 else "known failures"] = [r for r, _ in known]


def _when(rep: Any) -> str:
    """" (error in setup)" and the like; nothing for a failed test body."""
    when = getattr(rep, "when", None)
    return f" (error in {when})" if when in ("setup", "teardown", "collect") else ""
