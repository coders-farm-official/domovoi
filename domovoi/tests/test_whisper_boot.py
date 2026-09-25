"""First boot survives a Whisper config that can't load.

The repo defaults are large-v3 on cuda. On a machine with no NVIDIA GPU
that load used to raise straight out of the core's lifespan — BEFORE the
first-run setup code was written — so the core never started, the setup
code never existed, and the dashboard (whose config writes need the admin
that setup creates) could never repair the setting. And the dashboard
offered ``whisper_device`` but not the compute type, so choosing cpu left
float16 behind, which CTranslate2 can't run on a CPU: the next boot failed
the same way.

What is pinned here:

* the load ladder: the configured Whisper, then
  ``whisper_cpu_fallback_model`` on cpu/int8, then no STT at all — never
  an exception out of boot;
* the setup code (and device token) are on disk before Whisper loads, and
  the core serves requests with Whisper dead;
* ``whisper_compute_type = auto`` resolves per device, explicit values
  (the Beelink's ``int8``) pass through untouched;
* the device/compute pair is validated on config write;
* a satellite turn with no STT gets a spoken explanation and a released
  mic, never a crash or a hung turn;
* the STT state reaches the Models page, and the STT catalog rows set the
  compute type they advertise.

Nothing here loads a real model: ``FasterWhisperClient`` is replaced by a
recorder that succeeds or raises per rung.
"""

from __future__ import annotations

import io
import json
import types
import wave
from pathlib import Path

import pytest

from domovoi import admin_auth, streaming
from domovoi.clients import whisper as whisper_mod
from domovoi.clients.whisper import (
    SttUnavailableError,
    compute_pair_problem,
    resolve_compute_type,
)
from domovoi.config import Settings, settings
from domovoi.streaming import StreamSession
from domovoi.tests.auth_testkit import _db  # noqa: F401 — fixture
from domovoi.tests.conftest import requires_db

REPO_ROOT = Path(__file__).resolve().parents[2]


# ─── helpers ─────────────────────────────────────────────────────────────


class _Recorder:
    """Stands in for ``FasterWhisperClient``: records every construction
    as a (model, device, compute) rung and raises for the rungs listed in
    ``fail`` (or all of them)."""

    def __init__(self, fail: set[tuple[str, str, str]] | None = None, fail_all: bool = False,
                 exc: type[BaseException] = RuntimeError) -> None:
        self.attempts: list[tuple[str, str, str]] = []
        self.fail = fail or set()
        self.fail_all = fail_all
        self.exc = exc

    def __call__(self, model: str, device: str, compute_type: str):
        rung = (model, device, compute_type)
        self.attempts.append(rung)
        if self.fail_all or rung in self.fail:
            raise self.exc(f"cannot load {model} on {device} ({compute_type})")
        client = types.SimpleNamespace(rung=rung)

        async def transcribe(pcm: bytes) -> str:
            return "what time is it"

        client.transcribe = transcribe
        return client


@pytest.fixture
def ladder(monkeypatch):
    """Real-client mode for the whisper module only, a clean load state
    (restored afterwards), and a no-op DLL bootstrap."""
    monkeypatch.setattr(whisper_mod, "_client", None)
    monkeypatch.setattr(whisper_mod, "_status", None)
    monkeypatch.setattr(settings, "use_stubs", False)
    monkeypatch.setattr("domovoi.bootstrap.register_nvidia_dlls", lambda: ())

    def configure(model="large-v3", device="cuda", compute="auto", fallback="small.en",
                  recorder: _Recorder | None = None, cuda_devices: int | None = None) -> _Recorder:
        monkeypatch.setattr(settings, "whisper_model", model)
        monkeypatch.setattr(settings, "whisper_device", device)
        monkeypatch.setattr(settings, "whisper_compute_type", compute)
        monkeypatch.setattr(settings, "whisper_cpu_fallback_model", fallback)
        rec = recorder or _Recorder()
        monkeypatch.setattr(whisper_mod, "FasterWhisperClient", rec)
        # None = "CTranslate2 can't be asked" (it isn't installed in the
        # test venv): the configured rung is simply tried.
        monkeypatch.setattr(whisper_mod, "_cuda_device_count", lambda: cuda_devices)
        return rec

    return configure


