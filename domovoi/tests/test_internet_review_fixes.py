"""Fixes from the 2026-10-03 reviews of the internet build (DB-free).

* The refusal an egress-hooked httpx client raises is an
  ``InternetTurnedOff`` AND an ``httpx.ConnectError``.
* SearXNG start/stop runs one at a time and reads the answer under the
  lock: Yes-then-No during a slow first start ends stopped. Its state is
  reported to Settings → Internet, and "start it again" exists.
* Settings → Internet lists plugins that may reach the internet on their
  own and, under never, warns about a language model outside the house.
* One podcast's dead host no longer starves every other download.
* "I checked online" is not said when every search engine failed, and the
  "do this automatically?" offer is not made on an unsearched reply.
* A failed podcast-directory lookup is not "I couldn't find a podcast".
* Whisper loads a cache pinned by commit (no ``refs/main``).
* An Edge-marked clip that Edge did not render is rendered again once.
* The Models catalog says which Whisper sizes are on disk; a model pull
  that is running when the answer becomes never is cancelled.
* A satellite plugin's apt work is marked offline under never.
* A podcast download stops when the answer becomes never mid-transfer.
"""

from __future__ import annotations

import asyncio
import io
import math
import struct
import subprocess
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from domovoi import egress, internet_profile, searxng_service as svc
from domovoi.models import Context

INTERNET = "https://example.com/feed.xml"


# ─── the SDK refusal is also a network error ─────────────────────────────


@pytest.mark.asyncio
async def test_an_sdk_client_refusal_is_an_internet_off_and_a_connect_error() -> None:
    from domovoi.sdk.http import HttpFactory

    seen: list[str] = []
    transport = httpx.MockTransport(lambda r: seen.append(str(r.url)) or httpx.Response(200))
    async with HttpFactory("1.4.0").client(transport=transport) as client:
        with egress.override_policy("never"):
            with pytest.raises(httpx.ConnectError) as info:
                await client.get(INTERNET)
            ok = await client.get("http://192.168.1.10/feed.xml")   # the house network
    err = info.value
    assert isinstance(err, egress.InternetTurnedOff)
    assert isinstance(err, httpx.TransportError) and isinstance(err, ValueError)
    assert err.request.url == httpx.URL(INTERNET)
    assert egress.TURNED_OFF_REASON in str(err)
    assert egress.InternetTurnedOffConnectError is type(err)
    assert ok.status_code == 200 and seen == ["http://192.168.1.10/feed.xml"]


