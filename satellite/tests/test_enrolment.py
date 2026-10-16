"""The spare-device enrolment payload (satellite/enrolment.py) against the
shared fixture, docs/uml/spare-device-enrol-fixture.json.

The Android room mode's Kotlin parser reads the same file, so a case that
changes here changes there: the two parsers can't drift apart. Everything
is in memory; ``verify`` gets a fake core that signs like a real one.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
from pathlib import Path

import pytest

from satellite import _ed25519, enrolment as en, server_identity

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads(
    (ROOT / "docs" / "uml" / "spare-device-enrol-fixture.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", FIXTURE["valid"], ids=lambda c: c["name"])
def test_every_valid_payload_parses_to_what_the_fixture_says(case):
    assert en.parse(case["payload"]).to_json() == case["expect"]


@pytest.mark.parametrize("case", FIXTURE["invalid"], ids=lambda c: c["name"])
def test_every_invalid_payload_is_refused_for_the_fixture_s_reason(case):
    with pytest.raises(en.EnrolmentError) as caught:
        en.parse(case["payload"])
    assert caught.value.reason == case["reason"]
    # The message is shown to a person; it never echoes a secret (there is
    # none) and is never empty.
    assert str(caught.value)


@pytest.mark.parametrize("case", FIXTURE["build"], ids=lambda c: c["room"].strip())
def test_the_builder_writes_exactly_the_fixture_s_payload(case):
    payload = en.build(case["core"], case["fp"], case["room"])
    assert payload == case["payload"]
    parsed = en.parse(payload)
    assert parsed.room == case["room"].strip()
    assert parsed.fp == case["fp"].strip()


@pytest.mark.parametrize("case", FIXTURE["build_join"], ids=lambda c: c["join_link"][:40])
def test_the_join_link_is_exactly_the_fixture_s_and_parses_like_its_payload(case):
    assert en.join_link(case["payload"]) == case["join_link"]
    assert en.parse(case["join_link"]) == en.parse(case["payload"])


def test_a_join_link_is_built_only_for_a_payload_that_parses():
    with pytest.raises(en.EnrolmentError) as e:
        en.join_link("domovoi-room:1?core=http%3A%2F%2F8.8.8.8&fp=x&room=y")
    assert e.value.reason == "not_home_network"
    with pytest.raises(en.EnrolmentError):
        en.join_link(FIXTURE["build_join"][0]["join_link"]), "a link is not wrapped twice"


def test_the_reason_codes_are_the_fixture_s():
    assert tuple(FIXTURE["reasons"]) == en.REASONS
    used = {c["reason"] for c in FIXTURE["invalid"]}
    assert used == set(en.REASONS), "every reason has at least one fixture case"


def test_the_fixture_covers_what_the_brief_names():
    names = {c["name"] for c in FIXTURE["valid"] + FIXTURE["invalid"]}
    for needed in ("bad_version", "missing_fp", "non_http_scheme",
                   "room_needs_escapes", "ipv6_host"):
        assert needed in names


@pytest.mark.parametrize("name", [
    "Kitchen", "Kid's Room & Den", "Küche 2", "Grandma’s Study", "Den \U0001F3AE",
    "A+B=C?", "50% off / #1", "  Office  ", "Ｏｆｆｉｃｅ", "Living-Room_2", "2nd Bedroom", "42",
])
def test_whatever_the_builder_writes_parses_back(name):
    fp = FIXTURE["build"][0]["fp"]
    parsed = en.parse(en.build("http://192.168.1.20:6370", fp, name))
    assert parsed.room == name.strip()
    assert parsed.room_id == en.room_id_for(name)
    assert en.ROOM_ID_RE.match(parsed.room_id), "the core's own room-id rule"
    assert not parsed.room_id.endswith("_")


def test_the_builder_refuses_what_the_parser_would():
    fp = FIXTURE["build"][0]["fp"]
    with pytest.raises(en.EnrolmentError) as e:
        en.build("ws://192.168.1.20:6370", fp, "Kitchen")
    assert e.value.reason == "bad_core"
    with pytest.raises(en.EnrolmentError) as e:
        en.build("http://192.168.1.20:6370", "SHA256:short", "Kitchen")
    assert e.value.reason == "bad_fp"
    with pytest.raises(en.EnrolmentError) as e:
        en.build("http://192.168.1.20:6370", fp, "!!!")
    assert e.value.reason == "bad_room"


def test_a_room_id_never_looks_like_a_path_or_an_option():
    for name in ("../etc", "-rf", "a/b", "--help", "x\\y"):
        rid = en.room_id_for(name)
        assert "/" not in rid and "\\" not in rid and not rid.startswith("-")
        assert en.ROOM_ID_RE.match(rid)


def test_the_room_id_rule_is_the_core_s():
    """domovoi/main.py's _ROOM_ID_RE is the source of truth: USB adoption,
    the preseed route and the dashboard's adopt form all hold ids to it."""
    import re

    main_src = (ROOT / "domovoi" / "main.py").read_text(encoding="utf-8")
    m = re.search(r'_ROOM_ID_RE = re\.compile\(r"(.+?)"\)', main_src)
    assert m and m.group(1) == en.ROOM_ID_RE.pattern