# ─── defaults (item 4: model/device unchanged, compute type auto) ────────


def test_defaults_keep_large_v3_on_cuda_and_make_compute_auto() -> None:
    fields = Settings.model_fields
    assert fields["whisper_model"].default == "large-v3"
    assert fields["whisper_device"].default == "cuda"
    assert fields["whisper_compute_type"].default == "auto"
    assert fields["whisper_cpu_fallback_model"].default == "small.en"


def test_env_example_documents_the_whisper_settings_at_their_defaults() -> None:
    text = (REPO_ROOT / "domovoi" / ".env.example").read_text(encoding="utf-8")
    live = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            live[k.strip()] = v.strip()
    assert live["WHISPER_MODEL"] == "large-v3"
    assert live["WHISPER_DEVICE"] == "cuda"
    assert live["WHISPER_COMPUTE_TYPE"] == Settings.model_fields["whisper_compute_type"].default
    # The new knob is documented, commented at its default.
    assert "# WHISPER_CPU_FALLBACK_MODEL=small.en" in text


# ─── auto compute type ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("device", "compute", "expected"),
    [
        ("cpu", "auto", "int8"),
        ("cuda", "auto", "float16"),
        ("cuda:1", "auto", "float16"),
        ("CPU", " Auto ", "int8"),
        ("cpu", "", "int8"),
        # faster-whisper's own device=auto: let CTranslate2 choose.
        ("auto", "auto", "auto"),
        # Explicit values are used as written — the Beelink's .env pins int8.
        ("cpu", "int8", "int8"),
        ("cuda", "int8", "int8"),
        ("cuda", "float16", "float16"),
        ("cuda", "int8_float16", "int8_float16"),
    ],
)
def test_resolve_compute_type(device, compute, expected) -> None:
    assert resolve_compute_type(device, compute) == expected


@pytest.mark.parametrize(
    ("device", "compute", "bad"),
    [
        ("cpu", "float16", True),
        ("cpu", "bfloat16", True),
        ("cpu", "int8_float16", True),
        ("cpu", "auto", False),
        ("cpu", "int8", False),
        ("cpu", "float32", False),
        ("cuda", "auto", False),
        ("cuda", "float16", False),
        ("cuda", "int8", False),
        ("cuda", "int16", True),
    ],
)
def test_compute_pair_problem(device, compute, bad) -> None:
    problem = compute_pair_problem(device, compute)
    assert (problem is not None) is bad
    if bad:
        assert device in problem or "GPU" in problem or "CPU" in problem


# ─── the ladder ──────────────────────────────────────────────────────────


def test_the_configured_whisper_loads_first(ladder) -> None:
    rec = ladder(model="large-v3", device="cuda", compute="auto")
    client = whisper_mod.load_whisper_client()
    assert client is not None
    assert rec.attempts == [("large-v3", "cuda", "float16")]
    st = whisper_mod.stt_status()
    assert st["state"] == "ok"
    assert st["loaded"] == {"model": "large-v3", "device": "cuda", "compute_type": "float16"}
    assert st["configured"]["compute_type"] == "auto"
    assert st["configured"]["compute_type_resolved"] == "float16"
    assert st["fallback_reason"] is None and st["error"] is None
    assert whisper_mod.get_whisper_client() is client


def test_the_beelink_config_loads_exactly_as_written(ladder) -> None:
    """CPU host with an explicit .env (small.en / cpu / int8): one attempt,
    the same rung it always loaded — nothing about this change reaches it."""
    rec = ladder(model="small.en", device="cpu", compute="int8")
    assert whisper_mod.load_whisper_client() is not None
    assert rec.attempts == [("small.en", "cpu", "int8")]
    assert whisper_mod.stt_status()["state"] == "ok"


