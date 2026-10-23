"""The card helper's one-time pass (owner request 2026-10-22: the Windows
tray fetches the setup network's key itself, so nobody types it).

A zip build of a Wi-Fi setup card carries ``domovoi/claim.json`` (job id +
a 256-bit secret + when it stops working) and still no plaintext password
(WEB-1). ``POST /api/satellites/media/claim`` trades it once, to a loopback
peer only, within a day, while the web process still remembers the build.
After a restart the admin route ``POST /jobs/{id}/setup-key`` makes a new
key for the same network.

The claim route is DB-free (it reads process memory only), so most of this
runs anywhere; the setup-key route's restart path reads the job row and is
``requires_db``."""

from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import web.backend.api.satellite_media as media_api
from domovoi.db.session import engine
from domovoi.satellite_media import builder, payload
from domovoi.tests.conftest import requires_db
from web.backend.main import app

BASE = "http://127.0.0.1:6369"
HDR = {"X-Requested-With": "domovoi-tests"}


def _client(peer: str = "127.0.0.1") -> TestClient:
    # No `with`: no lifespan, so nothing here needs the database.
    return TestClient(app, base_url=BASE, headers=HDR, client=(peer, 50123))


@pytest.fixture(autouse=True)
def _clean_memory():
    media_api._JOB_CREDENTIALS.clear()
    media_api._JOB_CLAIMS.clear()
    yield
    media_api._JOB_CREDENTIALS.clear()
    media_api._JOB_CLAIMS.clear()


def _remember(job_id: int = 7, psk: str = "k7tqmzr4wd") -> dict:
    """A finished zip build of a Wi-Fi setup card, as _run_build leaves it."""
    in_zip, kept = media_api._new_claim(job_id)
    media_api._JOB_CLAIMS[job_id] = kept
    media_api._remember_credentials(job_id, {
        "ap": {"ssid": "Domovoi-Setup-3FA9", "psk": psk},
        "console": {"username": "domovoi", "password": "consolepw22"},
    })
    return in_zip


def _claim(client: TestClient, pass_: dict, **headers):
    return client.post(
        "/api/satellites/media/claim",
        json={"job_id": pass_["job_id"], "secret": pass_["secret"]},
        headers=headers or None,
    )


# ─── the zip ───────────────────────────────────────────────────────────


def _fake_payload(monkeypatch, tmp_path):
    async def _assemble(workspace, **kw):
        d = Path(workspace) / "payload"
        d.mkdir(parents=True, exist_ok=True)
        return {"dir": d, "warnings": [], "plugins": []}

    def _finalize(workspace, payload_dir, stage2, status_helper=None):
        tar = Path(workspace) / "payload.tar.gz"
        tar.write_bytes(b"not really a tar")
        return {"tar": tar, "sha256": "0" * 64, "bytes": tar.stat().st_size}

    monkeypatch.setattr(payload, "assemble", _assemble)
    monkeypatch.setattr(payload, "finalize", _finalize)
    monkeypatch.setattr(builder, "BUILDS_ROOT", tmp_path / "builds")


def _build_zip(transport: str, claim: dict | None, job: str) -> tuple[dict, Path]:
    result = asyncio.run(builder.build(
        board_id="pi02w", mic_profile="respeaker_2mic_hat_v2",
        setup_transport=transport, target_kind="zip", target_mount=None,
        job_id=job, offline=False, claim=claim,
    ))
    return result, Path(result["artifact_path"])


def test_a_wifi_setup_zip_carries_the_pass_and_no_password(tmp_path, monkeypatch):
    _fake_payload(monkeypatch, tmp_path)
    in_zip, kept = media_api._new_claim(41)
    result, artifact = _build_zip("portal", in_zip, "41")
    with zipfile.ZipFile(artifact) as zf:
        names = zf.namelist()
        blob = b"".join(zf.read(n) for n in names if not n.endswith(".tar.gz"))
        claim = json.loads(zf.read(builder.CLAIM_JSON_PATH))
    assert builder.CLAIM_JSON_PATH in names
    assert claim == in_zip
    assert claim["job_id"] == 41 and len(claim["secret"]) >= 43   # 256 bits
    assert claim["expires_at"].endswith("Z")
    # Still no plaintext password anywhere in it (WEB-1).
    creds = result["credentials"]
    assert "domovoi/ap.json" not in names and "domovoi/console.json" not in names
    assert creds["ap"]["psk"].encode() not in blob
    assert creds["console"]["password"].encode() not in blob
    # The server keeps only the secret's hash.
    assert in_zip["secret"] not in json.dumps(kept)


