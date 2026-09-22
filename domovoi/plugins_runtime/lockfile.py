"""Plugin lockfile parsing (design §7.4, §3.2 step 7).

A plugin lockfile is ``pip-compile --generate-hashes`` output and NOTHING
else: one exact pin per requirement (``name==version``, optionally with
extras and an environment marker), each followed by ``--hash=`` options.
The parser here is the single reader both the manifest layout check and
the installer's lockfile validation use, so a line is either understood
as a pinned requirement or refused — never substring-matched.

What is refused, and why:

* **Global pip options** (``--no-binary``, ``--index-url``,
  ``--find-links``, ``-e``, ``-r``, …) — a requirements file may carry
  them and they override the installer's own safety flags
  (``--only-binary=:all:``, the pinned index). Only ``--hash`` is
  allowed.
* **Direct references** (``name @ https://…``, ``name @ file://…``, a
  bare URL, a VCS spec) — pip accepts a hashed URL requirement as
  "pinned", which would let a lockfile point at any host. Every
  requirement must come from the configured package index by name, so
  the trust screen can show where it comes from.
* **Local paths** (``./vendor/x.whl``, ``../x``, ``C:\\…``) — same
  reason; a lockfile names distributions, not files.
* Anything else that is not ``name[extras]==version`` — ranges,
  wildcards, bare names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "LockRequirement",
    "LockfileError",
    "LOCK_REQUIREMENT_RE",
    "normalize_name",
    "parse_lockfile",
]

# The only shape a requirement line may take: name, optional extras, an
# exact ``==`` pin. Anchored on both ends; ``\S`` is deliberately NOT used
# for the version so ``==1.0@…`` / ``==1.0;…`` cannot smuggle a tail.
LOCK_REQUIREMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?P<extras>\[[^\]]*\])?"
    r"==(?P<version>[A-Za-z0-9.!+*]+)$"
)
# ``--hash=<algo>:<hex>``; pip only honours the strong algorithms.
_HASH_RE = re.compile(r"^--hash=(sha256|sha384|sha512):[0-9A-Fa-f]+$")
# Only ``--hash=…`` / ``--hash …`` may start with a dash inside a lockfile.
_HASH_OPTION_RE = re.compile(r"^--hash(?:=|$)", re.I)
# A ``#`` starts a comment at line start or after whitespace (pip's rule).
_COMMENT_RE = re.compile(r"(?:^|\s)#.*$")
_PATH_SUFFIXES = (".whl", ".zip", ".tar.gz", ".tgz", ".tar.bz2", ".tar")


class LockfileError(ValueError):
    """A lockfile line the parser refuses. ``code`` is the install-error
    code the API reports (``lockfile_option`` for a smuggled global
    option, ``lockfile_requirement`` for anything that is not an exact
    pin); ``details`` carries the line number and the offending text."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class LockRequirement:
    name: str                    # as written
    key: str                     # PEP 503 normalized name
    extras: tuple[str, ...]
    version: str
    marker: str | None
    hashes: tuple[str, ...]      # "sha256:…" values
    lineno: int                  # first physical line of the requirement

    @property
    def spec(self) -> str:
        extras = f"[{','.join(self.extras)}]" if self.extras else ""
        return f"{self.name}{extras}=={self.version}"


def normalize_name(name: str) -> str:
    """PEP 503 project-name normalization (``Foo_Bar.baz`` → ``foo-bar-baz``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Join backslash continuations; strip comments first (a comment ends
    the physical line, continuation or not). Returns (first lineno, text)."""
    out: list[tuple[int, str]] = []
    buf: list[str] = []
    start = 0
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = _COMMENT_RE.sub("", raw).strip()
        if not buf:
            start = lineno
        if line.endswith("\\"):
            buf.append(line[:-1].strip())
            continue
        buf.append(line)
        joined = " ".join(p for p in buf if p).strip()
        buf = []
        if joined:
            out.append((start, joined))
    if buf:
        joined = " ".join(p for p in buf if p).strip()
        if joined:
            out.append((start, joined))
    return out