def test_a_cuda_failure_falls_back_to_the_cpu_model_at_int8(ladder, caplog) -> None:
    rec = ladder(
        model="large-v3", device="cuda", compute="auto",
        recorder=_Recorder(fail={("large-v3", "cuda", "float16")}),
    )
    caplog.set_level("INFO", logger="domovoi.clients.whisper")
    client = whisper_mod.load_whisper_client()
    assert client is not None
    assert rec.attempts == [("large-v3", "cuda", "float16"), ("small.en", "cpu", "int8")]
    st = whisper_mod.stt_status()
    assert st["state"] == "fallback"
    assert st["loaded"] == {"model": "small.en", "device": "cpu", "compute_type": "int8"}
    assert "cannot load large-v3 on cuda" in st["fallback_reason"]
    assert st["error"] is None
    # Every step is in the log.
    log_text = caplog.text
    assert "loading the configured model=large-v3" in log_text
    assert "falling back to model=small.en device=cpu compute=int8" in log_text
    assert "running on the cpu fallback" in log_text
    # The streaming layer gets the fallback client.
    assert whisper_mod.get_whisper_client() is client


def test_cuda_with_no_visible_gpu_skips_straight_to_the_fallback(ladder) -> None:
    """faster-whisper downloads before it touches the device: on a machine
    with no NVIDIA GPU the configured cuda rung would pull all of large-v3
    just to fail. When CTranslate2 reports zero CUDA devices it isn't tried."""
    rec = ladder(model="large-v3", device="cuda", compute="auto", cuda_devices=0)
    assert whisper_mod.load_whisper_client() is not None
    assert rec.attempts == [("small.en", "cpu", "int8")]
    st = whisper_mod.stt_status()
    assert st["state"] == "fallback"
    assert "no CUDA device" in st["fallback_reason"]
    assert "whisper_device=cpu" in st["fallback_reason"]


def test_a_visible_gpu_or_an_unknown_count_still_tries_cuda(ladder) -> None:
    for count in (1, None):
        rec = ladder(model="large-v3", device="cuda", cuda_devices=count)
        whisper_mod._client = whisper_mod._status = None
        assert whisper_mod.load_whisper_client() is not None
        assert rec.attempts == [("large-v3", "cuda", "float16")], count


def test_a_cpu_config_never_probes_cuda(ladder, monkeypatch) -> None:
    rec = ladder(model="small.en", device="cpu", compute="int8")

    def _probe():
        raise AssertionError("a cpu config must not probe CUDA")

    monkeypatch.setattr(whisper_mod, "_cuda_device_count", _probe)
    assert whisper_mod.load_whisper_client() is not None
    assert rec.attempts == [("small.en", "cpu", "int8")]


def test_the_cuda_probe_asks_ctranslate2(monkeypatch) -> None:
    import sys

    fake = types.SimpleNamespace(get_cuda_device_count=lambda: 2)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    assert whisper_mod._cuda_device_count() == 2

    def _boom():
        raise RuntimeError("driver probe blew up")

    fake.get_cuda_device_count = _boom
    assert whisper_mod._cuda_device_count() is None


def test_the_cuda_probe_degrades_to_unknown_without_ctranslate2(monkeypatch) -> None:
    import builtins
    import sys

    monkeypatch.delitem(sys.modules, "ctranslate2", raising=False)
    real_import = builtins.__import__

    def _no_ct2(name, *a, **k):
        if name == "ctranslate2":
            raise ImportError("no ctranslate2")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_ct2)
    assert whisper_mod._cuda_device_count() is None