def test_a_usb_zip_and_a_zip_without_a_pass_carry_no_claim(tmp_path, monkeypatch):
    _fake_payload(monkeypatch, tmp_path)
    in_zip, _ = media_api._new_claim(42)
    _, usb = _build_zip("usb", in_zip, "42")          # no setup network
    _, older = _build_zip("portal", None, "43")       # as before this change
    for artifact in (usb, older):
        with zipfile.ZipFile(artifact) as zf:
            assert builder.CLAIM_JSON_PATH not in zf.namelist()


def test_a_drive_build_still_writes_ap_json(tmp_path, monkeypatch):
    """A card written straight to a drive gets ap.json itself, as before,
    and no claim (it needs none)."""
    _fake_payload(monkeypatch, tmp_path)
    monkeypatch.setattr(builder.overlay, "looks_like_pi_boot", lambda m, marker: True)
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "config.txt").write_text("dtparam=audio=on\n")
    (boot / "cmdline.txt").write_text("console=tty1 rootwait\n")
    in_zip, _ = media_api._new_claim(44)
    asyncio.run(builder.build(
        board_id="pi02w", mic_profile="respeaker_2mic_hat_v2",
        setup_transport="portal", target_kind="drive", target_mount=boot,
        job_id="44", offline=False, claim=in_zip,
    ))
    assert (boot / "domovoi" / "ap.json").is_file()
    assert not (boot / "domovoi" / "claim.json").exists()


# ─── the claim ─────────────────────────────────────────────────────────


def test_the_pass_works_once_from_loopback():
    p = _remember()
    r = _claim(_client(), p)
    assert r.status_code == 200, r.text
    assert r.json() == {"ap": {"ssid": "Domovoi-Setup-3FA9", "psk": "k7tqmzr4wd"}}
    assert "console" not in r.json()                 # the card helper doesn't need it
    assert r.headers["cache-control"] == "no-store"
    again = _claim(_client(), p)
    assert again.status_code == 409
    assert again.json()["reason"] == "used"
    assert again.json()["detail"] == "this card's one-time pass was already used"
    # The dashboard still shows the key: the person types it on their phone.
    assert media_api._JOB_CREDENTIALS[7]["ap"]["psk"] == "k7tqmzr4wd"


def test_the_pass_expires_after_a_day(monkeypatch):
    p = _remember()
    start = media_api._JOB_CLAIMS[7]["expires"] - media_api.CLAIM_TTL_SEC
    monkeypatch.setattr(media_api, "_now", lambda: start + media_api.CLAIM_TTL_SEC + 1)
    r = _claim(_client(), p)
    assert r.status_code == 410
    assert r.json()["reason"] == "expired"
    assert media_api._JOB_CLAIMS[7]["used"] is False   # an expired try spends nothing
    monkeypatch.setattr(media_api, "_now", lambda: start + media_api.CLAIM_TTL_SEC - 60)
    assert _claim(_client(), p).status_code == 200


@pytest.mark.parametrize("peer", ["192.168.1.50", "10.0.0.7", "fe80::1", "testclient", "0.0.0.0"])
def test_the_pass_is_refused_from_anywhere_but_this_pc(peer):
    p = _remember()
    r = _claim(_client(peer), p)
    assert r.status_code == 403
    assert r.json()["reason"] == "not_local"
    assert media_api._JOB_CLAIMS[7]["used"] is False


@pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded", "X-Real-IP", "X-Forwarded-Host"])
def test_a_loopback_peer_relaying_for_someone_else_is_refused(header):
    """WIN-7: a loopback peer is trusted for X-Forwarded-For on WSL, so a
    forwarding header means the peer can't be taken at its word. A LAN
    caller claiming to be loopback gets nowhere either."""
    p = _remember()
    value = "for=127.0.0.1" if header == "Forwarded" else "127.0.0.1"
    assert _claim(_client(), p, **{header: value}).json()["reason"] == "not_local"
    assert _claim(_client("192.168.1.50"), p, **{header: value}).json()["reason"] == "not_local"
    assert media_api._JOB_CLAIMS[7]["used"] is False


def test_the_ipv6_loopback_counts_as_this_pc():
    p = _remember()
    assert _claim(_client("::1"), p).status_code == 200
    p = _remember(job_id=8)
    assert _claim(_client("::ffff:127.0.0.1"), p).status_code == 200


def test_a_wrong_secret_is_refused_and_spends_nothing():
    p = _remember()
    r = _claim(_client(), {"job_id": 7, "secret": "x" * 43})
    assert r.status_code == 403 and r.json()["reason"] == "invalid"
    assert _claim(_client(), p).status_code == 200


def test_a_restarted_server_says_it_forgot_with_a_plain_410():
    p = _remember()
    media_api._JOB_CREDENTIALS.clear()            # what a restart leaves
    media_api._JOB_CLAIMS.clear()
    r = _claim(_client(), p)
    assert r.status_code == 410
    assert r.json() == {
        "detail": "the server restarted since this card was prepared, so it no longer has the password",
        "reason": "forgotten",
    }


def test_a_pass_outlives_neither_its_credentials():
    """Evicted by the cap like the credentials: the pass goes with them."""
    p = _remember(job_id=1)
    for j in range(2, 2 + media_api._CREDENTIAL_CAP):
        media_api._remember_credentials(j, {"ap": None, "console": {"username": "d", "password": "p"}})
    assert 1 not in media_api._JOB_CLAIMS
    assert _claim(_client(), p).json()["reason"] == "forgotten"


def test_the_claim_needs_the_preflight_header_like_every_write():
    p = _remember()
    c = TestClient(app, base_url=BASE, client=("127.0.0.1", 50123))
    r = c.post("/api/satellites/media/claim", json={"job_id": 7, "secret": p["secret"]})
    assert r.status_code == 403
    assert media_api._JOB_CLAIMS[7]["used"] is False


# ─── the admin route (the tray's "Sign in as admin to fetch it") ──────


def test_the_setup_key_route_answers_the_remembered_key():
    _remember()
    r = _client("192.168.1.50").post(
        "/api/satellites/media/jobs/7/setup-key", json={"ssid": "Domovoi-Setup-3FA9"},
    )
    # Pre-setup grace on a fresh test app, or a real admin session: either
    # way it's the admin tier, not the loopback rule.
    if r.status_code == 401:
        pytest.skip("this test database has an admin password; covered by the db test below")
    assert r.status_code == 200, r.text
    # The build's own console login, unchanged, and no userconf line: the
    # zip's userconf.txt already carries its hash.
    assert r.json() == {
        "ap": {"ssid": "Domovoi-Setup-3FA9", "psk": "k7tqmzr4wd"},
        "console": {"username": "domovoi", "password": "consolepw22"},
        "userconf": None,
        "reissued": False,
    }
    r = _client().post("/api/satellites/media/jobs/7/setup-key", json={"ssid": "Domovoi-Setup-0000"})
    assert r.status_code == 409


def test_the_setup_key_route_refuses_a_name_that_isnt_a_setup_network():
    r = _client().post("/api/satellites/media/jobs/7/setup-key", json={"ssid": "HomeWifi"})
    assert r.status_code in (401, 422)


def _truncate_jobs() -> None:
    async def _t():
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE satellite_media_jobs RESTART IDENTITY"))

    asyncio.run(_t())


def _insert_job(kind: str, status: str) -> int:
    async def _i():
        async with engine.begin() as conn:
            row = (await conn.execute(text(
                "INSERT INTO satellite_media_jobs (board, mic_profile, target_kind, "
                "target_ref, offline, status, status_text) VALUES "
                "('pi02w', 'none', :k, :t, true, :s, 'x') RETURNING id"
            ), {"k": kind, "t": "zip" if kind == "zip" else "tok", "s": status})).first()
            return int(row[0])

    return asyncio.run(_i())


