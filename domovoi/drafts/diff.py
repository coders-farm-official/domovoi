"""What changed between two versions of a draft: a word-level diff (our own
Myers, no library) over the draft as it reads (:func:`display_text`), and
the one line a revision's reply says ("cut 120 words (380 → 260)").

The phone's twin is ``DraftDiff.kt``; ``shared_cases.json`` holds both to
the same results. The dashboard asks the server for the diff, so it has no
copy of its own.

Tokens are runs of non-space characters and runs of whitespace; a run of
whitespace counts as one space, one line break or a paragraph break, so
re-wrapping a paragraph never shows as a change.
"""

from __future__ import annotations

import math
import re
from array import array

from domovoi.drafts import blocks as blocks_mod

_TOKEN = re.compile(r"\S+|\s+")
#: Past this many tokens on both sides the diff doesn't look for words in
#: common inside the changed stretch (a guard; a 30-page rewrite is section
#: by section anyway).
MAX_TOKENS = 40000
#: The most work the word diff does (a diagonal tried or a token compared)
#: before it settles for the plain view: the words both versions start and
#: end with kept, the stretch between shown as removed then added. Myers'
#: work grows with the input times the number of changed tokens, so two
#: unrelated long drafts would otherwise take seconds and gigabytes; this
#: keeps the worst case under 100 ms and a few MB. ``DraftDiff.kt`` counts
#: the same work, so both give the same view.
WORK_BUDGET = 300_000


def display_text(markdown: str) -> str:
    """The draft as plain text, as the changes view shows it: a block per
    paragraph, a bullet per line (with its "• "), a paragraph's lines kept."""
    out: list[str] = []
    prev = None
    for b in blocks_mod.parse(markdown):
        text = "\n".join(("• " + ln) if b.style == "bullet" else ln for ln in b.lines)
        if out:
            out.append("\n" if (b.style == "bullet" and prev == "bullet") else "\n\n")
        out.append(text)
        prev = b.style
    return "".join(out)


def tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall(text or ""):
        if t.isspace():
            n = t.count("\n")
            t = " " if n == 0 else ("\n" if n == 1 else "\n\n")
        out.append(t)
    return out


def _replaced(a: list[str], b: list[str]) -> list[tuple[str, str]]:
    """The diff past its budget: the words both sides start and end with
    kept, everything between shown as removed then added."""
    n, m = len(a), len(b)
    p = 0
    while p < n and p < m and a[p] == b[p]:
        p += 1
    s = 0
    while s < n - p and s < m - p and a[n - 1 - s] == b[m - 1 - s]:
        s += 1
    runs = (("=", a[:p]), ("-", a[p:n - s]), ("+", b[p:m - s]), ("=", a[n - s:]))
    return [(op, "".join(ts)) for op, ts in runs if ts]


def _myers(a: list[str], b: list[str]) -> list[tuple[str, str]]:
    """Myers' greedy diff. Each step keeps only the diagonals it can have
    reached (2d+3 numbers, not the whole V), and the work (a diagonal
    tried or a token compared, the same count as ``DraftDiff.kt``) stops at
    :data:`WORK_BUDGET`: past it, :func:`_replaced`."""
    n, m = len(a), len(b)
    if n + m == 0:
        return []
    if n + m > MAX_TOKENS:
        return _replaced(a, b)
    # Step d costs at least d + 1 work, so the budget caps d as well.
    maxd = min(n + m, math.isqrt(2 * WORK_BUDGET) + 1)
    off = maxd + 1
    v = array("i", bytes(4 * (2 * maxd + 3)))
    trace: list[array[int]] = []
    work = 0
    done = False
    for d in range(maxd + 1):
        trace.append(v[off - d - 1:off + d + 2])
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v[off + k - 1] < v[off + k + 1]):
                x = v[off + k + 1]
            else:
                x = v[off + k - 1] + 1
            y = x - k
            x0 = x
            while x < n and y < m and a[x] == b[y]:
                x += 1
                y += 1
            v[off + k] = x
            work += 1 + x - x0
            if x >= n and y >= m:
                done = True
                break
            if work > WORK_BUDGET:
                return _replaced(a, b)
        if done:
            break
    if not done:
        return _replaced(a, b)
    # Walk back through the trace (step d's slice starts at diagonal -d-1).
    ops: list[tuple[str, str]] = []
    x, y = n, m
    for d in range(len(trace) - 1, -1, -1):
        vv = trace[d]
        o = d + 1
        k = x - y
        if k == -d or (k != d and vv[o + k - 1] < vv[o + k + 1]):
            pk = k + 1
        else:
            pk = k - 1
        px = vv[o + pk]
        py = px - pk
        while x > px and y > py:
            ops.append(("=", a[x - 1]))
            x -= 1
            y -= 1
        if d > 0:
            if x == px:
                ops.append(("+", b[y - 1]))
                y -= 1
            else:
                ops.append(("-", a[x - 1]))
                x -= 1
    ops.reverse()
    return ops


def diff(old: str, new: str) -> list[list[str]]:
    """``[[op, text], ...]`` turning ``old`` into ``new`` (both plain text,
    :func:`display_text`): ``=`` kept, ``+`` added, ``-`` removed. Runs of
    one op are joined; whitespace between two changed words stays inside
    the change."""
    # Joined once per run: adding to the run's string token by token
    # copies it each time, which on a long unchanged stretch is quadratic.
    out: list[list[str]] = []
    run: list[str] = []
    for op, t in _myers(tokens(old), tokens(new)):
        if out and out[-1][0] == op:
            run.append(t)
        else:
            if out:
                out[-1][1] = "".join(run)
            out.append([op, ""])
            run = [t]
    if out:
        out[-1][1] = "".join(run)
    return out


def _words(text: str) -> int:
    return len([t for t in tokens(text) if not t.isspace()])


def change_line(old_markdown: str, new_markdown: str) -> str:
    """The one line a revision's reply says about what changed."""
    old, new = display_text(old_markdown), display_text(new_markdown)
    ow, nw = _words(old), _words(new)
    ops = diff(old, new)
    added = sum(_words(t) for op, t in ops if op == "+")
    removed = sum(_words(t) for op, t in ops if op == "-")
    if added == 0 and removed == 0:
        return "no changes"
    net = nw - ow
    small = max(5, ow // 10)
    if net < 0 and -net >= small:
        return f"cut {-net} word{'' if -net == 1 else 's'} ({ow} → {nw})"
    if net > 0 and net >= small:
        return f"added {net} word{'' if net == 1 else 's'} ({ow} → {nw})"
    changed = max(added, removed)
    return f"changed {changed} word{'' if changed == 1 else 's'} ({ow} → {nw})"
