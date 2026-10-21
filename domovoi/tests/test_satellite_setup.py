"""The Windows satellite Setup on the core (domovoi/satellite_setup.py and
the /v1/satellite-setup routes, 2026-10-21): setup codes (what they name,
how long, what a person may type), the download's file name the Setup
reads back, the fetch from the GitHub release held to its SHA256SUMS and
refused when internet access is off, and the routes' tiers. No DB, no
network: downloads are fakes and the cache a tmp dir.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi import HTTPException

from domovoi import main, satellite_setup as ss
from satellite import enrolment

FP = "SHA256:4gq+pNW/lnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA"
CORE = "http://192.168.1.20:6370"
PAYLOAD = enrolment.build(CORE, FP, "Kid's Room")
EXE = b"MZ" + b"\x90" * 4000


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    from domovoi import satellite_windows
    from domovoi.satellite_media import cache as media_cache

    monkeypatch.setattr(media_cache, "CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(ss, "windows_install", lambda: False)
    satellite_windows._verified.clear()
    ss.forget_codes()
    ss.forget_probe()
    yield
    ss.forget_codes()
    ss.forget_probe()


# ─── codes ────────────────────────────────────────────────────────────────

def test_a_code_names_its_payload_for_24_hours():
    made = ss.make_code(PAYLOAD, now=1000.0)
    code = made["code"]
    assert len(code) == 12 and all(c in ss.ALPHABET for c in code)
    assert code == ss.code_for(PAYLOAD), "the code is the payload's own hash"
    assert made["expires_at"] == 1000.0 + 24 * 3600 and made["room_id"] == "kids_room"
    assert ss.lookup(code, now=1000.0 + 24 * 3600 - 1) == PAYLOAD
    assert ss.lookup(code, now=1000.0 + 24 * 3600) is None, "expired"
    assert ss.lookup(code, now=1000.0) is None, "and gone"


def test_asking_again_for_the_same_room_gives_the_same_code_renewed():
    first = ss.make_code(PAYLOAD, now=0.0)
    again = ss.make_code(PAYLOAD, now=3600.0)
    assert again["code"] == first["code"] and again["expires_at"] == 3600.0 + ss.CODE_TTL_SEC
    other = ss.make_code(enrolment.build(CORE, FP, "Den"), now=3600.0)
    assert other["code"] != first["code"]


@pytest.mark.parametrize("typed", ["k7m4-qx2p-9tra", " K7M4 QX2P 9TRA ", "K7M4QX2P9TRA"])
def test_a_typed_code_ignores_case_dashes_and_spaces(typed):
    assert ss.normalize_code(typed) == "K7M4QX2P9TRA"


def test_look_alike_letters_read_as_digits_and_nonsense_is_no_code():
    assert ss.normalize_code("1O1O-ILIL-OOOO") == "101011110000"
    for bad in ("", "K7M4", "K7M4QX2P", "K7M4QX2P9TRAX", "K7M4-QX2P-9TRU", "../../etc", "K7M4QX2P" * 5, None):
        assert ss.normalize_code(bad) is None


def test_the_code_is_60_bits_of_the_payload_s_sha256():
    """The worker's twin (windows/installer/satellite-setup.ps1 CodeFor) is
    held to the same value in test_windows_satellite_setup.py."""
    digest = hashlib.sha256(PAYLOAD.encode("utf-8")).digest()
    bits = format(int.from_bytes(digest[:8], "big"), "064b")[:60]
    assert ss.code_for(PAYLOAD) == "".join(ss.ALPHABET[int(bits[i:i + 5], 2)] for i in range(0, 60, 5))
    assert ss.code_for(PAYLOAD) != ss.code_for(enrolment.build(CORE, FP, "Kid's Room 2"))


def test_only_an_enrolment_payload_gets_a_code():
    for bad in ("hello", "domovoi-room:1?core=http://8.8.8.8:6370&fp=" + FP + "&room=x"):
        with pytest.raises(ss.SetupCodeError):
            ss.make_code(bad)


def test_live_codes_are_capped_oldest_first():
    first = ss.make_code(enrolment.build(CORE, FP, "Room 0"), now=0.0)["code"]
    for i in range(1, ss.MAX_CODES + 1):
        ss.make_code(enrolment.build(CORE, FP, f"Room {i}"), now=float(i))
    assert ss.lookup(first, now=100.0) is None
    assert len(ss._codes) == ss.MAX_CODES


@pytest.mark.parametrize(("core", "name", "server"), [
    ("http://192.168.1.20:6370", "DomovoiSatelliteSetup_K7M4QX2P9TRA_192.168.1.20_6370.exe", "192.168.1.20"),
    ("http://domovoi.local:6400", "DomovoiSatelliteSetup_K7M4QX2P9TRA_domovoi.local_6400.exe",
     "domovoi.local:6400"),
    ("http://[fd00::17]:6370", "DomovoiSatelliteSetup_K7M4QX2P9TRA.exe", "[fd00::17]"),
    ("https://domovoi.lan:6370", "DomovoiSatelliteSetup_K7M4QX2P9TRA.exe", "https://domovoi.lan:6370"),
])
def test_the_file_name_carries_what_fits_in_one(core, name, server):
    assert ss.download_name("K7M4QX2P9TRA", core) == name
    assert ss.server_text(core) == server


def test_the_file_name_is_read_back_by_the_setup_s_own_pattern():
    """The worker's regex (windows/installer/satellite-setup.ps1) takes
    every name this module makes."""
    import re

    worker = (Path(main.__file__).resolve().parents[1] / "windows" / "installer" / "satellite-setup.ps1").read_text()
    pattern = re.search(r"\[regex\]::Match\(\$leaf, '([^']+)'", worker).group(1)
    for core in ("http://192.168.1.20:6370", "http://domovoi.local:6400", "https://domovoi.lan"):
        assert re.match(pattern, ss.download_name("K7M4QX2P9TRA", core), re.I), core


# ─── the Setup file from the release ─────────────────────────────────────

def test_sha256sums_are_read_like_sha256sum_writes_them():
    sha = "ab" * 32
    text = f"{'cd' * 32}  DomovoiSetup.exe\n{sha} *DomovoiSatelliteSetup.exe\n"
    assert ss.parse_sums(text) == sha
    assert ss.parse_sums(f"{'cd' * 32}  DomovoiSetup.exe\n") is None


def test_the_sources_are_this_release_then_the_latest(monkeypatch):
    assert ss.sources("v1.0.0") == [("v1.0.0", ss.RELEASE_DOWNLOAD + "v1.0.0/"), ("latest", ss.LATEST_DOWNLOAD)]
    assert ss.sources(None) == [("latest", ss.LATEST_DOWNLOAD)]
    assert ss.sources("v1/../../x") == [("latest", ss.LATEST_DOWNLOAD)]
    assert all(base.startswith("https://github.com/coders-farm-official/domovoi/releases/")
               for _, base in ss.sources("v9.9.9"))


class FakeRelease:
    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.asked: list[str] = []

    def __call__(self, url, dest, limit):
        self.asked.append(url)
        body = self.files.get(url)
        if body is None:
            raise ValueError("HTTP 404")
        if len(body) > limit:
            raise ValueError(f"larger than {limit} bytes")
        if dest is None:
            return body
        Path(dest).write_bytes(body)
        return hashlib.sha256(body).hexdigest()


def _sums(body: bytes) -> bytes:
    return f"{hashlib.sha256(body).hexdigest()}  DomovoiSatelliteSetup.exe\n".encode()


def test_the_fetch_keeps_the_setup_its_release_vouches_for(monkeypatch):
    base = ss.RELEASE_DOWNLOAD + "v9.9.9/"
    rel = FakeRelease({base + "SHA256SUMS": _sums(EXE), base + "DomovoiSatelliteSetup.exe": EXE})
    monkeypatch.setattr(ss, "_get", rel)
    monkeypatch.setattr(ss, "release_tag", lambda: "v9.9.9")
    ok, message = ss.fetch()
    assert ok, message
    assert ss.cached_file().read_bytes() == EXE
    st = ss.status()
    assert st["ready"] and st["release"] == "v9.9.9" and st["size"] == len(EXE)


def test_a_release_without_the_setup_falls_back_to_the_latest(monkeypatch):
    old = ss.RELEASE_DOWNLOAD + "v1.0.0-rc3/"
    rel = FakeRelease({old + "SHA256SUMS": b"00" * 32 + b"  DomovoiSetup.exe\n",
                       ss.LATEST_DOWNLOAD + "SHA256SUMS": _sums(EXE),
                       ss.LATEST_DOWNLOAD + "DomovoiSatelliteSetup.exe": EXE})
    monkeypatch.setattr(ss, "_get", rel)
    monkeypatch.setattr(ss, "release_tag", lambda: "v1.0.0-rc3")
    ok, message = ss.fetch()
    assert ok and "latest" in message
    assert old + "DomovoiSatelliteSetup.exe" not in rel.asked


@pytest.mark.parametrize("served", [EXE + b"x", b"PK" + EXE[2:]], ids=["not_the_listed_one", "not_a_program"])
def test_a_setup_its_release_does_not_vouch_for_is_never_kept(monkeypatch, served):
    base = ss.RELEASE_DOWNLOAD + "v9.9.9/"
    sums = _sums(EXE) if served.startswith(b"MZ") else _sums(served)
    rel = FakeRelease({base + "SHA256SUMS": sums, base + "DomovoiSatelliteSetup.exe": served,
                       ss.LATEST_DOWNLOAD + "SHA256SUMS": sums,
                       ss.LATEST_DOWNLOAD + "DomovoiSatelliteSetup.exe": served})
    monkeypatch.setattr(ss, "_get", rel)
    monkeypatch.setattr(ss, "release_tag", lambda: "v9.9.9")
    ok, message = ss.fetch()
    assert not ok and ss.cached_file() is None and not ss.status()["ready"]
    assert not list((ss._bucket()).glob("*.download"))


def test_bytes_changed_after_the_fetch_are_not_served(monkeypatch):
    test_the_fetch_keeps_the_setup_its_release_vouches_for(monkeypatch)
    (ss._bucket() / ss.SETUP_ASSET).write_bytes(EXE[:-1] + b"!")
    from domovoi import satellite_windows

    satellite_windows._verified.clear()
    assert ss.cached_file() is None


def test_the_fetch_is_refused_when_internet_access_is_off(monkeypatch):
    from domovoi import egress

    monkeypatch.setattr(egress, "internet_turned_off", lambda: True)
    monkeypatch.setattr(ss, "_get", lambda *a, **k: pytest.fail("no download under never"))
    ok, message = ss.fetch()
    assert not ok and "internet access is off" in message and "cached" not in message
    st = ss.status()
    assert st["fetchable"] is None and st["internet_off"], "and the card offers no button"


def test_a_failed_fetch_is_said_in_plain_words(monkeypatch):
    monkeypatch.setattr(ss, "_get", FakeRelease({}))
    monkeypatch.setattr(ss, "release_tag", lambda: "v9.9.9")
    ok, message = ss.fetch()
    assert not ok and "PowerShell command" in message
    assert "SHA256SUMS" not in message and "HTTP" not in message


def test_a_windows_install_never_fetches_it_its_setup_brings_it(monkeypatch):
    monkeypatch.setattr(ss, "windows_install", lambda: True)
    monkeypatch.setattr(ss, "_get", lambda *a, **k: pytest.fail("a Windows install fetches nothing"))
    ok, message = ss.fetch()
    assert not ok and "DomovoiSetup.exe" in message
    st = ss.status()
    assert st["windows_install"] and st["fetchable"] is None and not st["ready"]


def test_what_the_host_places_is_served_and_said_to_be_bundled():
    """windows/host's record beside the file it placed (DomovoiHost,
    SatelliteSetupPlacement): the same shape the fetch writes."""
    import json

    (ss._bucket() / ss.SETUP_ASSET).write_bytes(EXE)
    (ss._bucket() / ss.RECORD_NAME).write_text(json.dumps(
        {"sha256": hashlib.sha256(EXE).hexdigest(), "size": len(EXE), "release": "bundled", "fetched_at": 1}))
    st = ss.status()
    assert st["ready"] and st["bundled"] and ss.cached_file() is not None


def test_the_button_shows_only_when_a_release_has_the_setup(monkeypatch):
    calls = []
    base = ss.RELEASE_DOWNLOAD + "v9.9.9/"

    def get(url, dest, limit):
        calls.append(url)
        return FakeRelease({base + "SHA256SUMS": _sums(EXE)})(url, dest, limit)

    monkeypatch.setattr(ss, "_get", get)
    monkeypatch.setattr(ss, "release_tag", lambda: "v9.9.9")
    assert ss.status()["fetchable"] == "v9.9.9"
    assert ss.status()["fetchable"] == "v9.9.9" and len(calls) == 1, "asked once, then remembered"
    ss.forget_probe()
    monkeypatch.setattr(ss, "_get", FakeRelease({base + "SHA256SUMS": b"00" * 32 + b"  DomovoiSetup.exe\n"}))
    assert ss.status()["fetchable"] is None, "no release carries it yet: no button"


# ─── the routes ───────────────────────────────────────────────────────────

def _routes():
    from domovoi.tests.route_walk import iter_route_contexts

    return {(m, r.path): r for r in iter_route_contexts(main.app.routes) for m in (r.methods or ())}


def test_making_codes_and_fetching_are_an_admin_s_and_the_rest_is_public():
    from domovoi.admin_auth import require_admin_mutation, require_admin_read

    routes = _routes()

    def deps(m, p):
        return [d.dependency for d in routes[(m, p)].dependencies]

    assert require_admin_mutation in deps("POST", "/v1/admin/satellite-setup/codes")
    assert require_admin_mutation in deps("POST", "/v1/admin/satellite-setup/fetch")
    assert require_admin_read in deps("GET", "/v1/admin/satellite-setup")
    for path in ("/v1/satellite-setup/codes/{code}", "/v1/satellite-setup/download/{code}", "/win/{code}"):
        assert deps("GET", path) == [], path
    assert ("GET", "/v1/admin/satellite-setup/codes") not in routes, "no listing of codes"


async def test_a_live_code_answers_with_the_payload_and_the_bundle_digest(tmp_path, monkeypatch):
    from domovoi import server_identity as core_identity
    from satellite import server_identity

    identity = core_identity.load_or_create(tmp_path / "identity.json")
    monkeypatch.setattr(main.server_identity, "load_or_create", lambda *a, **k: identity)
    payload = enrolment.build(CORE, identity.fingerprint, "Kid's Room")
    made = await main.admin_satellite_setup_code(main._SetupCodeBody(payload=payload))
    code = made["code"]
    assert made["code_display"] == f"{code[:4]}-{code[4:8]}-{code[8:]}"
    assert made["server"] == "192.168.1.20" and made["download_path"].endswith(code)
    assert made["file_name"] == f"DomovoiSatelliteSetup_{code}_192.168.1.20_6370.exe"
    got = await main.satellite_setup_code(made["code_display"].lower())
    assert got["payload"] == payload and got["room"] == "Kid's Room" and got["code"] == code
    assert "signature" not in got, "unsigned without a challenge"
    digest = await main.satellite_code_bundle_digest()
    assert got["bundle_sha256"] == digest["sha256"] and got["bundle_path"] == digest["path"]
    # With the Setup's challenge: signed by the identity key the payload names.
    challenge = "0f" * 16
    got = await main.satellite_setup_code(code, challenge=challenge)
    public = server_identity.unb64(got["public_key"])
    assert server_identity.fingerprint_for(public) == identity.fingerprint == enrolment.parse(payload).fp
    message = ss.setup_message(challenge, code, payload, got["bundle_sha256"])
    assert server_identity.verify(public, message, server_identity.unb64(got["signature"]))
    assert not server_identity.verify(public, ss.setup_message("1f" * 16, code, payload, got["bundle_sha256"]),
                                      server_identity.unb64(got["signature"])), "bound to the challenge"
    for bad in ("x" * 32, "0F" * 16, "0f" * 17, "0f\n" + "0" * 29):
        with pytest.raises(HTTPException) as e:
            await main.satellite_setup_code(code, challenge=bad)
        assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        await main.satellite_setup_code("ZZZZZZZZZZZZ")
    assert e.value.status_code == 404 and "expired" in e.value.detail


async def test_a_bad_payload_is_a_422():
    with pytest.raises(HTTPException) as e:
        await main.admin_satellite_setup_code(main._SetupCodeBody(payload="nope"))
    assert e.value.status_code == 422


async def test_the_download_is_named_for_the_code_and_says_plainly_when_it_can_t(monkeypatch):
    code = ss.make_code(PAYLOAD)["code"]
    resp = await main.satellite_setup_download(code)
    assert resp.status_code == 404 and b"doesn't hold the Windows Setup" in resp.body
    test_the_fetch_keeps_the_setup_its_release_vouches_for(monkeypatch)
    for route in (main.satellite_setup_download, main.satellite_setup_short):
        resp = await route(ss.display_code(code))
        assert Path(resp.path) == ss.cached_file()
        assert f'filename="DomovoiSatelliteSetup_{code}_192.168.1.20_6370.exe"' in resp.headers["content-disposition"]
    resp = await main.satellite_setup_download("ZZZZZZZZZZZZ")
    assert resp.status_code == 404 and b"expired" in resp.body


async def test_a_frozen_channel_hands_out_no_codes_and_no_setup(monkeypatch):
    async def frozen(*a, **k):
        raise HTTPException(status_code=423, detail="frozen")

    monkeypatch.setattr(main.container_freeze, "require_unfrozen", frozen)
    code = ss.make_code(PAYLOAD)["code"]
    for call in (main.satellite_setup_code, main.satellite_setup_download, main.satellite_setup_short):
        with pytest.raises(HTTPException) as e:
            await call(code)
        assert e.value.status_code == 423