FAKE_DIGEST = "$6$abcdefgh12345678$" + "a" * 86


@requires_db
def test_after_a_restart_an_admin_gets_a_new_key_for_the_same_network(monkeypatch):
    """And a new console login: the zip's is only a hash, so after a restart
    it is unknown; the card the tray is writing hasn't booted, so a new
    password, with the userconf.txt line that carries its hash, works."""
    hashed = []
    monkeypatch.setattr(media_api.overlay, "hash_password",
                        lambda pw: hashed.append(pw) or FAKE_DIGEST)
    _truncate_jobs()
    try:
        job = _insert_job("zip", "done")
        with TestClient(app, base_url=BASE, headers=HDR) as client:
            r = client.post(f"/api/satellites/media/jobs/{job}/setup-key",
                            json={"ssid": "Domovoi-Setup-3FA9"})
            if r.status_code == 401:
                pytest.skip("the shared test database has an admin password set")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["reissued"] is True
            assert body["ap"]["ssid"] == "Domovoi-Setup-3FA9"
            assert len(body["ap"]["psk"]) == 10
            console = body["console"]
            assert console["username"] == "domovoi" and len(console["password"]) == media_api.overlay._CONSOLE_PASSWORD_LEN
            assert hashed == [console["password"]]          # hashed the way the build does
            assert body["userconf"] == f"domovoi:{FAKE_DIGEST}\n"
            # The dashboard's "show setup details" shows the new ones, and
            # not the userconf line (it's for the card writer).
            shown = client.get(f"/api/satellites/media/jobs/{job}/credentials").json()
            assert shown["ap"] == body["ap"] and shown["console"] == console
            assert shown["reissued"] is True and "userconf" not in shown
            row = next(j for j in client.get("/api/satellites/media/jobs").json() if j["id"] == job)
            assert row["has_credentials"] is True
            # Asked again: the same key, not a third one.
            again = client.post(f"/api/satellites/media/jobs/{job}/setup-key",
                                json={"ssid": "Domovoi-Setup-3FA9"}).json()
            assert again == body                            # with its userconf line, for a second card

            # Nothing to recover: no such build, a drive build, an unfinished one.
            drive = _insert_job("drive", "done")
            running = _insert_job("zip", "running")
            for j in (9999, drive, running):
                r = client.post(f"/api/satellites/media/jobs/{j}/setup-key",
                                json={"ssid": "Domovoi-Setup-3FA9"})
                assert r.status_code == 404, (j, r.text)
                assert r.json()["detail"] == media_api._CANT_RECOVER_DETAIL
    finally:
        _truncate_jobs()


@requires_db
def test_prepare_to_claim_end_to_end(monkeypatch, tmp_path):
    """The real _run_build: a portal zip's pass is minted, written by the
    builder, remembered as a hash, and trades once for the build's key."""
    _truncate_jobs()
    _fake_payload(monkeypatch, tmp_path)
    try:
        with TestClient(app, base_url=BASE, headers=HDR, client=("127.0.0.1", 50123)) as client:
            r = client.post("/api/satellites/media/prepare", json={
                "board": "pi02w", "mic_profile": "respeaker_2mic_hat_v2",
                "target": {"kind": "zip"}, "setup_transport": "portal",
            })
            if r.status_code == 401:
                pytest.skip("the shared test database has an admin password set")
            assert r.status_code == 200, r.text
            job = r.json()["job"]["id"]
            import time as _t

            for _ in range(200):
                rows = client.get("/api/satellites/media/jobs").json()
                if rows and rows[0]["status"] in ("done", "failed"):
                    break
                _t.sleep(0.1)
            assert rows[0]["status"] == "done", rows[0]
            assert job in media_api._JOB_CLAIMS
            dl = client.get(f"/api/satellites/media/jobs/{job}/download")
            artifact = tmp_path / "got.zip"
            artifact.write_bytes(dl.content)
            with zipfile.ZipFile(artifact) as zf:
                p = json.loads(zf.read(builder.CLAIM_JSON_PATH))
            assert p["job_id"] == job
            got = _claim(client, p)
            assert got.status_code == 200, got.text
            assert got.json()["ap"] == media_api._JOB_CREDENTIALS[job]["ap"]
            assert _claim(client, p).json()["reason"] == "used"
    finally:
        _truncate_jobs()


