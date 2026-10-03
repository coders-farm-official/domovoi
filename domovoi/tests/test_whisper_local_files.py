"""B6: Whisper loads from the local cache first, with no Hugging Face call.

faster-whisper, given a model NAME, asks huggingface.co for the repo's
current revision on every load (and falls back to the cache only when that
request fails). ``FasterWhisperClient`` now loads with
``local_files_only=True`` first on every answer, so a cached model makes no
request and the installed revision holds. Only a model that isn't on disk
is downloaded, once, and only while the internet answer allows it; under
``INTERNET_ACCESS=never`` the load fails with a hint that says why. A model
given as a local directory is never retried, and the configured → CPU
fallback → none ladder is unchanged.

Most tests use a fake ``faster_whisper``; the last two load the real
``tiny.en`` from the dev box's cache with every outbound socket recorded.
"""

from __future__ import annotations

import os
import socket
import sys
import types

import pytest

from domovoi import egress
from domovoi.clients import whisper as whisper_mod
from domovoi.config import settings

try:  # the real class, when huggingface_hub is installed (it is, with faster-whisper)
    from huggingface_hub.errors import LocalEntryNotFoundError
except Exception:  # pragma: no cover — a stand-in with the same name

    class LocalEntryNotFoundError(FileNotFoundError):  # type: ignore[no-redef]
        pass


NOT_CACHED_MSG = (
    "Cannot find an appropriate cached snapshot folder for the specified revision "
    "on the local disk and outgoing traffic has been disabled."
)


class _FakeModel:
    """Stands in for ``faster_whisper.WhisperModel``. ``cached`` names load
    with local_files_only; others raise like huggingface_hub does. A load
    without local_files_only is the one-time download."""

    cached: set[str] = set()
    calls: list[tuple[str, dict]] = []
    downloads: list[str] = []
    fail_with: Exception | None = None

    def __init__(self, model: str, **kwargs) -> None:
        cls = type(self)
        cls.calls.append((model, dict(kwargs)))
        if cls.fail_with is not None:
            raise cls.fail_with
        if os.path.isdir(model):
            return
        if kwargs.get("local_files_only"):
            if model not in cls.cached:
                raise LocalEntryNotFoundError(NOT_CACHED_MSG)
            return
        cls.downloads.append(model)
        cls.cached.add(model)


@pytest.fixture
def fake_whisper(monkeypatch):
    _FakeModel.cached = set()
    _FakeModel.calls = []
    _FakeModel.downloads = []
    _FakeModel.fail_with = None
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=_FakeModel))
    monkeypatch.setattr(whisper_mod, "_physical_cores", lambda: 4)
    monkeypatch.setattr(settings, "whisper_cpu_threads", 0)
    # The fake has no CTranslate2 underneath; skip the short-window build.
    monkeypatch.setattr(whisper_mod.FasterWhisperClient, "_load_short_window", lambda self: None)
    return _FakeModel


ANSWERS_ALLOWING = ("", "always", "sometimes")


# ─── model_not_cached ──────────────────────────────────────────────────────


def test_model_not_cached_recognises_the_family():
    assert whisper_mod.model_not_cached(LocalEntryNotFoundError(NOT_CACHED_MSG))
    assert whisper_mod.model_not_cached(
        OSError("Cannot find the requested files in the local cache and outgoing traffic has been disabled.")
    )
    # Anywhere in the cause chain.
    try:
        try:
            raise LocalEntryNotFoundError("x")
        except LocalEntryNotFoundError as inner:
            raise RuntimeError("load failed") from inner
    except RuntimeError as outer:
        assert whisper_mod.model_not_cached(outer)
    assert not whisper_mod.model_not_cached(RuntimeError("CUDA driver version is insufficient"))
    assert not whisper_mod.model_not_cached(ValueError("Invalid model size 'huge'"))