def test_the_fallback_model_is_a_setting(ladder) -> None:
    rec = ladder(model="large-v3", device="cuda", fallback="base.en",
                 recorder=_Recorder(fail={("large-v3", "cuda", "float16")}))
    assert whisper_mod.load_whisper_client() is not None
    assert rec.attempts[-1] == ("base.en", "cpu", "int8")


def test_a_gpu_compute_type_on_cpu_falls_back_too(ladder, caplog) -> None:
    """A hand-edited .env with cpu + float16: the configured rung is still
    tried (CTranslate2 decides), and its refusal lands on cpu/int8."""
    rec = ladder(model="medium", device="cpu", compute="float16",
                 recorder=_Recorder(fail={("medium", "cpu", "float16")}))
    caplog.set_level("WARNING", logger="domovoi.clients.whisper")
    assert whisper_mod.load_whisper_client() is not None
    assert rec.attempts == [("medium", "cpu", "float16"), ("small.en", "cpu", "int8")]
    assert "the configured pair looks wrong" in caplog.text
    assert whisper_mod.stt_status()["state"] == "fallback"


def test_total_failure_boots_without_stt(ladder, caplog) -> None:
    rec = ladder(model="large-v3", device="cuda", recorder=_Recorder(fail_all=True))
    caplog.set_level("INFO", logger="domovoi.clients.whisper")
    # Never raises: boot carries on.
    assert whisper_mod.load_whisper_client() is None
    assert rec.attempts == [("large-v3", "cuda", "float16"), ("small.en", "cpu", "int8")]
    st = whisper_mod.stt_status()
    assert st["state"] == "unavailable"
    assert st["loaded"] is None
    assert "cannot load large-v3 on cuda" in st["error"]
    assert "cannot load small.en on cpu" in st["error"]
    assert "speech recognition is UNAVAILABLE" in caplog.text

    # A turn asking for the client gets a typed error — and the dead load
    # is NOT retried inside somebody's voice turn.
    with pytest.raises(SttUnavailableError):
        whisper_mod.get_whisper_client()
    with pytest.raises(SttUnavailableError):
        whisper_mod.get_whisper_client()
    assert len(rec.attempts) == 2


def test_no_second_attempt_when_the_config_is_already_the_fallback(ladder) -> None:
    rec = ladder(model="small.en", device="cpu", compute="int8",
                 recorder=_Recorder(fail_all=True))
    assert whisper_mod.load_whisper_client() is None
    assert rec.attempts == [("small.en", "cpu", "int8")]
    assert whisper_mod.stt_status()["state"] == "unavailable"


def test_an_empty_fallback_setting_means_no_fallback(ladder) -> None:
    rec = ladder(model="large-v3", device="cuda", fallback="  ",
                 recorder=_Recorder(fail_all=True))
    assert whisper_mod.load_whisper_client() is None
    assert rec.attempts == [("large-v3", "cuda", "float16")]


def test_missing_faster_whisper_is_not_retried(ladder) -> None:
    rec = ladder(recorder=_Recorder(fail_all=True, exc=ImportError))
    assert whisper_mod.load_whisper_client() is None
    assert len(rec.attempts) == 1
    st = whisper_mod.stt_status()
    assert st["state"] == "unavailable"
    assert "real-clients" in st["error"]


def test_stub_mode_loads_the_stub(monkeypatch) -> None:
    monkeypatch.setattr(whisper_mod, "_client", None)
    monkeypatch.setattr(whisper_mod, "_status", None)
    assert settings.use_stubs is True
    client = whisper_mod.load_whisper_client()
    assert isinstance(client, whisper_mod.WhisperStubClient)
    assert whisper_mod.stt_status()["state"] == "stub"


def test_status_before_any_load_is_not_loaded(monkeypatch) -> None:
    monkeypatch.setattr(whisper_mod, "_client", None)
    monkeypatch.setattr(whisper_mod, "_status", None)
    st = whisper_mod.stt_status()
    assert st["state"] == "not_loaded"
    assert st["configured"]["model"] == settings.whisper_model


