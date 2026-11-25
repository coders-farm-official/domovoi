r"""CORE-46 (round 6): validators anchored with ``$`` accept a trailing newline.

In Python, ``$`` matches at the end of the string AND just before a final
``\n``, so ``re.match(r"^[a-z]+$", "abc\n")`` passes. The audit's proof:
``GET /x\n HTTP/1.1`` on the port-80 listener came back with a bare LF inside
the ``Location`` header; ``valid_challenge("abc\n")`` and
``valid_addr("127.0.0.1:6370\n")`` were true and got signed; Friend Houses
ids with a trailing newline were forwarded to the core.

Every validator now ends in ``\Z`` (or uses ``fullmatch``). The last test is
the gate that keeps it so: a ``$``-anchored pattern in an ``re`` call outside
the modules that parse free text fails it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from domovoi import server_identity
from web.backend.short_address import answer

REPO = Path(__file__).resolve().parents[2]


# ─── the card's proof of concept ──────────────────────────────────────────


def test_the_port_80_redirect_refuses_a_target_ending_in_a_newline() -> None:
    req = "GET /x\n HTTP/1.1\r\nHost: domovoi\r\n\r\n"
    data, status = answer(req.encode("ascii"), local_ip="192.168.1.20",
                          names=frozenset({"domovoi"}), target_port=6369)
    assert status == 400
    assert b"Location" not in data
    # ...and the same request without it is still redirected.
    data, status = answer(b"GET /x HTTP/1.1\r\nHost: domovoi\r\n\r\n", local_ip="192.168.1.20",
                          names=frozenset({"domovoi"}), target_port=6369)
    assert status == 302 and b"Location: http://domovoi:6369/x\r\n" in data


@pytest.mark.parametrize("challenge", ["abc\n", "0123456789abcdef\n"])
def test_a_challenge_ending_in_a_newline_is_not_signed(challenge: str) -> None:
    assert not server_identity.valid_challenge(challenge)
    assert server_identity.valid_challenge(challenge.rstrip("\n"))


def test_an_address_ending_in_a_newline_is_not_signed() -> None:
    assert not server_identity.valid_addr("127.0.0.1:6370\n")
    assert server_identity.valid_addr("127.0.0.1:6370")


def test_the_web_health_challenge_refuses_a_trailing_newline() -> None:
    from web.backend import main as web_main

    assert not web_main._HEALTH_CHALLENGE_RE.match("abc\n")
    assert web_main._HEALTH_CHALLENGE_RE.match("abc")


@pytest.mark.parametrize(("check", "good"), [
    ("_id", "inv_0123abcd"),
    ("_house", "a" * 64),
    ("_space", "b" * 32),
])
def test_friend_houses_ids_ending_in_a_newline_never_reach_the_core(check: str, good: str) -> None:
    from web.backend.api import friend_houses

    fn = getattr(friend_houses, check)
    with pytest.raises(HTTPException) as e:
        fn(good + "\n", "thing") if check == "_id" else fn(good + "\n")
    assert e.value.status_code == 404
    assert (fn(good, "thing") if check == "_id" else fn(good)) == good


def test_the_lan_origin_pattern_refuses_a_trailing_newline() -> None:
    """origin_allowed strips the header first; the pattern itself (also
    the dashboard's CORS regex) no longer leans on that."""
    from domovoi.transport_guard import lan_origin_regex

    assert re.match(lan_origin_regex(), "http://192.168.1.20:6369")
    assert not re.match(lan_origin_regex(), "http://192.168.1.20:6369\n")


# ─── the gate ─────────────────────────────────────────────────────────────

#: Modules whose ``$`` patterns parse free text (spoken commands, documents,
#: tool output, markup), where a trailing newline is not an attack and the
#: line ``$`` semantics may be relied on. Everything else that checks a value
#: with ``re`` ends its pattern in ``\Z`` or calls ``fullmatch``.
_PARSERS = (
    "domovoi/handlers/", "domovoi/doc_qa/", "domovoi/drafts/", "domovoi/doc_extract/",
    "domovoi/voice_docs/", "domovoi/phonemizer/", "domovoi/early_commit.py", "domovoi/router.py",
    "domovoi/lookup_answer.py", "domovoi/uncertainty.py", "domovoi/help_desk/", "domovoi/person_names.py",
    "domovoi/library_duplicates.py", "domovoi/library_naming.py", "domovoi/lists.py",
    "domovoi/timer_delivery.py", "domovoi/clients/ollama.py", "domovoi/clients/musicbrainz.py",
    "domovoi/workers/library_enricher.py", "domovoi/calendar_ics.py", "domovoi/calendar_feeds.py",
    "domovoi/host_memory.py", "domovoi/container_guard/compose.py", "domovoi/printing/templates.py",
    "domovoi/printing/capabilities.py", "domovoi/richdoc/word.py", "domovoi/richdoc/markdown_word.py", "domovoi/satellite_media/fetchers.py",
    "domovoi/plugins_runtime/lockfile.py", "domovoi/streaming.py",
    "web/backend/api/documents.py", "web/backend/api/files_security.py",
    "plugins/radio/domovoi_plugin_radio/handlers/",
    "scripts/manual/", "scripts/release/sbom.py", "scripts/release/finish_release.py",
    "scripts/eval", "scripts/g2p/",
)
_RE_CALLS = {"re.compile", "re.match", "re.search"}

#: Patterns another engine reads too (a JSON schema, a bash ``=~``), where
#: ``\Z`` is not syntax: they keep ``$``, and this module checks them with
#: ``fullmatch`` only (asserted below).
_SHARED = {
    "satellite/devices.py": "_WINDOWS_DEVICE_NAME_RE",
    "domovoi/satellite_linux.py": "_FILE_RE",
}


def _anchored_with_dollar(pattern: str) -> bool:
    i, n, in_class = 0, len(pattern), False
    while i < n:
        c = pattern[i]
        if c == "\\":
            i += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
        elif c == "[":
            in_class = True
            i += 1
            if i < n and pattern[i] == "^":
                i += 1
            if i < n and pattern[i] == "]":
                i += 1
            continue
        elif c == "$":
            return True
        i += 1
    return False


def _offenders() -> list[str]:
    out = []
    for top in ("domovoi", "web", "satellite", "plugins", "scripts"):
        for p in sorted((REPO / top).rglob("*.py")):
            rel = p.relative_to(REPO).as_posix()
            if "/tests/" in rel or rel.startswith(_PARSERS):
                continue
            try:
                source = p.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (SyntaxError, UnicodeDecodeError):
                continue
            shared = _SHARED.get(rel)
            shared_lines = set()
            if shared:
                for node in ast.walk(tree):
                    if isinstance(node, ast.Assign) and any(
                            isinstance(t, ast.Name) and t.id == shared for t in node.targets):
                        shared_lines.add(node.value.lineno)
                if re.search(re.escape(shared) + r"\.(match|search)\(", source):
                    out.append(f"{rel}: {shared} keeps $ for another engine, so use fullmatch only")
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and node.lineno in shared_lines:
                    continue
                if not (isinstance(node, ast.Call) and ast.unparse(node.func) in _RE_CALLS and node.args):
                    continue
                call = ast.unparse(node)
                if "MULTILINE" in call or "re.M" in call:
                    continue
                for lit in ast.walk(node.args[0]):
                    if isinstance(lit, ast.Constant) and isinstance(lit.value, str) \
                            and _anchored_with_dollar(lit.value):
                        out.append(f"{rel}:{node.lineno}: {lit.value[:60]!r}")
    return out


def test_no_validator_is_anchored_with_dollar() -> None:
    offenders = _offenders()
    assert not offenders, (
        "These patterns end in $, which also matches before a final newline "
        "(CORE-46). Use \\Z, or call fullmatch:\n" + "\n".join(offenders)
    )


def test_the_gate_sees_a_dollar_and_not_a_dollar_in_a_class() -> None:
    assert _anchored_with_dollar(r"^[a-z]+$")
    assert _anchored_with_dollar(r"^(?=.{1,9}$)[a-z]+\Z")
    assert not _anchored_with_dollar(r"^[a-z$]+\Z")
    assert not _anchored_with_dollar(r"^\$[0-9]+\Z")
    assert re.match(r"^[a-z]+$", "abc\n") and not re.match(r"^[a-z]+\Z", "abc\n")