# ─── Local first ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("answer", ANSWERS_ALLOWING + ("never",))
def test_a_cached_model_loads_local_only_with_no_second_attempt(fake_whisper, answer):
    fake_whisper.cached = {"small.en"}
    with egress.override_policy(answer):
        client = whisper_mod.FasterWhisperClient("small.en", "cpu", "int8")
    assert fake_whisper.calls == [
        ("small.en", {"local_files_only": True, "device": "cpu", "compute_type": "int8", "cpu_threads": 4}),
    ]
    assert fake_whisper.downloads == []
    assert client.cpu_threads == 4


@pytest.mark.parametrize("answer", ANSWERS_ALLOWING)
def test_a_missing_model_is_downloaded_once_when_allowed(fake_whisper, answer):
    with egress.override_policy(answer):
        whisper_mod.FasterWhisperClient("large-v3", "cuda", "float16")
    assert [c[1].get("local_files_only") for c in fake_whisper.calls] == [True, None]
    assert fake_whisper.calls[1] == ("large-v3", {"device": "cuda", "compute_type": "float16"})
    assert fake_whisper.downloads == ["large-v3"]

    # The next load (a restart) finds it in the cache: local only again.
    fake_whisper.calls = []
    with egress.override_policy(answer):
        whisper_mod.FasterWhisperClient("large-v3", "cuda", "float16")
    assert [c[1].get("local_files_only") for c in fake_whisper.calls] == [True]


def test_a_missing_model_under_never_is_not_downloaded_and_says_why(fake_whisper):
    with egress.override_policy("never"):
        with pytest.raises(RuntimeError) as info:
            whisper_mod.FasterWhisperClient("small.en", "cpu", "int8")
    assert len(fake_whisper.calls) == 1  # no retry
    assert fake_whisper.downloads == []
    msg = str(info.value)
    assert "isn't downloaded" in msg
    assert egress.TURNED_OFF_REASON in msg
    assert "docs/INTERNET.md" in msg and "Before you disconnect" in msg
    assert isinstance(info.value.__cause__, LocalEntryNotFoundError)


@pytest.mark.parametrize("answer", ANSWERS_ALLOWING + ("never",))
def test_a_local_model_directory_is_never_retried(fake_whisper, tmp_path, answer):
    fake_whisper.fail_with = LocalEntryNotFoundError(NOT_CACHED_MSG)
    model_dir = str(tmp_path / "my-converted-model")
    os.makedirs(model_dir)
    with egress.override_policy(answer):
        with pytest.raises(RuntimeError):
            whisper_mod.FasterWhisperClient(model_dir, "cpu", "int8")
    assert len(fake_whisper.calls) == 1
    assert fake_whisper.downloads == []


@pytest.mark.parametrize("answer", ANSWERS_ALLOWING)
def test_any_other_failure_raises_as_before_without_a_retry(fake_whisper, answer):
    fake_whisper.fail_with = RuntimeError("CUDA driver version is insufficient")
    with egress.override_policy(answer):
        with pytest.raises(RuntimeError) as info:
            whisper_mod.FasterWhisperClient("large-v3", "cuda", "float16")
    assert len(fake_whisper.calls) == 1
    # The existing CUDA hint, not the offline one.
    assert "whisper_device=cpu" in str(info.value)
    assert "isn't downloaded" not in str(info.value)


def test_the_hint_is_only_about_internet_when_it_is_off():
    err = LocalEntryNotFoundError(NOT_CACHED_MSG)
    with egress.override_policy("always"):
        assert "isn't downloaded" not in whisper_mod._load_hint("small.en", "cpu", "int8", err)
    with egress.override_policy("never"):
        assert "isn't downloaded" in whisper_mod._load_hint("small.en", "cuda", "float16", err)


# ─── The ladder is unchanged ───────────────────────────────────────────────