# ─── config registry + pair validation on write ──────────────────────────


def test_compute_type_and_fallback_model_are_editable_restart_tier() -> None:
    from domovoi.config_schema import FIELD_BY_NAME, coerce_and_validate

    ct = FIELD_BY_NAME["whisper_compute_type"]
    assert ct.tier == "restart" and ct.section == "advanced" and ct.type == "choice"
    assert {"auto", "int8", "float16", "int8_float16", "float32"} <= set(ct.choices)
    assert coerce_and_validate(ct, "auto") == "auto"
    with pytest.raises(ValueError):
        coerce_and_validate(ct, "fp32")
    fb = FIELD_BY_NAME["whisper_cpu_fallback_model"]
    assert fb.tier == "restart" and fb.section == "advanced"
    # The pair guard only edits restart_required, so both halves must stay
    # restart-tier (a hot field would already be on the live singleton).
    assert FIELD_BY_NAME["whisper_device"].tier == "restart"


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """The .env the config write persists to, in tmp; no WHISPER_* exports
    shadowing it."""
    from domovoi import config_env_writer

    path = tmp_path / ".env"
    monkeypatch.setattr(config_env_writer, "_ENV_FILE", path)
    for name in ("WHISPER_DEVICE", "WHISPER_COMPUTE_TYPE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "whisper_device", "cuda")
    monkeypatch.setattr(settings, "whisper_compute_type", "auto")
    return path


def _guard(persist: dict):
    from domovoi.main import _guard_whisper_pair

    restart = [n for n in persist]
    rejected: dict[str, str] = {}
    normalized = _guard_whisper_pair(persist, restart, rejected)
    return persist, restart, rejected, normalized


def test_pair_guard_rejects_cpu_with_float16_in_one_write(env_file) -> None:
    persist, restart, rejected, normalized = _guard(
        {"whisper_device": "cpu", "whisper_compute_type": "float16", "bot_name": "Dom"}
    )
    assert persist == {"bot_name": "Dom"}
    assert restart == ["bot_name"]
    assert set(rejected) == {"whisper_device", "whisper_compute_type"}
    assert "GPU compute type" in rejected["whisper_compute_type"]
    assert normalized == {}


def test_pair_guard_moves_a_stranded_float16_to_auto_when_only_the_device_changes(env_file) -> None:
    env_file.write_text("WHISPER_COMPUTE_TYPE=float16\n", encoding="utf-8")
    persist, restart, rejected, normalized = _guard({"whisper_device": "cpu"})
    assert persist == {"whisper_device": "cpu", "whisper_compute_type": "auto"}
    assert restart == ["whisper_device", "whisper_compute_type"]
    assert rejected == {}
    assert "auto" in normalized["whisper_compute_type"]


def test_pair_guard_reads_the_pending_device_not_the_boot_value(env_file) -> None:
    """The singleton still says cuda (restart-tier: saved, not applied);
    the next boot will read cpu from .env, so float16 must be refused."""
    env_file.write_text("WHISPER_DEVICE=cpu\n", encoding="utf-8")
    assert settings.whisper_device == "cuda"
    persist, _restart, rejected, _normalized = _guard({"whisper_compute_type": "float16"})
    assert persist == {}
    assert "whisper_compute_type" in rejected


def test_pair_guard_leaves_valid_pairs_alone(env_file) -> None:
    for change in (
        {"whisper_device": "cpu"},  # compute stays auto → int8
        {"whisper_device": "cpu", "whisper_compute_type": "int8"},
        {"whisper_compute_type": "float16"},  # device stays cuda
        {"whisper_model": "small.en"},
    ):
        persist, _restart, rejected, normalized = _guard(dict(change))
        assert persist == change and rejected == {} and normalized == {}, change


@requires_db
@pytest.mark.asyncio
async def test_config_write_validates_the_whisper_pair(_db, env_file) -> None:  # noqa: F811
    from domovoi.tests.auth_testkit import bearer, claim_admin, core_client, web_client

    async with web_client() as web:
        admin = await claim_admin(web)
    async with core_client() as core:
        r = await core.post(
            "/v1/admin/config",
            json={"changes": {"whisper_device": "cpu", "whisper_compute_type": "float16"}},
            headers=bearer(admin),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body["rejected"]) == {"whisper_device", "whisper_compute_type"}
        assert body["restart_required"] == []
        assert not env_file.exists() or "WHISPER" not in env_file.read_text(encoding="utf-8")

        env_file.write_text("WHISPER_COMPUTE_TYPE=float16\n", encoding="utf-8")
        r = await core.post(
            "/v1/admin/config",
            json={"changes": {"whisper_device": "cpu"}},
            headers=bearer(admin),
        )
        body = r.json()
        assert body["rejected"] == {}
        assert sorted(body["restart_required"]) == ["whisper_compute_type", "whisper_device"]
        assert "whisper_compute_type" in body["normalized"]
        saved = env_file.read_text(encoding="utf-8")
        assert "WHISPER_DEVICE=cpu" in saved
        assert "WHISPER_COMPUTE_TYPE=auto" in saved
        assert "float16" not in saved


# ─── boot order + serving without STT ────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_setup_code_is_written_before_whisper_and_the_core_boots_without_it(
    tmp_path, monkeypatch, ladder
) -> None:
    from domovoi import main as main_mod
    from domovoi.tests.auth_testkit import core_client
    from domovoi.db.session import engine
    from sqlalchemy import text

    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE admin_auth, admin_sessions CASCADE"))

    rec = ladder(model="large-v3", device="cuda", recorder=_Recorder(fail_all=True))
    # The rest of the lifespan stays in stub mode; only the Whisper load
    # runs the real ladder, against a recorder that fails every rung.
    monkeypatch.setattr(settings, "use_stubs", True)
    seen: dict[str, bool] = {}

    def _load_at_boot():
        seen["setup_code_on_disk"] = admin_auth.setup_code_path().is_file()
        seen["device_token_on_disk"] = admin_auth.device_token_path().is_file()
        return whisper_mod._load_real_client()

    monkeypatch.setattr(main_mod, "load_whisper_client", _load_at_boot)

    async with main_mod.app.router.lifespan_context(main_mod.app):
        assert seen == {"setup_code_on_disk": True, "device_token_on_disk": True}
        assert len(rec.attempts) == 2
        async with core_client() as core:
            health = await core.get("/v1/health")
            assert health.status_code == 200, health.text
            assert health.json()["stt"] == "unavailable"
            hw = await core.get("/v1/admin/hardware")
            assert hw.status_code == 200, hw.text
            stt = hw.json()["stt"]
            assert stt["state"] == "unavailable"
            assert stt["configured"]["model"] == "large-v3"
            assert "cannot load" in stt["error"]
    assert admin_auth.read_setup_code()


