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

import re

from domovoi.drafts import blocks as blocks_mod

_TOKEN = re.compile(r"\S+|\s+")
#: Past this many tokens on both sides the diff is "everything changed"
#: (a guard; a 30-page rewrite is section by section anyway).
MAX_TOKENS = 40000


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


def _myers(a: list[str], b: list[str]) -> list[tuple[str, str]]:
    n, m = len(a), len(b)
    if n + m == 0:
        return []
    if n + m > MAX_TOKENS:
        return [("-", t) for t in a] + [("+", t) for t in b]
    maxd = n + m
    off = maxd
    v = [0] * (2 * maxd + 2)
    trace: list[list[int]] = []
    done = False
    for d in range(maxd + 1):
        trace.append(v[:])
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v[off + k - 1] < v[off + k + 1]):
                x = v[off + k + 1]
            else:
                x = v[off + k - 1] + 1
            y = x - k
            while x < n and y < m and a[x] == b[y]:
                x += 1
                y += 1
            v[off + k] = x
            if x >= n and y >= m:
                done = True
                break
        if done:
            break
    # Walk back through the trace.
    ops: list[tuple[str, str]] = []
    x, y = n, m
    for d in range(len(trace) - 1, -1, -1):
        vv = trace[d]
        k = x - y
        if k == -d or (k != d and vv[off + k - 1] < vv[off + k + 1]):
            pk = k + 1
        else:
            pk = k - 1
        px = vv[off + pk]
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
    ops = _myers(tokens(old), tokens(new))
    out: list[list[str]] = []
    for op, t in ops:
        if out and out[-1][0] == op:
            out[-1][1] += t
        else:
            out.append([op, t])
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
