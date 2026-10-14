"""The dashboard half of "Turn on wake-word training", and the settings.

* ``GET /v1/admin/wake/trainer`` (and the web's ``/api/wake-words/trainer``)
  is an admin read; ``POST …/enable`` is the security tier at both hops
  (it builds and runs a Docker image and writes the train command), pinned
  in test_route_auth_matrix.SECURITY_TIER_ROUTES too. The web hop forwards
  the caller's credentials and passes the core's 409 reason through.
* The trainer's knobs and the time limit are in Settings → Configuration
  (admin, the advanced section); the command, the image name and the
  docker binary are not (what they name runs as the service user).
* Switching ``wake_word_trainer_enabled`` on starts the worker without a
  restart (it isn't started at boot while off).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from domovoi import admin_auth
from domovoi import wake_trainer_setup as wts
from domovoi.config import Settings, settings
from domovoi.config_schema import FIELD_BY_NAME
from domovoi.main import app as core_app
from domovoi.tests.route_walk import iter_route_contexts


def _gates(app, method: str, path: str) -> list[Any]:
    def calls(dep):
        for d in getattr(dep, "dependencies", ()):
            if d.call is not None:
                yield d.call
            yield from calls(d)

    for rc in iter_route_contexts(app.routes):
        if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or set()):
            return list(calls(getattr(rc, "dependant", None)))
    raise AssertionError(f"{method} {path} is not a route")


def test_the_core_routes_wear_their_tiers():
    assert admin_auth.require_admin_read in _gates(core_app, "GET", "/v1/admin/wake/trainer")
    gates = _gates(core_app, "POST", "/v1/admin/wake/trainer/enable")
    assert admin_auth.require_admin_security in gates
    assert admin_auth.require_admin_mutation not in gates


def test_the_web_routes_wear_their_tiers():
    from web.backend.main import app as web_app

    assert admin_auth.require_admin_read in _gates(web_app, "GET", "/api/wake-words/trainer")
    assert admin_auth.require_admin_security in _gates(web_app, "POST",
                                                       "/api/wake-words/trainer/enable")


@pytest.mark.asyncio
async def test_a_refusal_is_a_409_with_the_reason(monkeypatch):
    from domovoi import main

    async def refuse(cpu_ok=False):
        raise wts.Refused("Only 20 GB of disk is free; training needs about 31 GB plus 5 GB to spare.")

    monkeypatch.setattr(wts, "start", refuse)
    with pytest.raises(HTTPException) as e:
        await main.admin_wake_trainer_enable(main._AdminWakeTrainerEnableBody(cpu_ok=True))
    assert e.value.status_code == 409 and "20 GB of disk" in e.value.detail


def test_the_web_hop_forwards_and_passes_the_reason_through(monkeypatch):
    import web.backend.api.wake_words as wake_api
    from web.backend.main import app as web_app

    seen: dict = {}

    async def fake_post(path, body=None, timeout=30.0, headers=None):
        seen.update(path=path, body=body)
        return 409, {"detail": "Internet access is turned off for this server"}

    async def fake_get(path, timeout=10.0, headers=None):
        seen["get"] = path
        return 200, {"enabled": False, "job": {"state": "idle"}}

    async def admin_ok():
        return None

    monkeypatch.setattr(wake_api, "post_admin", fake_post)
    monkeypatch.setattr(wake_api, "get_admin", fake_get)
    # The gates themselves are pinned above; here an admin is signed in.
    monkeypatch.setitem(web_app.dependency_overrides, admin_auth.require_admin_security, admin_ok)
    monkeypatch.setitem(web_app.dependency_overrides, admin_auth.require_admin_read, admin_ok)
    with TestClient(web_app, headers={"X-Requested-With": "domovoi-tests"}) as client:
        r = client.post("/api/wake-words/trainer/enable", json={"cpu_ok": True})
        g = client.get("/api/wake-words/trainer")
    assert seen["path"] == "/v1/admin/wake/trainer/enable" and seen["body"] == {"cpu_ok": True}
    assert r.status_code == 409 and "Internet access is turned off" in r.text
    assert seen["get"] == "/v1/admin/wake/trainer" and g.status_code == 200
    assert g.json()["job"]["state"] == "idle"


def test_the_knobs_are_in_configuration_and_the_command_is_not():
    for name in ("wake_word_trainer_enabled", "wake_word_train_timeout_sec",
                 "wake_trainer_n_samples", "wake_trainer_n_samples_val", "wake_trainer_steps",
                 "wake_trainer_real_target_fraction", "wake_trainer_cpus",
                 "wake_trainer_memory_gb"):
        spec = FIELD_BY_NAME[name]
        assert spec.section == "advanced" and spec.group == "Wake words", name
        assert name in Settings.model_fields
    for name in ("wake_word_train_command", "wake_trainer_image", "wake_trainer_cache_volume"):
        assert name not in FIELD_BY_NAME, f"{name} would let an HTTP write choose what runs"
    assert FIELD_BY_NAME["wake_word_trainer_enabled"].tier == "reapply"


def test_settings_defaults():
    s = Settings(_env_file=None)
    assert s.wake_word_trainer_enabled is False
    assert s.wake_word_train_timeout_sec == 0          # sized to the server
    assert s.wake_trainer_memory_gb == 12
    assert s.wake_trainer_image == "domovoi-wake-trainer:latest"


@pytest.mark.asyncio
async def test_switching_it_on_starts_the_worker(monkeypatch):
    import asyncio

    from domovoi.plugins_runtime.workers import WorkerRunner
    from domovoi.workers.base import Worker

    class _S:
        flag = False
        loop_sec = 3600.0

    ticks: list[int] = []

    class W(Worker):
        name = "w"
        enabled_setting = "flag"
        interval_setting = "loop_sec"
        stub_suppressed = False

        async def tick(self):
            ticks.append(1)

    runner = WorkerRunner()
    runner.add_worker(W(), owner="t", settings_source=_S())
    await runner.start_owner("t")
    assert runner.status("t")["workers"][0]["state"] == "stopped"
    assert runner.start_worker("t", "w") is False      # still off
    _S.flag = True
    assert runner.start_worker("t", "w") is True
    await asyncio.sleep(0.05)
    assert ticks == [1]
    await runner.stop_owner("t")


def test_the_reapply_hook_is_registered():
    from domovoi import main, reapply

    main._register_core_reapply_hooks()
    assert "wake_word_trainer_enabled" in reapply.registered_fields()
