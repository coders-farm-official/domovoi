"""Reading a fetched page in a process of its own, killed when it runs over.

A page a person names is untrusted input to Python's HTML parser, and some
inputs make that parser slow (``"<p>" + "<a" * n`` is quadratic: W1 review
round 2). A thread can't be stopped, so a slow page would keep a core busy
after its lookup gave up. Here the work (:func:`read_text`: decode, the main
text, the passage for the question) runs in a short-lived child process:

* the child is plain ``python -I`` running this file's :func:`_child`, with
  only the standard library and ``domovoi.page_text`` / ``domovoi.lookup_answer``
  (no app import, no settings, no network, no database);
* at most :data:`MAX_BYTES` of the page are given to it, and it is killed
  after :data:`TIMEOUT_SEC`, its CPU freed at once;
* at most :data:`WORKERS` run at a time, waited on from this module's own
  small thread pool (never the shared executor);
* on POSIX the child caps its own memory (``RLIMIT_AS``) and lowers its
  priority; on Windows it starts below normal priority.

:func:`read_text` is also importable and pure, for the child and the tests.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import subprocess
import sys
from pathlib import Path

#: Bytes of a page the parser is given (the fetch reads up to 2 MB).
MAX_BYTES = 256 * 1024
#: The longest one page's text work may take; the child is then killed.
TIMEOUT_SEC = 5.0
#: Children at a time.
WORKERS = 2
#: The child's address-space cap on POSIX.
CHILD_MEMORY_BYTES = 512 * 1024 * 1024
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_REPO = str(Path(__file__).resolve().parents[1])
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="page-worker")


class PageTooSlow(TimeoutError):
    """The page's text work ran past :data:`TIMEOUT_SEC` (the child was killed)."""


def read_text(content: bytes, charset: str, ctype: str, query: str,
              extract_chars: int = 3000) -> tuple[str, str | None, str]:
    """(title, text or None for not a page, extract) of a fetched page; at
    most :data:`MAX_BYTES` of it are read."""
    from domovoi import lookup_answer, page_text

    content = content[:MAX_BYTES]
    try:
        raw = content.decode(charset, "replace")
    except LookupError:
        raw = content.decode("utf-8", "replace")
    if ctype in _HTML_TYPES or (not ctype and raw.lstrip()[:1] == "<"):
        title, text = page_text.main_text(raw)
    elif ctype == "text/plain":
        title, text = "", page_text.plain_text(raw)
    else:
        return "", None, ""
    extract = lookup_answer.best_passage(text, query or title, max_chars=extract_chars) if text else ""
    return title, text, extract


def _limit_self() -> None:
    if sys.platform == "win32":
        return
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (CHILD_MEMORY_BYTES, CHILD_MEMORY_BYTES))
    except Exception:  # noqa: BLE001 — best effort
        pass
    try:
        os.nice(10)
    except Exception:  # noqa: BLE001
        pass


def _child() -> None:
    """The child's whole life: arguments in argv, the page on stdin, the
    answer as JSON on stdout."""
    _limit_self()
    charset, ctype, query, extract_chars = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    content = sys.stdin.buffer.read(MAX_BYTES)
    title, text, extract = read_text(content, charset, ctype, query, extract_chars)
    sys.stdout.write(json.dumps({"title": title, "text": text, "extract": extract}))
    sys.stdout.flush()


_CHILD_CODE = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from domovoi.page_worker import _child; _child()"
)


def run_child(content: bytes, charset: str, ctype: str, query: str, extract_chars: int,
              timeout: float = TIMEOUT_SEC) -> tuple[str, str | None, str]:
    """Blocking: the child's answer, or :class:`PageTooSlow` after it was
    killed. ``on_start`` sees the child (the tests check it is gone)."""
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", _CHILD_CODE, _REPO, charset or "utf-8", ctype or "", query or "",
         str(int(extract_chars))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=flags, close_fds=True,
    )
    LAST_CHILD[0] = proc
    try:
        out, _ = proc.communicate(content[:MAX_BYTES], timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise PageTooSlow(f"page text work ran past {timeout:g} s")
    if proc.returncode != 0:
        return "", "", ""
    try:
        d = json.loads(out.decode("utf-8"))
    except ValueError:
        return "", "", ""
    return str(d.get("title") or ""), (None if d.get("text") is None else str(d["text"])), str(d.get("extract") or "")


#: The last child started (the tests read it to show it was killed).
LAST_CHILD: list[subprocess.Popen | None] = [None]


async def read_page_text(content: bytes, charset: str, ctype: str, query: str,
                         extract_chars: int = 3000) -> tuple[str, str | None, str]:
    """:func:`read_text` in a child process, waited on from this module's own
    pool; raises :class:`PageTooSlow`."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_POOL, run_child, content, charset, ctype, query, extract_chars)


__all__ = ["MAX_BYTES", "PageTooSlow", "TIMEOUT_SEC", "read_page_text", "read_text"]