# ─── a turn with no STT ──────────────────────────────────────────────────


class _FakeWS:
    def __init__(self) -> None:
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                satellite_voice={}, wifi_status={}, satellite_volume={},
                greeting_phrases=[],
            )
        )
        self.sent_text: list[dict] = []
        self.sent_bytes: list[bytes] = []

    async def send_text(self, data: str) -> None:
        self.sent_text.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)


@pytest.fixture
def no_stt(monkeypatch):
    """The whisper module as boot leaves it when every rung failed, with the
    real get_whisper_client in the streaming layer; the router must never
    be reached and the voice lookup stays off the DB."""
    monkeypatch.setattr(whisper_mod, "_client", None)
    monkeypatch.setattr(
        whisper_mod, "_status",
        whisper_mod._status_doc("unavailable", error="Whisper failed to load"),
    )

    async def _route(*_a, **_k):
        raise AssertionError("route() must not be called without a transcript")

    async def _voice(_name):
        return (None, None)

    class _TTS:
        async def synthesize(self, text, *, engine=None, voice=None) -> bytes:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16_000)
                wf.writeframes(b"\x12\x34" * 800)
            return buf.getvalue()

    monkeypatch.setattr(streaming, "route", _route)
    monkeypatch.setattr(streaming, "resolve_voice", _voice)
    monkeypatch.setattr(streaming, "get_tts_client", lambda: _TTS())


