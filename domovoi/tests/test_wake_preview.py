"""Settings > Wake words > "Hear the training voices".

The phrase spoken by the trainer's own synthetic voices (the trainer image's
piper-sample-generator), never by Domovoi's voice: the core runs the repo
wrapper's ``--preview``, which runs the image's ``entrypoint.py --preview``.
No Docker here: the wrapper's argv and argument handling, the entrypoint's
pure helpers, the core job with the command faked, and the web routes.
"""

from __future__ import annotations

import asyncio
import importlib.util
import random
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domovoi import admin_auth

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts" / "wake_word" / "train_wake_word.py"
ENTRYPOINT = ROOT / "scripts" / "wake_word" / "docker" / "entrypoint.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── the entrypoint's preview mode ─────────────────────────────────────────

def test_preview_voices_come_from_the_trainings_own_speaker_pairs():
    ep = _load(ENTRYPOINT, "_ep_prev")
    S, n, batch = 904, 5000, 50
    voices = ep.preview_voices(S, n, batch, 4, random.Random(7))
    assert len(voices) == 4
    for s1, s2, length in voices:
        k = s1 * S + s2                       # the pair's place in training's walk
        assert 0 <= k < n
        assert length == ep.POSITIVE_LENGTHS[(k // batch) % 3]
    # Different first speakers (training's first 5000 clips span six).
    assert len({v[0] for v in voices}) == 4


def test_preview_voices_fill_up_when_few_first_speakers_exist():
    ep = _load(ENTRYPOINT, "_ep_prev2")
    voices = ep.preview_voices(904, 10, 16, 4, random.Random(1))   # all first speaker 0
    assert len(voices) == 4 and len(set(voices)) == 4
    assert {v[0] for v in voices} == {0}


def test_preview_count_is_bounded():
    ep = _load(ENTRYPOINT, "_ep_prev3")
    assert ep.preview_count(None) == 4
    assert ep.preview_count("2") == 2
    assert ep.preview_count("99") == ep.PREVIEW_MAX
    assert ep.preview_count("0") == 1
    assert ep.preview_count("x") == 4


def test_preview_speaks_with_the_positive_settings_train_py_uses():
    """train.py's --generate_clips passes noise 0.98/0.98 and lengths
    0.75/1.0/1.25 for the training positives (slerp at the generator's 0.5)."""
    ep = _load(ENTRYPOINT, "_ep_prev4")
    assert (ep.POSITIVE_SLERP, ep.POSITIVE_NOISE, ep.POSITIVE_NOISE_W) == (0.5, 0.98, 0.98)
    assert ep.POSITIVE_LENGTHS == (0.75, 1.0, 1.25)


def test_entrypoint_preview_needs_a_phrase(monkeypatch):
    ep = _load(ENTRYPOINT, "_ep_prev5")
    monkeypatch.delenv("WAKE_PHRASE", raising=False)
    monkeypatch.setattr(ep, "lower_priority", lambda: None)
    monkeypatch.setattr(sys, "argv", ["entrypoint.py", "--preview"])
    with pytest.raises(SystemExit) as e:
        ep.main()
    assert e.value.code == 2


# ── the wrapper's --preview ───────────────────────────────────────────────

def test_preview_argv_is_isolated(monkeypatch):
    w = _load(WRAPPER, "_ww_prev")
    argv = w.build_preview_argv("/home/alex/.domovoi/wake_trainer/preview/abc", "hey alex", 4,
                                ["--cpus", "2"])
    assert argv[-2:] == [w.IMAGE, "--preview"]
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "/home/alex/.domovoi/wake_trainer/preview/abc:/data/preview" in argv
    assert not any("/data/clips" in a or "/data/cache" in a for a in argv)
    assert argv[argv.index("--name") + 1] == w.PREVIEW_CONTAINER != w.CONTAINER
    assert f"{w.PREVIEW_LABEL}=1" in argv
    assert not any(a.startswith(f"{w.LABEL}=") for a in argv)     # --stop never hits it
    assert "WAKE_PHRASE=hey alex" in argv and "WAKE_PREVIEW_COUNT=4" in argv
    assert "--gpus" not in argv


def test_preview_argv_carries_the_households_pronunciation(monkeypatch):
    monkeypatch.setenv("WAKE_SYNTH_IPA", "alex=ˈælɛks")
    w = _load(WRAPPER, "_ww_prev_ipa")
    assert "WAKE_SYNTH_IPA=alex=ˈælɛks" in w.build_preview_argv("/x", "hey alex", 3)
    monkeypatch.setenv("WAKE_SYNTH_IPA", "alex=$(boom)")
    w = _load(WRAPPER, "_ww_prev_ipa2")
    assert not any(a.startswith("WAKE_SYNTH_IPA") for a in w.build_preview_argv("/x", "hey alex", 3))


@pytest.mark.parametrize("args", [
    ["--preview", "--out", "/tmp/x"],                       # no phrase
    ["--preview", "--phrase", "hey alex"],                  # no out
    ["--preview", "--phrase", "hey alex", "--out", "/tmp/x", "--count", "9"],
])
def test_wrapper_preview_argument_errors(monkeypatch, args):
    w = _load(WRAPPER, "_ww_prev_args")
    monkeypatch.setattr(sys, "argv", ["train_wake_word.py", *args])
    with pytest.raises(SystemExit) as e:
        w.main()
    assert e.value.code == 2


def test_wrapper_preview_without_docker_says_so(monkeypatch, tmp_path):
    w = _load(WRAPPER, "_ww_prev_nodocker")
    monkeypatch.setattr(w, "docker_available", lambda: False)
    monkeypatch.setattr(sys, "argv", ["train_wake_word.py", "--preview", "--phrase", "hey alex",
                                      "--out", str(tmp_path / "p")])
    assert w.main() == 3


def test_wrapper_preview_runs_and_cleans_only_previews(monkeypatch, tmp_path):
    w = _load(WRAPPER, "_ww_prev_run")
    ran: dict = {}
    monkeypatch.setattr(w, "docker_available", lambda: True)
    monkeypatch.setattr(w, "_docker", lambda args, timeout: type("R", (), {"returncode": 0,
                                                                              "stdout": ""})())
    monkeypatch.setattr(w, "docker_host_resources", lambda: (8, 32 * 2**30))
    monkeypatch.setattr(w, "remove_training_containers",
                        lambda reason: (_ for _ in ()).throw(AssertionError("a training was stopped")))

    def fake_run(argv, deadline, cleanup=None):
        ran.update(argv=argv, cleanup=cleanup)
        return 0

    monkeypatch.setattr(w, "run_container", fake_run)
    monkeypatch.setattr(sys, "argv", ["train_wake_word.py", "--preview", "--phrase", "hey alex",
                                      "--out", str(tmp_path / "p"), "--count", "3"])
    assert w.main() == 0
    assert ran["cleanup"] is w.remove_preview_containers
    assert "WAKE_PREVIEW_COUNT=3" in ran["argv"]
    assert ran["argv"][ran["argv"].index("--cpus") + 1] == "2"
    assert ran["argv"][ran["argv"].index("--memory") + 1] == w.PREVIEW_MEMORY


# ── the core job ──────────────────────────────────────────────────────────

@pytest.fixture()
def core(monkeypatch, tmp_path):
    from domovoi import wake_preview, wake_trainer_setup

    wake_preview.reset_for_tests()
    monkeypatch.setattr(wake_preview.settings, "wake_models_dir", str(tmp_path / "wake_models"))
    monkeypatch.setattr(wake_trainer_setup.settings, "wake_models_dir", str(tmp_path / "wake_models"))
    monkeypatch.setattr(wake_trainer_setup, "enabled", lambda: True)
    monkeypatch.setattr(wake_trainer_setup, "job_running", lambda: False)
    image = {"name": "domovoi-wake-trainer:latest", "present": True, "current": True}

    async def requirements(force=False):
        return {"image": image}

    monkeypatch.setattr(wake_trainer_setup, "requirements", requirements)
    monkeypatch.setattr(wake_trainer_setup, "source_tree_id", lambda: "a" * 40)
    monkeypatch.setattr(
        wake_preview.settings, "wake_word_train_command",
        '"/usr/bin/python3" "/opt/domovoi/scripts/wake_word/train_wake_word.py" --clips '
        '"{clips_dir}" --phrase "{phrase}" --slug "{slug}" --out "{out}"')
    from domovoi.workers import wake_word_trainer as wwt

    monkeypatch.setattr(wwt, "synthesis_ipa", lambda p: "")
    yield wake_preview
    wake_preview.reset_for_tests()


def test_unavailable_when_training_is_off(core, monkeypatch):
    from domovoi import wake_trainer_setup

    monkeypatch.setattr(wake_trainer_setup, "enabled", lambda: False)
    st = asyncio.run(core.status())
    assert st["available"] is False and "Turn on wake-word training" in st["unavailable_reason"]
    with pytest.raises(core.Refused):
        asyncio.run(core.start("hey alex"))


def test_unavailable_with_a_hand_written_command(core, monkeypatch):
    monkeypatch.setattr(core.settings, "wake_word_train_command", "mytrainer {clips_dir} {out}")
    ok, why = asyncio.run(core.available())
    assert ok is False and "Domovoi's own trainer" in why


@pytest.mark.parametrize("present, current, src, ok, says", [
    (True, False, "a" * 40, False, "older version"),
    (False, False, "a" * 40, False, "missing"),
    (True, True, "a" * 40, True, None),
    (True, False, "unknown", True, None),     # a checkout git can't name: let the run say
])
def test_an_older_trainer_image_has_no_voices_yet(core, monkeypatch, present, current, src, ok, says):
    from domovoi import wake_trainer_setup

    async def requirements(force=False):
        return {"image": {"present": present, "current": current}}

    monkeypatch.setattr(wake_trainer_setup, "requirements", requirements)
    monkeypatch.setattr(wake_trainer_setup, "source_tree_id", lambda: src)
    got, why = asyncio.run(core.available())
    assert got is ok
    if says:
        assert says in why


def test_preview_argv_uses_the_configured_wrapper(core, tmp_path):
    argv = core.preview_argv("hey alex", tmp_path / "out")
    assert argv[:2] == ["/usr/bin/python3", "/opt/domovoi/scripts/wake_word/train_wake_word.py"]
    assert argv[2:] == ["--preview", "--phrase", "hey alex", "--out", str(tmp_path / "out"),
                        "--count", str(core.COUNT)]


def test_a_preview_runs_and_lists_its_clips(core, monkeypatch):
    from domovoi.workers import wake_word_trainer as wwt

    seen: dict = {}

    def fake_run(argv, *, env, timeout, cancel, log_path=None, stop_argv=None, on_line=None):
        out = Path(argv[argv.index("--out") + 1])
        seen.update(argv=argv, env=env, stop=stop_argv)
        on_line("[entrypoint] preview: wrote preview_1.wav")
        for i in (1, 2):
            (out / f"preview_{i}.wav").write_bytes(b"RIFF")
        (out / "preview.json").write_text('{"spoken_as": "hˈeɪ ˈælɛks"}', "utf-8")
        return wwt.TrainRun(returncode=0, output="[entrypoint] DONE")

    monkeypatch.setattr(wwt, "run_train_command", fake_run)

    async def go():
        st = await core.start("hey alex")
        assert st["state"] == "running" and st["phrase"] == "hey alex"
        await core._TASK
        return st["job"]

    job = asyncio.run(go())
    st = asyncio.run(core.status())
    assert st["state"] == "done" and st["job"] == job
    assert st["clips"] == ["preview_1.wav", "preview_2.wav"]
    assert st["spoken_as"] == "hˈeɪ ˈælɛks"
    assert seen["stop"][-1] == "--stop-preview"
    assert "WAKE_SYNTHETIC_ONLY" not in seen["env"] and "WAKE_TRAINER_REQUIRE_GPU" not in seen["env"]
    assert core.clip_path(job, "preview_1.wav").is_file()
    assert core.clip_path(job, "../setup.json") is None
    assert core.clip_path("../x", "preview_1.wav") is None


def test_a_failed_preview_says_why(core, monkeypatch):
    from domovoi.workers import wake_word_trainer as wwt

    monkeypatch.setattr(wwt, "run_train_command", lambda argv, **k: wwt.TrainRun(
        returncode=3, output="[wake-trainer] ERROR: the trainer image 'x' is not built."))

    async def go():
        await core.start("hey alex")
        await core._TASK

    asyncio.run(go())
    st = asyncio.run(core.status())
    assert st["state"] == "failed" and "not built" in st["error"]


def test_one_preview_at_a_time_and_only_a_phrase(core, monkeypatch):
    from domovoi.workers import wake_word_trainer as wwt
    import threading

    release = threading.Event()

    def slow(argv, **k):
        release.wait(5)
        return wwt.TrainRun(returncode=1, output="")

    monkeypatch.setattr(wwt, "run_train_command", slow)

    async def go():
        await core.start("hey alex")
        with pytest.raises(core.Refused):
            await core.start("hey sam")
        release.set()
        await core._TASK

    asyncio.run(go())
    with pytest.raises(core.Refused):
        asyncio.run(core.start("hey $(alex)"))


# ── routes ────────────────────────────────────────────────────────────────

def _gates(app, method: str, path: str):
    from domovoi.tests.route_walk import iter_route_contexts

    def calls(dep):
        for d in getattr(dep, "dependencies", ()):
            if d.call is not None:
                yield d.call
            yield from calls(d)

    for rc in iter_route_contexts(app.routes):
        if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or set()):
            return list(calls(getattr(rc, "dependant", None)))
    raise AssertionError(f"{method} {path} is not a route")


def test_routes_wear_their_tiers():
    from domovoi.main import app as core_app
    from web.backend.main import app as web_app

    assert admin_auth.require_admin_read in _gates(core_app, "GET", "/v1/admin/wake/preview")
    assert admin_auth.require_admin_mutation in _gates(core_app, "POST", "/v1/admin/wake/preview")
    assert admin_auth.require_admin_read in _gates(web_app, "GET", "/api/wake-words/preview")
    assert admin_auth.require_admin_mutation in _gates(web_app, "POST", "/api/wake-words/preview")
    assert admin_auth.require_device_read in _gates(
        web_app, "GET", "/api/wake-words/preview/{job}/{name}/audio")
    assert admin_auth.require_admin_mutation in _gates(
        web_app, "POST", "/api/wake-words/{wake_word_id}/synthetic-copy")


def test_core_refusal_is_a_409(monkeypatch):
    from fastapi import HTTPException

    from domovoi import main, wake_preview

    async def refuse(phrase):
        raise wake_preview.Refused("Turn on wake-word training above to hear the training voices")

    monkeypatch.setattr(wake_preview, "start", refuse)
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.admin_wake_preview_start(main._AdminWakePreviewBody(phrase="hey alex")))
    assert e.value.status_code == 409 and "Turn on" in e.value.detail