@pytest.fixture
def ladder(monkeypatch, fake_whisper):
    monkeypatch.setattr(whisper_mod, "_client", None)
    monkeypatch.setattr(whisper_mod, "_status", None)
    monkeypatch.setattr(settings, "use_stubs", False)
    monkeypatch.setattr(settings, "whisper_model", "large-v3")
    monkeypatch.setattr(settings, "whisper_device", "cpu")
    monkeypatch.setattr(settings, "whisper_compute_type", "int8")
    monkeypatch.setattr(settings, "whisper_cpu_fallback_model", "small.en")
    monkeypatch.setattr("domovoi.bootstrap.register_nvidia_dlls", lambda: ())
    return fake_whisper


def test_under_never_a_missing_model_falls_back_to_a_cached_one(ladder):
    ladder.cached = {"small.en"}
    with egress.override_policy("never"):
        assert whisper_mod.load_whisper_client() is not None
    status = whisper_mod.stt_status()
    assert status["state"] == "fallback"
    assert status["loaded"]["model"] == "small.en"
    assert "isn't downloaded" in status["fallback_reason"]
    assert ladder.downloads == []
    assert all(c[1].get("local_files_only") is True for c in ladder.calls)


def test_under_never_with_nothing_cached_the_core_runs_without_stt(ladder):
    with egress.override_policy("never"):
        assert whisper_mod.load_whisper_client() is None
    status = whisper_mod.stt_status()
    assert status["state"] == "unavailable"
    assert egress.TURNED_OFF_REASON in status["error"]
    assert ladder.downloads == []


# ─── The real stack: no socket opened for a cached model ───────────────────


@pytest.fixture
def outbound_recorder(monkeypatch):
    """Record (and refuse) every DNS lookup and TCP connect in-process."""
    attempts: list[str] = []
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect

    def getaddrinfo(host, *a, **k):
        if host not in ("localhost", "127.0.0.1", "::1", None):
            attempts.append(f"dns {host}")
            raise socket.gaierror(socket.EAI_NONAME, "test: outbound refused")
        return real_getaddrinfo(host, *a, **k)

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in ("127.0.0.1", "::1", "localhost"):
            attempts.append(f"connect {address}")
            raise OSError("test: outbound refused")
        return real_connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    # Online-looking huggingface_hub: not in offline mode, so ONLY our
    # local_files_only keeps it off the network.
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    import huggingface_hub.constants as hf_constants

    monkeypatch.setattr(hf_constants, "HF_HUB_OFFLINE", False)
    return attempts


def _real_tiny_en_cached() -> bool:
    try:
        from faster_whisper.utils import download_model

        download_model("tiny.en", local_files_only=True)
        return True
    except Exception:
        return False


def test_the_recorder_sees_the_per_load_revision_check(outbound_recorder):
    """Positive control: without local_files_only, faster-whisper's own
    model resolution reaches for huggingface.co (and then quietly falls back
    to the cache when that fails). The recorder must see that attempt, or
    the next test's 'no attempts' proves nothing."""
    pytest.importorskip("faster_whisper")
    if not _real_tiny_en_cached():
        pytest.skip("tiny.en is not in this machine's Hugging Face cache")
    from faster_whisper.utils import download_model

    outbound_recorder.clear()
    try:
        download_model("tiny.en")
    except Exception:
        pass  # whether it falls back or raises, the attempt is what counts
    assert any("huggingface.co" in a for a in outbound_recorder), outbound_recorder


def test_the_real_client_loads_a_cached_model_with_no_outbound_attempt(outbound_recorder, monkeypatch):
    from domovoi.bootstrap import register_nvidia_dlls

    register_nvidia_dlls()
    pytest.importorskip("faster_whisper")
    pytest.importorskip("ctranslate2")
    if not _real_tiny_en_cached():
        pytest.skip("tiny.en is not in this machine's Hugging Face cache")
    monkeypatch.setattr(settings, "whisper_cpu_threads", 2)
    outbound_recorder.clear()
    with egress.override_policy("always"):
        client = whisper_mod.FasterWhisperClient("tiny.en", "cpu", "int8")
    assert client._model is not None
    assert outbound_recorder == []