def test_the_sync_client_raises_the_same_class() -> None:
    with egress.sync_client(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as c:
        with egress.override_policy("never"), pytest.raises(httpx.ConnectError) as info:
            c.get(INTERNET)
    assert isinstance(info.value, egress.InternetTurnedOff)


def test_a_plugin_that_catches_only_httpx_errors_treats_it_as_offline() -> None:
    """The shape a plugin's existing network handling has."""
    def fetch():
        with egress.sync_client(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as c:
            try:
                return c.get(INTERNET).text
            except httpx.HTTPError:
                return "offline"

    with egress.override_policy("never"):
        assert fetch() == "offline"


# ─── SearXNG: one reconcile at a time ────────────────────────────────────


@pytest.fixture
def compose(monkeypatch, tmp_path):
    monkeypatch.delenv(svc.MANAGE_ENV, raising=False)
    (tmp_path / "domovoi").mkdir()
    (tmp_path / "domovoi" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(svc.settings, "repo_dir", str(tmp_path))
    monkeypatch.setitem(svc._LAST, "state", "unknown")
    return tmp_path


@pytest.mark.asyncio
async def test_no_saved_during_a_slow_first_start_ends_stopped(monkeypatch, compose) -> None:
    """The ops review's race: the stop found no container (the start was
    still pulling), then the start finished and left SearXNG running under
    never, with restart: unless-stopped."""
    state = {"running": False}
    started, release = threading.Event(), threading.Event()
    calls: list[str] = []

    def fake_run(argv, timeout):
        verb = argv[1]
        calls.append(verb)
        if verb == "compose":
            started.set()
            release.wait(5)
            state["running"] = True
            return subprocess.CompletedProcess(argv, 0, "", "")
        if verb == "inspect":
            if not state["running"]:
                return subprocess.CompletedProcess(argv, 1, "", "Error: No such object: domovoi-searxng")
            return subprocess.CompletedProcess(argv, 0, "true\n", "")
        if verb == "stop":
            state["running"] = False
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(svc, "_run", fake_run)
    with egress.override_policy("always"):
        first = asyncio.create_task(svc.reconcile())
        await asyncio.to_thread(started.wait, 5)
        assert svc.status()["state"] == "starting"
    with egress.override_policy("never"):
        second = asyncio.create_task(svc.reconcile())
        await asyncio.sleep(0.05)
        release.set()
        await first
        await second
    assert state["running"] is False                       # stopped in the end
    assert calls.index("compose") < calls.index("stop")
    assert svc.status()["state"] == "stopped"


@pytest.mark.asyncio
async def test_the_status_reports_the_last_outcome(monkeypatch, compose) -> None:
    monkeypatch.setattr(svc, "_run", lambda argv, timeout: subprocess.CompletedProcess(
        argv, 1, "", "Error response from daemon: pull access denied"))
    assert svc.status()["state"] == "unknown"
    await svc.reconcile("always")
    st = svc.status()
    assert st["state"] == "failed" and "pull access denied" in st["detail"] and st["managed"] is True
    monkeypatch.setenv(svc.MANAGE_ENV, "0")
    assert svc.status()["state"] == "unmanaged"


@pytest.mark.asyncio
async def test_start_it_again_schedules_the_reconcile(monkeypatch) -> None:
    from domovoi import main

    ran: list[str] = []

    async def fake_reconcile(answer=None):
        ran.append(egress.policy())
        return svc.SearxngAction("start", True, "up")

    monkeypatch.delenv(svc.MANAGE_ENV, raising=False)
    monkeypatch.setattr(svc, "reconcile", fake_reconcile)
    with egress.override_policy("always"):
        out = await main.admin_internet_search_helper()
        for _ in range(3):
            await asyncio.sleep(0)
    assert out["scheduled"] is True and set(out["search_helper"]) == {"state", "detail", "at", "managed"}
    assert ran == ["always"]
    with egress.override_policy(""):
        out = await main.admin_internet_search_helper()
    assert out["scheduled"] is False                       # unanswered: left alone


# ─── Settings → Internet: plugins and warnings ───────────────────────────


def test_network_plugins_are_listed_and_the_bundled_one_marked(monkeypatch, tmp_path) -> None:
    from domovoi.plugins_runtime import loader

    bundled = tmp_path / "repo" / "plugins"
    (bundled / "radio").mkdir(parents=True)
    installed = tmp_path / "installed" / "kiwix"
    installed.mkdir(parents=True)

    def lp(slug, name, network, where):
        return SimpleNamespace(manifest=SimpleNamespace(name=name, permissions={"network": network}),
                               install_dir=where)

    monkeypatch.setattr(loader.LOADER, "loaded", {
        "radio": lp("radio", "Radio", True, bundled / "radio"),
        "kiwix": lp("kiwix", "Kiwix", True, installed),
        "sleep": lp("sleep", "Sleep", False, tmp_path / "installed" / "sleep"),
    })
    monkeypatch.setattr(loader, "bundled_root", lambda: bundled)
    assert internet_profile.network_plugins() == [
        {"slug": "kiwix", "name": "Kiwix", "bundled": False},
        {"slug": "radio", "name": "Radio", "bundled": True},
    ]


def test_never_warns_about_a_language_model_outside_the_house() -> None:
    def s(**kw):
        base = {"internet_access": "never", "ollama_url": "http://localhost:11434",
                "ollama_model": "llama3.2:3b", "ollama_tool_model": "qwen2.5:14b",
                "ollama_vision_model": ""}
        base.update(kw)
        return SimpleNamespace(**base)

    assert internet_profile.never_warnings(s()) == []
    assert internet_profile.never_warnings(s(ollama_url="http://192.168.1.20:11434")) == []
    remote = internet_profile.never_warnings(s(ollama_url="https://ollama.example.com"))
    assert len(remote) == 1 and "not on this network" in remote[0]
    cloud = internet_profile.never_warnings(s(ollama_tool_model="gpt-oss:120b-cloud"))
    assert len(cloud) == 1 and "gpt-oss:120b-cloud" in cloud[0] and "OLLAMA_TOOL_MODEL" in cloud[0]
    assert internet_profile.never_warnings(s(internet_access="always",
                                             ollama_url="https://ollama.example.com")) == []


def test_the_hugging_face_note_reads_for_an_owner() -> None:
    assert "Hugging Face" not in internet_profile.HF_RESTART_NOTE


# ─── podcasts: one dead host doesn't starve the rest ─────────────────────


def _eps(n: int) -> list[dict]:
    return [{"id": i, "subscription_id": 1 if i == 1 else 2, "guid": f"g{i}", "title": f"e{i}",
             "sub_title": "S", "enclosure_url": f"https://feeds.example.com/{i}.mp3"} for i in range(1, n + 1)]


@pytest.mark.asyncio
async def test_a_dead_host_newest_episode_does_not_block_the_others(monkeypatch) -> None:
    from domovoi.workers import podcast_feed_poller as poller

    tried: list[int] = []

    async def download(session, ep):
        tried.append(ep["id"])
        return None if ep["id"] == 1 else True     # the newest one's host is dead

    monkeypatch.setattr(poller, "download_episode", download)
    monkeypatch.setattr(poller.connectivity, "current_probe", lambda: None)
    assert await poller.download_pending(None, _eps(5)) == 4
    assert tried == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_the_pass_stops_when_the_line_itself_is_gone(monkeypatch) -> None:
    from domovoi.workers import podcast_feed_poller as poller

    tried: list[int] = []

    async def down(session, ep):
        tried.append(ep["id"])
        return None

    monkeypatch.setattr(poller, "download_episode", down)
    monkeypatch.setattr(poller.connectivity, "current_probe", lambda: None)
    assert await poller.download_pending(None, _eps(6)) == 0
    assert tried == [1, 2, 3]                      # three in a row: the line is down

    class OfflineProbe:
        online = True

        async def check_now(self):
            self.online = False
            return False

    tried.clear()
    monkeypatch.setattr(poller.connectivity, "current_probe", lambda: OfflineProbe())
    await poller.download_pending(None, _eps(6))
    assert tried == [1]                            # the probe says offline at once

    tried.clear()
    monkeypatch.setattr(poller.connectivity, "current_probe", lambda: None)
    with egress.override_policy("never"):
        await poller.download_pending(None, _eps(6))
    assert tried == [1]


@pytest.mark.asyncio
async def test_a_download_stops_when_the_answer_becomes_never(monkeypatch, tmp_path) -> None:
    """The check runs per chunk: a transfer already running stops, the
    partial file goes, and the episode waits (pending)."""
    from domovoi import net_safety
    from domovoi.workers import podcast_feed_poller as poller

    monkeypatch.setattr(poller.settings, "podcasts_dir", str(tmp_path))

    class _Resp:
        def raise_for_status(self):
            pass

        async def aclose(self):
            pass

    async def open_stream(client, url, **kw):
        return _Resp()

    async def chunks(resp, cap):
        for c in (b"a" * 10, b"b" * 10, b"c" * 10):
            yield c

    # The answer becomes never after the first chunk: the per-chunk check
    # refuses from then on.
    checks = {"n": 0}

    def require_destination(url):
        checks["n"] += 1
        if checks["n"] >= 2:
            raise egress.InternetTurnedOff(url)

    monkeypatch.setattr(poller.egress, "require_destination", require_destination)

    class _Session:
        def __init__(self):
            self.sql: list[str] = []

        async def execute(self, statement, params=None):
            self.sql.append(" ".join(str(statement).split()))

        async def commit(self):
            pass

    monkeypatch.setattr(net_safety, "open_stream", open_stream)
    monkeypatch.setattr(net_safety, "iter_capped", chunks)
    session = _Session()
    out = await poller.download_episode(session, {"id": 9, "subscription_id": 1, "guid": "g",
                                                   "title": "t", "sub_title": "S",
                                                   "enclosure_url": INTERNET})
    assert out is None and checks["n"] == 2
    assert any("download_status='pending'" in q for q in session.sql)
    assert not list(tmp_path.rglob("*.mp3"))


# ─── web answers: honest when the engines failed ─────────────────────────


@pytest.mark.asyncio
async def test_no_results_because_every_engine_failed_is_not_a_search(monkeypatch) -> None:
    from domovoi.clients.searxng import RealSearxNGClient

    real = httpx.AsyncClient
    payload = {"results": [], "unresponsive_engines": [["google", "CAPTCHA"], ["duckduckgo", "timeout"]]}
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real(
        *a, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)), **kw))
    out = await RealSearxNGClient("http://127.0.0.1:6888").search_detailed("weather tomorrow")
    assert out.status == "engines_failed" and out.results == []

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real(
        *a, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": []})), **kw))
    out = await RealSearxNGClient("http://127.0.0.1:6888").search_detailed("weather tomorrow")
    assert out.status == "no_results"


class _Detailed:
    def __init__(self, status: str) -> None:
        from domovoi.clients.searxng import SearchOutcome

        self.outcome = SearchOutcome([], status)
        self.base_url = "http://127.0.0.1:6888"

    async def search(self, query, max_results=5):
        return []

    async def search_detailed(self, query, max_results=5):
        return self.outcome


@pytest.mark.asyncio
async def test_engines_failed_replies_on_both_paths(monkeypatch) -> None:
    from domovoi.handlers import double_check as dc

    monkeypatch.setattr(dc, "get_searxng_client", lambda: _Detailed("engines_failed"))
    h = dc.DoubleCheckHandler()
    ctx = Context(room_id="kitchen", online=True)
    q = await h._answer_question_from_web("weather tomorrow", ctx, None)
    c = await h._verify_claim_directly("the eiffel tower is in paris", ctx, None)
    for r in (q, c):
        assert r.text == "My search engines didn't answer just now. Try again in a moment."
        assert "checked online" not in r.text.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "offered"), [
    ("unreachable", False), ("error", False), ("engines_failed", False), ("internet_off", False),
    ("no_results", True),
])
async def test_the_auto_search_offer_follows_only_a_real_search(monkeypatch, status, offered) -> None:
    from domovoi.handlers import double_check as dc

    class _Prefs:
        def __init__(self, session):
            pass

        async def record_offer_response(self, person_id, category, affirmative):
            return dc.AUTO_SEARCH_OFFER_THRESHOLD, 0

    asked: list[str] = []

    async def offer(self, category, ctx, session, response):
        asked.append(category)

    monkeypatch.setattr(dc, "WebSearchPrefsRepository", _Prefs)
    monkeypatch.setattr(dc.DoubleCheckHandler, "_maybe_offer_prefs_followup", offer)
    monkeypatch.setattr(dc, "get_searxng_client", lambda: _Detailed(status))
    ctx = Context(room_id="kitchen", online=True, person_id=7)
    await dc.DoubleCheckHandler()._handle_self_doubt_offer(
        {"category": "weather", "search_query": "weather tomorrow"}, True, ctx, None,
    )
    assert asked == (["weather"] if offered else [])