@pytest.mark.asyncio
async def test_a_turn_without_stt_is_told_why_and_the_mic_is_released(no_stt, caplog) -> None:
    ws = _FakeWS()
    sess = StreamSession(ws, "office")  # type: ignore[arg-type]
    caplog.set_level("WARNING", logger="domovoi.streaming")
    await sess._process_utterance(b"\x00" * 32_000, trigger="wake_word")

    kinds = [f.get("type") for f in ws.sent_text]
    assert "transcript" not in kinds
    start = next(f for f in ws.sent_text if f.get("type") == "response_start")
    assert start["matched_handler"] == "stt_unavailable"
    assert start["matched_path"] == "system"
    assert "can't understand speech" in start["text"]
    assert ws.sent_bytes, "the explanation is spoken, not just logged"
    ends = [f for f in ws.sent_text if f.get("type") == "response_end"]
    assert len(ends) == 1
    assert ends[0]["expect_followup"] is False
    assert kinds[-1] == "response_end"
    assert "speech recognition is unavailable" in caplog.text


@pytest.mark.asyncio
async def test_a_barge_in_turn_without_stt_ends_quietly(no_stt, monkeypatch) -> None:
    """A barge-in capture opens over the speaker — most likely over the
    notice itself — and with no transcript there is no self-echo check, so
    speaking again could loop. It ends with no speech and a released mic."""
    class _NoTTS:
        async def synthesize(self, *_a, **_k):
            raise AssertionError("a barge-in turn must not speak the notice again")

    monkeypatch.setattr(streaming, "get_tts_client", lambda: _NoTTS())
    ws = _FakeWS()
    sess = StreamSession(ws, "office")  # type: ignore[arg-type]
    await sess._process_utterance(b"\x00" * 3_200, trigger="barge_in")

    assert ws.sent_text == [
        {"type": "response_end", "interrupted": True, "expect_followup": False}
    ]
    assert ws.sent_bytes == []


@pytest.mark.asyncio
async def test_a_turn_without_stt_or_tts_still_ends(no_stt, monkeypatch) -> None:
    class _DeadTTS:
        async def synthesize(self, *_a, **_k):
            raise RuntimeError("tts down too")

    monkeypatch.setattr(streaming, "get_tts_client", lambda: _DeadTTS())
    ws = _FakeWS()
    sess = StreamSession(ws, "office")  # type: ignore[arg-type]
    await sess._process_utterance(b"\x00" * 3_200, trigger="wake_word")

    kinds = [f.get("type") for f in ws.sent_text]
    assert kinds == ["error", "response_end"]
    assert ws.sent_text[-1]["interrupted"] is True
    assert ws.sent_text[-1]["expect_followup"] is False
    assert ws.sent_bytes == []


@pytest.mark.asyncio
async def test_the_cpu_fallback_client_serves_turns(ladder, monkeypatch) -> None:
    """After a fallback, the turn transcribes on the fallback client and
    reaches the router as usual."""
    ladder(recorder=_Recorder(fail={("large-v3", "cuda", "float16")}))
    whisper_mod.load_whisper_client()
    # The rest of the turn runs in stub mode, as the suite does.
    monkeypatch.setattr(settings, "use_stubs", True)
    seen: list[str] = []

    async def _route(intent, ctx, session):
        seen.append(intent.transcript)
        raise RuntimeError("stop here - the rest of the path needs a DB")

    class _Probe:
        online = True

    monkeypatch.setattr(streaming, "route", _route)
    ws = _FakeWS()
    ws.app.state.probe = _Probe()
    sess = StreamSession(ws, "office")  # type: ignore[arg-type]
    await sess._process_utterance(b"\x00" * 3_200, trigger="wake_word")
    assert seen == ["what time is it"]