def _refuse_requirement(lineno: int, text: str, spec: str) -> LockfileError:
    """Pick the most useful message for a line that is not an exact pin."""
    lowered = text.lower()
    if "@" in spec or "://" in lowered or spec.split("+", 1)[0] in (
        "git", "hg", "svn", "bzr"
    ):
        why = (
            "direct references (name @ url, URLs, VCS specs) are not "
            "allowed — every requirement must be an exact name==version pin "
            "resolved from the configured package index"
        )
    elif (
        spec.startswith((".", "/", "~", "\\"))
        or "/" in spec
        or "\\" in spec
        or (len(spec) > 1 and spec[1] == ":")
        or spec.lower().endswith(_PATH_SUFFIXES)
    ):
        why = (
            "local paths are not allowed — a lockfile names distributions "
            "on the configured package index, not files"
        )
    else:
        why = "must be an exact pin (name[extras]==version)"
    return LockfileError(
        "lockfile_requirement",
        f"lockfile line {lineno}: {spec!r} {why} (design §7.4)",
        {"line": lineno, "requirement": spec},
    )


def parse_lockfile(text: str) -> list[LockRequirement]:
    """Parse a lockfile into requirements or raise :class:`LockfileError`
    on the first line that is not an exact, index-resolved pin."""
    reqs: list[LockRequirement] = []
    for lineno, line in _logical_lines(text):
        tokens = line.split()
        if not tokens:
            continue
        if tokens[0].startswith("-"):
            if not _HASH_OPTION_RE.match(tokens[0]):
                option = re.split(r"[=\s]", tokens[0], maxsplit=1)[0]
                raise LockfileError(
                    "lockfile_option",
                    f"lockfile line {lineno} carries the pip option {option!r} — "
                    f"plugin lockfiles may contain only pinned, hashed "
                    f"requirements plus --hash= continuations; global pip "
                    f"options are forbidden because they override the "
                    f"installer's --only-binary safety flag and can execute a "
                    f"build backend pre-confirm (design §7.4)",
                    {"line": lineno, "option": option},
                )
            # A hash line on its own belongs to the requirement above it.
            if not reqs:
                raise LockfileError(
                    "lockfile_requirement",
                    f"lockfile line {lineno}: --hash before any requirement",
                    {"line": lineno},
                )
            prev = reqs.pop()
            hashes = list(prev.hashes) + _hashes(tokens, lineno)
            reqs.append(LockRequirement(
                prev.name, prev.key, prev.extras, prev.version, prev.marker,
                tuple(hashes), prev.lineno,
            ))
            continue

        # Requirement spec, optional ``; marker``, then ``--hash`` options.
        spec = tokens[0]
        rest = tokens[1:]
        marker: str | None = None
        if ";" in spec:
            spec, _, tail = spec.partition(";")
            rest = ([";" + tail] if tail else [";"]) + rest
        if rest and rest[0].startswith(";"):
            marker_tokens: list[str] = []
            first = rest[0][1:]
            if first:
                marker_tokens.append(first)
            i = 1
            while i < len(rest) and not rest[i].startswith("--"):
                marker_tokens.append(rest[i])
                i += 1
            marker = " ".join(marker_tokens).strip() or None
            rest = rest[i:]
        if "://" in line.lower():
            raise _refuse_requirement(lineno, line, spec)
        m = LOCK_REQUIREMENT_RE.match(spec)
        if m is None:
            raise _refuse_requirement(lineno, line, spec)
        extras = tuple(
            e.strip() for e in (m.group("extras") or "")[1:-1].split(",") if e.strip()
        )
        reqs.append(LockRequirement(
            name=m.group("name"),
            key=normalize_name(m.group("name")),
            extras=extras,
            version=m.group("version"),
            marker=marker,
            hashes=tuple(_hashes(rest, lineno)),
            lineno=lineno,
        ))
    return reqs


def _hashes(tokens: list[str], lineno: int) -> list[str]:
    """Every remaining token must be a ``--hash`` option."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--hash" and i + 1 < len(tokens):
            tok = f"--hash={tokens[i + 1]}"
            i += 1
        if not tok.startswith("--hash"):
            if tok.startswith("-"):
                option = re.split(r"[=\s]", tok, maxsplit=1)[0]
                raise LockfileError(
                    "lockfile_option",
                    f"lockfile line {lineno} carries the pip option {option!r} — "
                    f"only --hash= is allowed after a requirement (design §7.4)",
                    {"line": lineno, "option": option},
                )
            raise LockfileError(
                "lockfile_requirement",
                f"lockfile line {lineno}: unexpected {tok!r} after the "
                f"requirement — only --hash= options may follow a pin",
                {"line": lineno, "requirement": tok},
            )
        if not _HASH_RE.match(tok):
            raise LockfileError(
                "lockfile_requirement",
                f"lockfile line {lineno}: {tok!r} is not a --hash=sha256:<hex> "
                f"(or sha384/sha512) option",
                {"line": lineno, "requirement": tok},
            )
        out.append(tok[len("--hash="):])
        i += 1
    return out