def test_web_hop_and_the_audio_route(monkeypatch, tmp_path):
    import web.backend.api.wake_words as wake_api
    from domovoi.config import settings as core_settings
    from web.backend.main import app as web_app

    seen: dict = {}

    async def fake_post(path, body=None, timeout=30.0, headers=None):
        seen.update(path=path, body=body)
        return 200, {"state": "running", "job": "0123456789ab"}

    async def ok():
        return None

    monkeypatch.setattr(wake_api, "post_admin", fake_post)
    for gate in (admin_auth.require_admin_mutation, admin_auth.require_device_read):
        monkeypatch.setitem(web_app.dependency_overrides, gate, ok)
    monkeypatch.setattr(core_settings, "wake_models_dir", str(tmp_path / "wake_models"))
    clip = tmp_path / "wake_trainer" / "preview" / "0123456789ab" / "preview_1.wav"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"RIFF....WAVE")
    with TestClient(web_app, headers={"X-Requested-With": "domovoi-tests"}) as client:
        r = client.post("/api/wake-words/preview", json={"phrase": " hey al ex "})
        bad = client.post("/api/wake-words/preview", json={"phrase": "hey `alex`"})
        a = client.get("/api/wake-words/preview/0123456789ab/preview_1.wav/audio")
        missing = client.get("/api/wake-words/preview/0123456789ab/preview_2.wav/audio")
        sneaky = client.get("/api/wake-words/preview/0123456789ab/setup.json/audio")
        sneaky2 = client.get("/api/wake-words/preview/..%2F..%2Fx/preview_1.wav/audio")
    assert r.status_code == 200 and seen == {"path": "/v1/admin/wake/preview",
                                             "body": {"phrase": "hey al ex"}}
    assert bad.status_code == 422
    assert a.status_code == 200 and a.content == b"RIFF....WAVE"
    assert a.headers["content-type"] == "audio/wav"
    assert missing.status_code == 404
    assert sneaky.status_code in (404, 422) and sneaky2.status_code in (404, 422)