def test_the_home_names_are_the_core_s_lan_names():
    """The same suffixes the core takes as LAN names in a Host header
    (domovoi/transport_guard.py), less .localhost: a device must never
    dial itself."""
    import re

    src = (ROOT / "domovoi" / "transport_guard.py").read_text(encoding="utf-8")
    m = re.search(r"LOCAL_SUFFIXES = \((.+?)\)", src)
    core = {s.strip().strip('"') for s in m.group(1).split(",") if s.strip()}
    assert set(en.HOME_SUFFIXES) == core - {".localhost"}


@pytest.mark.parametrize("host,home", [
    ("192.168.1.20", True), ("10.0.0.1", True), ("172.16.0.1", True), ("172.31.255.255", True),
    ("172.32.0.1", False), ("8.8.8.8", False), ("127.0.0.1", False), ("100.64.0.1", False),
    ("169.254.1.1", False), ("fd00::17", True), ("fe80::1", True), ("2001:db8::1", False),
    ("::1", False), ("domovoi.local", True), ("kitchen.home.arpa", True), ("local", False),
    (".local", False), ("domovoi", False), ("domovoi.lan", True), ("core.internal", True),
    ("example.com", False), ("domovoi.local.example.com", False), ("lan", False),
])
def test_only_the_home_network_is_a_core(host, home):
    assert en.is_home_network_host(host) is home


# ─── verify: the installer's check before it writes anything ──────────────

def _core(seed_byte: int = 7):
    seed = bytes([seed_byte]) * 32
    public = _ed25519.public_key(seed)
    return seed, public, server_identity.fingerprint_for(public)


class _Resp:
    def __init__(self, body: bytes):
        self._body, self.status = body, 200

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _signing_opener(seed, public, seen):
    """Answers /v1/health like a current core: signs the caller's nonce and
    the address it says it dialed."""
    def opener(url, timeout=None):
        seen.append(url)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
        challenge, addr = query["challenge"], query.get("addr")
        sig = _ed25519.sign(seed, server_identity.health_message(challenge, addr))
        doc = {"status": "ok", "identity": {
            "algorithm": "ed25519",
            "fingerprint": server_identity.fingerprint_for(public),
            "public_key": base64.b64encode(public).decode("ascii"),
            "challenge": challenge, "addr": addr,
            "signature": base64.b64encode(sig).decode("ascii"),
        }}
        return _Resp(json.dumps(doc).encode())
    return opener


def test_verify_proves_the_core_named_in_the_payload():
    seed, public, fp = _core()
    seen: list[str] = []
    enrolment = en.parse(en.build("http://192.168.1.20:6370", fp, "Study"))
    assert en.verify(enrolment, opener=_signing_opener(seed, public, seen)) == fp
    assert seen and seen[0].startswith("http://192.168.1.20:6370/v1/health?challenge=")
    assert "addr=192.168.1.20%3A6370" in seen[0]


def test_verify_refuses_a_core_holding_a_different_key():
    seed, public, _fp = _core(7)
    _s2, _p2, other_fp = _core(8)
    enrolment = en.parse(en.build("http://192.168.1.20:6370", other_fp, "Study"))
    with pytest.raises(server_identity.IdentityError):
        en.verify(enrolment, opener=_signing_opener(seed, public, []))


def test_verify_binds_the_ipv6_address_it_dialed():
    seed, public, fp = _core()
    seen: list[str] = []
    enrolment = en.parse(en.build("http://[fd00::17]:6370", fp, "Hall"))
    assert en.verify(enrolment, opener=_signing_opener(seed, public, seen)) == fp
    assert "addr=%5Bfd00%3A%3A17%5D%3A6370" in seen[0]


# ─── the installer's command line ─────────────────────────────────────────

def test_parse_on_the_command_line_prints_json(capsys):
    case = FIXTURE["valid"][0]
    assert en._main(["parse", case["payload"]]) == 0
    assert json.loads(capsys.readouterr().out) == case["expect"]


def test_a_refused_payload_exits_2_with_the_reason_on_stderr(capsys):
    case = next(c for c in FIXTURE["invalid"] if c["name"] == "bad_version")
    assert en._main(["parse", case["payload"]]) == 2
    err = json.loads(capsys.readouterr().err)
    assert err["error"] == "unsupported_version"


def test_verify_on_the_command_line_exits_3_when_the_core_cannot_prove_it(monkeypatch, capsys):
    def nobody(*a, **k):
        raise server_identity.IdentityError("could not reach it")
    monkeypatch.setattr(server_identity, "verify_server", nobody)
    assert en._main(["verify", FIXTURE["valid"][0]["payload"]]) == 3
    assert json.loads(capsys.readouterr().err)["error"] == "unverified"


def test_the_module_needs_nothing_outside_the_standard_library():
    """The installer runs it with the system python3, before the venv."""
    import ast

    tree = ast.parse((ROOT / "satellite" / "enrolment.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "json", "re", "sys", "unicodedata", "urllib",
                        "dataclasses", "ipaddress", "satellite"}
