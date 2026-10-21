"""Take secrets, codes, addresses and names out of text before help shows it.

Everything troubleshooting and "report a problem" read comes from the
system's own records: an outage report a satellite wrote, the update
unit's last result, a plugin's load error, a container incident. Help picks
known fields rather than dumping records (:mod:`.troubleshoot`), but a
free-text field (an exit reason, an error line) can carry anything. Two
passes run over every such string:

1. **Known values, by exact match** (case ignored): what this install
   actually holds — the household token, the setup code, password and
   token hashes, the Wi-Fi network names and host names its satellites
   reported, this server's own host name, the hosts and domains of its
   configured URLs and people's names
   (:mod:`.known`, gathered per request and set with :func:`known_values`).
   Each becomes ``[private]``. This is the guarantee for what Domovoi
   knows about.
2. **Patterns, as a backstop** for what it doesn't: IPv4/IPv6/MAC
   addresses, ``host:port``, domain names, URLs, e-mail addresses, quoted
   strings (a network name, a transcript), long hex or base64 runs,
   ``key=value`` pairs whose key names a secret or a network, word-phrase
   codes, six-digit codes, home folders.

What neither pass can promise: a value Domovoi never saw, written in a way
no pattern matches (a stranger's first name in an error line). That is why
"report a problem" shows the person the whole text before they post it.
"""

from __future__ import annotations

import contextlib
import re
from contextvars import ContextVar
from typing import Any, Iterable, Iterator

#: Known values shorter than this are not matched (they'd hit ordinary words).
KNOWN_MIN_LEN = 3

_KNOWN: ContextVar[tuple[str, ...]] = ContextVar("help_known_values", default=())


def prepare_known(values: Iterable[Any]) -> tuple[str, ...]:
    """Distinct non-trivial values, longest first (so a full name goes
    before its first name)."""
    seen: dict[str, str] = {}
    for v in values:
        s = " ".join(str(v or "").split())
        if len(s) >= KNOWN_MIN_LEN and s.lower() not in seen:
            seen[s.lower()] = s
    return tuple(sorted(seen.values(), key=len, reverse=True))


@contextlib.contextmanager
def known_values(values: Iterable[Any]) -> Iterator[tuple[str, ...]]:
    """Make ``values`` the known values every :func:`redact` in this
    context scrubs (a request's, or a test's)."""
    prepared = prepare_known(values)
    token = _KNOWN.set(prepared)
    try:
        yield prepared
    finally:
        _KNOWN.reset(token)


def scrub_known(text: str, known: Iterable[str]) -> str:
    out = text
    for v in known:
        out = re.sub(re.escape(v), "[private]", out, flags=re.I)
    return out


def _ipv6(m: "re.Match[str]") -> str:
    """An IPv6 address, not a clock time ("12:30:45"): it has "::" or more
    than three groups."""
    t = m.group(0)
    return "[address]" if ("::" in t or t.count(":") >= 4) else t


_TLDS = (
    "com|net|org|io|dev|app|info|biz|me|us|uk|de|fr|nl|nz|ca|au|eu|co|tv|xyz|cloud|online|site|tech|"
    "home|lan|local|internal|arpa|localdomain|casa|house|family|ru|jp|in|br|es|it|se|no|dk|fi|ch|at|be|pl"
)

_RULES: list[tuple[re.Pattern[str], Any]] = [
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [token]"),
    # key=value with a secret- or network-sounding key (before the generic
    # rules eat the value).
    (re.compile(
        r"(?i)\b([\w-]*(?:token|password|passwd|secret|api[_-]?key|apikey|cookie|auth|code|psk|ssid|essid|"
        r"network|host|hostname)[\w-]*)"
        r"(\s*[=:]\s*)(?!\[)(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    ), r"\1\2[redacted]"),
    # "password hunter2": only for words that never precede an ordinary word
    # ("error code model_missing" keeps its code).
    (re.compile(r"(?i)\b(password|passwd|passphrase|secret|psk)(\s+)(?!\[)([^\s,;]+)"), r"\1\2[redacted]"),
    # "token Sunflower2024!", "household token is Sunflower2024!": a value
    # after "token" that looks like one (a digit or a symbol, 6+ long).
    (re.compile(r"(?i)\b(token|code|key)(\s+(?:is\s+|was\s+)?)(?!\[)(?=\S*[\d!@#$%^&*?])([^\s,;]{6,})"),
     r"\1\2[redacted]"),
    (re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s'\"<>)]+"), "[url]"),
    # What log lines put a network's, a host's or a person's name after.
    (re.compile(r"(?i)\b(wi-?fi network|wireless network|ssid|essid|associate with|associated with|"
                r"join network|access point)(\s+)(?!\[)([^\s,;:]+)"), r"\1\2[network]"),
    (re.compile(r"(?i)\b(resolve|resolving|lookup of)(\s+)(?!\[)([a-z0-9][a-z0-9.-]{1,62})\b"), r"\1\2[host]"),
    (re.compile(r"(?i)\b[a-z0-9]+(?:-[a-z0-9]+)*-(?:desktop|laptop|pc|server|nas|pi|router|host|macbook|imac|"
                r"mac|tower|box|ubuntu|debian|fedora|windows|win)\b"), "[host]"),
    (re.compile(r"\b(matched|identified as|recogni[sz]ed as|speaker|person|user)(\s+)"
                r"(?!\[)([A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+)?)"), r"\1\2[name]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "[email]"),
    # Quoted text: a network name ('SmithFamily5G'), a heard sentence.
    (re.compile(r"'[^'\n]{1,120}'|\"[^\"\n]{1,120}\"|“[^”\n]{1,120}”"), "[quoted]"),
    (re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"), "[mac]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b"), "[address]"),
    (re.compile(r"(?i)(?<![\w:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?:%\w+)?(?![\w:])"), _ipv6),
    # Domain names (nas.smith-family.net, ha.smithhouse.org:8123).
    (re.compile(rf"(?i)\b(?:[a-z0-9](?:[a-z0-9-]{{0,61}}[a-z0-9])?\.)+(?:{_TLDS})(?::\d{{1,5}})?\b"), "[host]"),
    # A bare host with a port (kamron-desktop:6369).
    (re.compile(r"(?i)\b[a-z][a-z0-9-]{1,62}:\d{2,5}\b"), "[host]"),
    (re.compile(r"(?i)(?:[A-Z]:\\Users\\|/home/|/Users/)[^\\/\s]+"), "[home]"),
    # The setup code and the household token: eight lowercase words joined
    # by hyphens (admin_auth.generate_word_phrase); six or more hyphened
    # words are taken for one.
    (re.compile(r"(?i)\b(?:[a-z]{2,12}-){5,}[a-z]{2,12}\b"), "[code]"),
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "[token]"),
    (re.compile(r"\b[A-Za-z0-9_-]{24,}={0,2}(?![\w/])"), "[token]"),
    (re.compile(r"(?<![\d.:])\d{6}(?![\d.:])"), "[code]"),
    (re.compile(r"(?<![\d.:])\d{3}[ -]\d{3}(?![\d.:])"), "[code]"),
]


def redact(text: object, *, limit: int = 400, known: Iterable[str] | None = None) -> str:
    """``text`` with known values, then secrets, codes, addresses and
    quoted text replaced, cut to ``limit``."""
    s = str(text or "")
    s = scrub_known(s, prepare_known(known) if known is not None else _KNOWN.get())
    for pattern, repl in _RULES:
        s = pattern.sub(repl, s)
    s = " ".join(s.split())
    if limit and len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s