# ─── podcast subscribe: a failed lookup isn't "couldn't find" ───────────


@pytest.mark.asyncio
async def test_a_failed_directory_lookup_says_so(monkeypatch) -> None:
    from domovoi.handlers import spoken_audio as sa

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real(
        *a, transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectTimeout("slow"))),
        **kw))
    h = sa.SpokenAudioHandler()
    with pytest.raises(sa.PodcastDirectoryUnreachable):
        await h._itunes_lookup("the daily")
    r = await h._subscribe(Context(room_id="kitchen", online=True), None, "the daily")
    assert r.text == "I couldn't reach the podcast directory just now. Try again in a moment."

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real(
        *a, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": []})), **kw))
    r = await h._subscribe(Context(room_id="kitchen", online=True), None, "nonexistent show")
    assert r.text == "I couldn't find a podcast called nonexistent show to subscribe to."


# ─── Whisper: a cache pinned by commit ───────────────────────────────────


def _hf_cache(root: Path, repo: str, shas: list[str], *, refs_main: bool) -> None:
    base = root / ("models--" + repo.replace("/", "--"))
    for sha in shas:
        d = base / "snapshots" / sha
        d.mkdir(parents=True)
        (d / "model.bin").write_bytes(b"x")
    if refs_main:
        (base / "refs").mkdir(parents=True)
        (base / "refs" / "main").write_text(shas[0], encoding="utf-8")


def test_whisper_cache_knows_what_is_on_disk(monkeypatch, tmp_path) -> None:
    from domovoi import whisper_cache

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _hf_cache(tmp_path, "Systran/faster-whisper-tiny.en", ["abc"], refs_main=False)
    assert whisper_cache.whisper_model_cached("tiny.en") is True
    assert whisper_cache.whisper_model_cached("base.en") is False
    assert whisper_cache.whisper_model_cached("not-a-model") is None
    assert whisper_cache.whisper_model_cached(str(tmp_path)) is True          # a directory by path


def test_whisper_cache_repo_map_matches_faster_whisper() -> None:
    utils = pytest.importorskip("faster_whisper.utils")
    from domovoi import whisper_cache

    assert whisper_cache.WHISPER_REPOS == utils._MODELS


def test_a_single_pinned_snapshot_is_found_only_without_refs_main(monkeypatch, tmp_path) -> None:
    from domovoi.clients import whisper

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _hf_cache(tmp_path, "Systran/faster-whisper-tiny.en", ["d1d751a5"], refs_main=False)
    pinned = whisper.pinned_snapshot_dir("tiny.en")
    assert pinned and pinned.replace("\\", "/").endswith("snapshots/d1d751a5")
    _hf_cache(tmp_path, "Systran/faster-whisper-base.en", ["aaa"], refs_main=True)
    assert whisper.pinned_snapshot_dir("base.en") is None                    # the normal layout
    _hf_cache(tmp_path, "Systran/faster-whisper-small.en", ["s1", "s2"], refs_main=False)
    assert whisper.pinned_snapshot_dir("small.en") is None                   # ambiguous


@pytest.mark.parametrize("answer", ["never", "always", ""])
def test_whisper_loads_a_pinned_cache_with_no_request(monkeypatch, tmp_path, answer) -> None:
    fw = pytest.importorskip("faster_whisper")
    from domovoi.clients import whisper

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _hf_cache(tmp_path, "Systran/faster-whisper-tiny.en", ["d1d751a5"], refs_main=False)

    class LocalEntryNotFoundError(Exception):
        pass

    loads: list[tuple[str, bool]] = []

    class FakeModel:
        def __init__(self, model, local_files_only=False, **kw):
            loads.append((str(model).replace("\\", "/"), local_files_only))
            if model == "tiny.en":
                raise LocalEntryNotFoundError("Cannot find an appropriate cached snapshot folder")

    monkeypatch.setattr(fw, "WhisperModel", FakeModel)
    monkeypatch.setattr(whisper.FasterWhisperClient, "_load_short_window", lambda self: None)
    with egress.override_policy(answer):
        client = whisper.FasterWhisperClient("tiny.en", "cpu", "int8")
    assert loads[0] == ("tiny.en", True)
    assert len(loads) == 2 and loads[1][0].endswith("snapshots/d1d751a5") and loads[1][1] is False
    assert client.short_window is None


# ─── clips: an Edge-marked clip that Edge did not render ─────────────────


def _mp3(rate: int) -> bytes:
    from domovoi import canned_sounds as cs

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(i / 9))) for i in range(rate // 4)))
    out = cs._wav_to_mp3(buf.getvalue())
    if out is None:
        pytest.skip("lameenc is not installed")
    return out


@pytest.mark.asyncio
async def test_an_edge_clip_rendered_by_a_stand_in_is_redone_once(monkeypatch, tmp_path) -> None:
    from domovoi import canned_sounds as cs

    marker = "edge|en-US-GuyNeural"
    text = "I'm having trouble reaching the network."
    (tmp_path / "net.mp3").write_bytes(_mp3(22050))            # Piper's rate, Edge's tag
    (tmp_path / "net.voice").write_text(f"{marker}\n{cs._hash(text)}\n", encoding="utf-8")
    assert cs._mp3_sample_rate(tmp_path / "net.mp3") == 22050
    renders: list[str] = []

    async def synth(t, engine, model_ref):
        renders.append(engine)
        return _mp3(24000), marker

    monkeypatch.setattr(cs, "_synth_clip_mp3", synth)
    voice = {"name": "Guy", "engine": "edge", "model_ref": "en-US-GuyNeural"}
    # Edge can't be reached: kept as it is, nothing tried.
    await cs._regen_entry(tmp_path, "net.mp3", "net.voice", text, marker, voice, edge_ok=False)
    assert renders == []
    # Edge works: rendered again, once.
    await cs._regen_entry(tmp_path, "net.mp3", "net.voice", text, marker, voice, edge_ok=True)
    await cs._regen_entry(tmp_path, "net.mp3", "net.voice", text, marker, voice, edge_ok=True)
    assert renders == ["edge"]
    assert cs._mp3_sample_rate(tmp_path / "net.mp3") == 24000


def test_a_real_edge_clip_and_a_piper_voice_are_left_alone(tmp_path) -> None:
    from domovoi import canned_sounds as cs

    (tmp_path / "a.mp3").write_bytes(_mp3(24000))
    (tmp_path / "a.voice").write_text("edge|en-US-GuyNeural\nh\n", encoding="utf-8")
    assert not cs._misattributed_edge_clip(tmp_path / "a.mp3", tmp_path / "a.voice", "edge|en-US-GuyNeural")
    (tmp_path / "b.mp3").write_bytes(_mp3(22050))
    (tmp_path / "b.voice").write_text("piper|en_US-lessac-medium\nh\n", encoding="utf-8")
    assert not cs._misattributed_edge_clip(tmp_path / "b.mp3", tmp_path / "b.voice", "piper|en_US-lessac-medium")


# ─── Models: which Whisper sizes are on disk; a pull cut off ─────────────


@pytest.mark.asyncio
async def test_the_catalog_says_which_whisper_sizes_are_downloaded(monkeypatch, tmp_path) -> None:
    from web.backend.api import models as models_api

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _hf_cache(tmp_path, "Systran/faster-whisper-tiny.en", ["abc"], refs_main=True)
    cat = await models_api.get_catalog()
    rows = {r["name"]: r["cached"] for r in cat["whisper"]}
    assert rows and all(v in (True, False, None) for v in rows.values())
    if "tiny.en" in rows:
        assert rows["tiny.en"] is True
    assert any(v is False for v in rows.values())


@pytest.mark.asyncio
async def test_a_running_pull_is_cancelled_when_the_answer_becomes_never(monkeypatch) -> None:
    from web.backend.api import models as models_api

    finished: list[tuple] = []
    closed: list[bool] = []
    state = {"off": False}

    async def pull(model):
        try:
            yield {"status": "pulling manifest"}
            state["off"] = True                    # the answer became never
            yield {"status": "downloading", "completed": 1, "total": 10}
            yield {"status": "success"}
        finally:
            closed.append(True)

    async def nothing(*a, **kw):
        return None

    async def finish(job_id, status, pct=None, error=None):
        finished.append((job_id, status, error))

    monkeypatch.setattr(models_api.ollama_client, "pull_model", pull)
    monkeypatch.setattr(models_api, "_set_running", nothing)
    monkeypatch.setattr(models_api, "_update_progress", nothing)
    monkeypatch.setattr(models_api, "_finish", finish)
    monkeypatch.setattr(models_api.egress, "internet_turned_off", lambda: state["off"])
    await models_api._run_pull(5, "tinyllama")
    assert finished == [(5, "cancelled", models_api.PULL_CANCELLED_INTERNET_OFF)]
    assert closed == [True]


# ─── satellites: plugin apt work is marked offline under never ───────────


@pytest.mark.asyncio
async def test_the_satellite_payload_manifest_marks_offline(monkeypatch, tmp_path) -> None:
    from domovoi import satellite_payload as sp

    async def plugins():
        return [{"slug": "x", "version": "1.0.0", "root": tmp_path,
                 "decl": {"apt_packages": ["libfoo2"], "pip_requirements": [], "pip_lockfile": None,
                          "post_install": None, "max_payload_mb": 64}}]

    monkeypatch.setattr(sp, "enabled_satellite_plugins", plugins)
    monkeypatch.setattr(sp, "payload_files", lambda root, decl: {})
    assert "offline" not in (await sp.build_channel_manifest())["meta"]["x"]
    with egress.override_policy("never"):
        assert (await sp.build_channel_manifest())["meta"]["x"]["offline"] is True
