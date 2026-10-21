"""Take secrets, codes and addresses out of text before help shows it.

Everything troubleshooting and "report a problem" read comes from the
system's own records: an outage report a satellite wrote, the update
unit's last result, a plugin's load error, a container incident. Those are
summaries already, and help picks known fields rather than dumping
records (:mod:`.troubleshoot`), but a free-text field (an exit reason, an
error line) can still carry an address or a token. :func:`redact` is the
last pass over every such string:

* IPv4 and IPv6 addresses, MAC addresses, ``host:port`` pairs, ``.local``
  style host names;
* e-mail addresses, and URLs (the host and anything after it);
* long hex or base64-looking runs (tokens, hashes, keys, SHAs);
* ``key=value`` / ``key: value`` pairs whose key names a secret
  (token, password, secret, key, code, cookie, auth, bearer);
* word phrases shaped like the setup code or household token (six or
  more hyphened words), and six-digit pairing codes;
* home folders (``/home/<name>``, ``C:\\Users\\<name>``).

Each becomes a bracketed word (``[address]``, ``[token]``…), so a reader
still sees that something was there.
"""

from __future__ import annotations

import re
from typing import Any


def _ipv6(m: "re.Match[str]") -> str:
    """An IPv6 address, not a clock time ("12:30:45"): it has "::" or more
    than three groups."""
    t = m.group(0)
    return "[address]" if ("::" in t or t.count(":") >= 4) else t


_RULES: list[tuple[re.Pattern[str], Any]] = [
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [token]"),
    # key=value with a secret-sounding key (before the generic rules eat the value).
    (re.compile(
        r"(?i)\b([\w-]*(?:token|password|passwd|secret|api[_-]?key|apikey|cookie|auth|code|psk)[\w-]*)"
        r"(\s*[=:]\s*)(?!\[)(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    ), r"\1\2[redacted]"),
    # "password hunter2": only for words that never precede an ordinary word
    # ("error code model_missing" keeps its code).
    (re.compile(r"(?i)\b(password|passwd|passphrase|secret|psk)(\s+)(?!\[)([^\s,;]+)"), r"\1\2[redacted]"),
    (re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s'\"<>)]+"), "[url]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "[email]"),
    (re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"), "[mac]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b"), "[address]"),
    (re.compile(r"(?i)(?<![\w:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?:%\w+)?(?![\w:])"), _ipv6),
    (re.compile(r"(?i)\b[a-z0-9-]+\.(?:local|lan|home|internal|localdomain)(?::\d{1,5})?\b"), "[host]"),
    (re.compile(r"(?i)\blocalhost:\d{1,5}\b"), "[address]"),
    (re.compile(r"(?i)(?:[A-Z]:\\Users\\|/home/|/Users/)[^\\/\s]+"), "[home]"),
    # The setup code and the household token: eight lowercase words joined
    # by hyphens (admin_auth.generate_word_phrase); six or more hyphened
    # words are taken for one.
    (re.compile(r"(?i)\b(?:[a-z]{2,12}-){5,}[a-z]{2,12}\b"), "[code]"),
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "[token]"),
    (re.compile(r"\b[A-Za-z0-9_-]{24,}={0,2}(?![\w/])"), "[token]"),
    (re.compile(r"(?<![\d.:])\d{6}(?![\d.:])"), "[code]"),
]


def redact(text: object, *, limit: int = 400) -> str:
    """``text`` with secrets, codes and addresses replaced, cut to ``limit``."""
    s = str(text or "")
    for pattern, repl in _RULES:
        s = pattern.sub(repl, s)
    s = " ".join(s.split())
    if limit and len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s