# ─── the Models page surface ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stt_rows_send_their_compute_type(monkeypatch) -> None:
    import web.backend.api.models as models_api

    sent: list[dict] = []

    async def _post_admin(path, body, headers=None):
        sent.append({"path": path, **body})
        return 200, {"applied": [], "restart_required": list(body["changes"]),
                     "rejected": {}, "normalized": {}}

    monkeypatch.setattr(models_api, "post_admin", _post_admin)
    monkeypatch.setattr(models_api, "auth_forward_headers", lambda _r: {})

    await models_api.set_active(
        models_api.SetActiveBody(role="stt", model="large-v3", compute_type="int8"), None
    )
    assert sent[-1]["changes"] == {"whisper_model": "large-v3", "whisper_compute_type": "int8"}

    # No compute type: the model alone, as before.
    await models_api.set_active(models_api.SetActiveBody(role="stt", model="small.en"), None)
    assert sent[-1]["changes"] == {"whisper_model": "small.en"}

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await models_api.set_active(
            models_api.SetActiveBody(role="qa", model="llama3.2:3b", compute_type="int8"), None
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_the_stt_role_carries_the_device_compute_pair(monkeypatch) -> None:
    import web.backend.api.models as models_api

    fields = [
        {"name": "whisper_model", "value": "large-v3", "tier": "restart"},
        {"name": "whisper_device", "value": "cpu", "tier": "restart"},
        {"name": "whisper_compute_type", "value": "auto", "tier": "restart"},
        {"name": "ollama_model", "value": "llama3.2:3b", "tier": "reapply"},
    ]

    async def _get_admin(path, headers=None):
        return 200, {"fields": fields}

    monkeypatch.setattr(models_api, "get_admin", _get_admin)
    monkeypatch.setattr(models_api, "auth_forward_headers", lambda _r: {})
    roles = {r["role"]: r for r in (await models_api.get_active(None))["roles"]}
    assert roles["stt"]["model"] == "large-v3"
    assert roles["stt"]["device"] == "cpu"
    assert roles["stt"]["compute_type"] == "auto"
    assert "compute_type" not in roles["qa"]


def test_catalog_whisper_rows_name_a_compute_type_they_can_set() -> None:
    """Every STT row's ``compute`` is written as whisper_compute_type when
    it is picked, so it must be a value the config write accepts — and the
    plain rows are ``auto`` so a CPU host can pick them without tripping
    the pair check. The large-v3 int8 row really is int8 now."""
    from domovoi.config_schema import WHISPER_COMPUTE_TYPE_CHOICES

    data = json.loads(
        (REPO_ROOT / "web" / "backend" / "model_catalog.json").read_text(encoding="utf-8")
    )
    rows = data["whisper"]
    for m in rows:
        assert m["compute"] in WHISPER_COMPUTE_TYPE_CHOICES, m
        # Picking a row never strands a GPU type on a CPU host (or the reverse).
        assert compute_pair_problem("cpu", m["compute"]) is None, m
        assert compute_pair_problem("cuda", m["compute"]) is None, m
    assert ("large-v3", "int8") in {(m["name"], m["compute"]) for m in rows}
    assert "float16" not in {m["compute"] for m in rows}
    # The CPU fallback model is pickable from the page.
    assert "small.en" in {m["name"] for m in rows}


def test_models_page_banner_reads_the_stt_block() -> None:
    src = (REPO_ROOT / "web" / "static" / "models.jsx").read_text(encoding="utf-8")
    assert "SttStatusBanner" in src
    assert "hwData.stt" in src
    assert "compute_type" in src
