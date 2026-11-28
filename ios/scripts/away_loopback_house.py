"""A real 1.5.0 house for the iPhone's away chat end-to-end test, on loopback.

    TEST_DATABASE_URL=postgresql+asyncpg://domovoi:domovoi@127.0.0.1:6433/domovoi_test \
    AWAY_E2E_CONFIG=/tmp/away-e2e.json \
        python -m pytest ios/scripts/away_loopback_house.py -q -s      # from the repo root

then, while it waits, in another shell:

    AWAY_E2E_CONFIG=/tmp/away-e2e.json swift test --package-path ios/Packages/DomovoiKit \
        --filter AwayLoopbackE2ETests

The house is the real one, as `domovoi/tests/test_away_integration.py` runs it
(``domovoi/away/loopback.py``): a claimed house with a registered phone
holding its device key, the dashboard's API served on 127.0.0.1 (so the phone
enrols over HTTP exactly as at home, `POST /api/away/enroll`), and the real
away listener on 127.0.0.1 wired to the core's relay and the web's chat code
against the TEST database, with the tests' echoing model. No Tor: the phone
reaches the listener with the DEBUG-only direct transport.

It writes `AWAY_E2E_CONFIG` (the two ports and the phone's household token,
device id and device key; test values minted here), keeps the listener's
phone list in step with the database (what the core does on every change),
and stops when the phone writes `<config>.done`, or after five minutes.

Not collected by the repo's pytest run (`testpaths`); pass the file. The
root conftest keeps HOME in a temp folder, as for every test.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

# First: it points DATABASE_URL at the test database before anything reads it.
from domovoi.tests.conftest import requires_db  # isort: skip

from domovoi.away import keys as away_keys
from domovoi.away.loopback import AwayLoopback
from domovoi.friend_houses import service as svc
from domovoi.tests.auth_testkit import HEADER, _db, web_client  # noqa: F401 — fixture
from domovoi.tests.away_kit import KEY, away_db, claimed, phone  # noqa: F401 — fixture
from domovoi.tests.test_away_bridge import house  # noqa: F401 — fixture

pytestmark = [requires_db, pytest.mark.asyncio]

WEB_PORT = int(os.environ.get("AWAY_E2E_WEB_PORT", "6601"))
WAIT_SEC = float(os.environ.get("AWAY_E2E_WAIT_SEC", "300"))


@pytest.fixture(autouse=True)
def fresh_relay(monkeypatch):
    from domovoi.away import bridge

    monkeypatch.setitem(bridge._TOKEN, "value", None)
    yield
    bridge.stop_all()


async def test_serve_a_house_for_the_iphone(house, tmp_path) -> None:  # noqa: F811
    import uvicorn

    from web.backend.main import app as web_app

    config_path = Path(os.environ["AWAY_E2E_CONFIG"])
    done = Path(str(config_path) + ".done")
    done.unlink(missing_ok=True)
    async with web_client() as c:
        base, _, _ = await claimed(c)
        p = await phone(c, base, prefix="ios")
    house_key = away_keys.noise_key(create=True)
    server = uvicorn.Server(uvicorn.Config(web_app, host="127.0.0.1", port=WEB_PORT, log_level="warning",
                                           lifespan="off"))
    serving = asyncio.get_running_loop().create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.05)
        async with AwayLoopback(tmp_path / "listener", house_key=house_key) as loop:
            await svc._write_phones(loop.folder)
            config_path.write_text(json.dumps({
                "web": f"http://127.0.0.1:{WEB_PORT}",
                "listener_port": loop.port,
                "token_header": HEADER,
                "token": base[HEADER],
                "device_id": p["id"],
                "device_key": p["headers"][KEY],
            }), encoding="utf-8")
            print(f"\naway house ready: web :{WEB_PORT}, listener :{loop.port}, config {config_path}", flush=True)
            waited = 0.0
            while not done.exists() and waited < WAIT_SEC:
                # What the core does on every change: the listener's list,
                # then the watch that cuts a revoked phone off.
                await svc._write_phones(loop.folder)
                loop.state.watch_once()
                await asyncio.sleep(0.2)
                waited += 0.2
            events = [e["event"] for e in loop.events]
            print(f"away house: {events.count('away')} requests relayed, {events.count('away_closed')} connections closed",
                  flush=True)
            assert done.exists(), "the phone never said it was done"
            print("away house: the phone says " + done.read_text(encoding="utf-8").strip(), flush=True)
    finally:
        server.should_exit = True
        await serving
        config_path.unlink(missing_ok=True)
        done.unlink(missing_ok=True)