def test_the_tray_knows_every_reason_the_server_gives():
    """The card helper words its message from ``reason``
    (windows/tray/CardKeys.cs, HttpCardKeys.Claim): each one the server can
    send that has its own words there is named, and the rest fall to
    "didn't accept"."""
    cs = (Path(__file__).resolve().parents[2] / "windows" / "tray" / "CardKeys.cs").read_text(encoding="utf-8")
    for reason in ("used", "expired", "forgotten"):
        assert reason in media_api._CLAIM_REFUSALS
        assert f'case "{reason}":' in cs, reason
    for words in (
        "This card's one-time pass was already used.",
        "This card was prepared more than a day ago, so its pass expired.",
        "Your Domovoi server restarted since this card was prepared, so it no longer has the password.",
        "Can't reach your Domovoi server.",
    ):
        assert words in cs, words
    assert media_api._CANT_RECOVER_DETAIL in cs


@requires_db
def test_with_no_way_to_hash_the_console_login_stays_the_zips(monkeypatch):
    """No openssl: the setup key is still made again; the console login
    can't be, so none is sent and the card keeps the zip's userconf.txt."""
    monkeypatch.setattr(media_api.overlay, "hash_password", lambda pw: None)
    _truncate_jobs()
    try:
        job = _insert_job("zip", "done")
        with TestClient(app, base_url=BASE, headers=HDR) as client:
            r = client.post(f"/api/satellites/media/jobs/{job}/setup-key",
                            json={"ssid": "Domovoi-Setup-3FA9"})
            if r.status_code == 401:
                pytest.skip("the shared test database has an admin password set")
            body = r.json()
            assert body["reissued"] is True and len(body["ap"]["psk"]) == 10
            assert body["console"] is None and body["userconf"] is None
    finally:
        _truncate_jobs()


@requires_db
def test_the_jobs_list_says_which_builds_still_have_setup_details():
    """After a restart the dashboard shows a plain line instead of a "show
    setup details" button that would open nothing."""
    _truncate_jobs()
    try:
        kept = _insert_job("zip", "done")
        cleared = _insert_job("zip", "done")
        media_api._remember_credentials(kept, {
            "ap": {"ssid": "Domovoi-Setup-3FA9", "psk": "k7tqmzr4wd"},
            "console": {"username": "domovoi", "password": "consolepw22"},
        })
        with TestClient(app, base_url=BASE, headers=HDR) as client:
            r = client.get("/api/satellites/media/jobs")
            if r.status_code == 401:
                pytest.skip("the shared test database has an admin password set")
            rows = {j["id"]: j for j in r.json()}
            assert rows[kept]["has_credentials"] is True
            assert rows[cleared]["has_credentials"] is False
    finally:
        _truncate_jobs()


def test_the_dashboard_row_never_offers_a_button_with_nothing_behind_it():
    """Owner rule: no misleading controls. The row's button is gated on
    has_credentials, and the cleared case reads the agreed line."""
    jsx = (Path(__file__).resolve().parents[2] / "web" / "static" / "satellite_media.jsx").read_text(encoding="utf-8")
    assert "j.has_credentials !== false" in jsx
    assert ("Setup details were cleared when Domovoi restarted. Write this card with the tray "
            "icon (it can sign in as admin to make a new password).") in jsx
    # The row names its build number, which the tray shows too.
    assert "#{j.id} · {j.board} · {j.mic_profile}" in jsx
    cs = (Path(__file__).resolve().parents[2] / "windows" / "tray" / "CardZip.cs").read_text(encoding="utf-8")
    assert '"Dashboard build " + (Claim != null ? "#"' in cs
