"""Voice goes first for single background model calls (S3-7).

The house's Ollama answers one request at a time, so a bedtime story's
passage (a plugin's request through ``sdk.http``), a memory pass or a news
briefing being written held a spoken answer up until it finished. These
calls now wait while a voice turn is under way, and are stopped and asked
again after it when a turn starts before they have answered. A voice
turn's own calls never wait.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from domovoi import voice_activity, voice_first

FAST = 0.01


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    voice_activity._reset_for_tests()
    monkeypatch.setattr(voice_first, "POLL_SEC", FAST)
    monkeypatch.setattr(voice_first, "CALL_QUIET_SEC", 0.0)
    yield
    voice_activity._reset_for_tests()


async def _hold_turn(started: asyncio.Event, release: asyncio.Event) -> None:
    async with voice_activity.turn("test"):
        started.set()
        await release.wait()


async def test_a_quiet_house_runs_the_call_once():
    calls = []

    async def make():
        calls.append(1)
        return "ok"

    assert await voice_first.ahead_of_voice(make, poll_sec=FAST) == "ok"
    assert calls == [1]


async def test_the_call_waits_for_a_turn_to_finish():
    started, release = asyncio.Event(), asyncio.Event()
    turn = asyncio.create_task(_hold_turn(started, release))
    await started.wait()
    ran = asyncio.Event()

    async def make():
        ran.set()
        return "ok"

    call = asyncio.create_task(voice_first.ahead_of_voice(make, poll_sec=FAST))
    await asyncio.sleep(0.1)
    assert not ran.is_set()  # the voice turn has Ollama to itself
    release.set()
    await turn
    assert await asyncio.wait_for(call, 2) == "ok"


async def test_a_turn_that_starts_mid_call_stops_it_and_it_runs_again_after():
    attempts: list[str] = []
    first_started = asyncio.Event()

    async def make():
        n = len(attempts)
        attempts.append("started")
        if n == 0:
            first_started.set()
            try:
                await asyncio.sleep(10)  # a long story passage
            except asyncio.CancelledError:
                attempts[n] = "cancelled"
                raise
        return f"answer {n}"

    call = asyncio.create_task(voice_first.ahead_of_voice(make, poll_sec=FAST))
    await first_started.wait()
    started, release = asyncio.Event(), asyncio.Event()
    turn = asyncio.create_task(_hold_turn(started, release))
    await started.wait()
    await asyncio.sleep(0.1)
    # The passage was stopped at once, and nothing runs while the turn does.
    assert attempts == ["cancelled"]
    release.set()
    await turn
    assert await asyncio.wait_for(call, 2) == "answer 1"
    assert attempts == ["cancelled", "started"]


async def test_a_voice_turns_own_call_never_waits():
    started, release = asyncio.Event(), asyncio.Event()
    other = asyncio.create_task(_hold_turn(started, release))  # another room's turn
    await started.wait()

    async def make():
        return "ok"

    async with voice_activity.turn("test"):
        assert voice_activity.in_turn()
        assert await asyncio.wait_for(voice_first.ahead_of_voice(make, poll_sec=FAST), 1) == "ok"
    assert not voice_activity.in_turn()
    release.set()
    await other


async def test_the_wait_gives_up_after_its_limit_and_the_call_then_runs_through():
    started, release = asyncio.Event(), asyncio.Event()
    turn = asyncio.create_task(_hold_turn(started, release))
    await started.wait()
    assert await voice_first.wait_for_voice(max_sec=0.05, poll_sec=FAST) is False
    release.set()
    await turn


# ── a plugin's request through sdk.http ────────────────────────────────


def test_only_requests_that_generate_are_held():
    from domovoi.sdk.http import generates

    assert generates("POST", "http://127.0.0.1:11434/api/generate")
    assert generates("POST", "http://localhost:11434/api/chat")
    assert generates("POST", "http://localhost:11434/v1/chat/completions")
    assert not generates("GET", "http://localhost:11434/api/tags")
    assert not generates("POST", "http://localhost:11434/api/pull")
    assert not generates("POST", "http://192.168.1.20:6370/api/generate")


async def test_a_plugins_story_request_yields_to_a_voice_turn():
    from domovoi.sdk.http import HttpFactory

    sent: list[str] = []
    first_in = asyncio.Event()

    async def ollama(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        if request.url.path == "/api/generate" and len(sent) == 1:
            first_in.set()
            await asyncio.sleep(10)  # generating; closed when the turn starts
        return httpx.Response(200, json={"response": f"passage {len(sent)}"})

    async with HttpFactory().client(transport=httpx.MockTransport(ollama)) as c:
        call = asyncio.create_task(c.post("http://127.0.0.1:11434/api/generate", json={"prompt": "once"}))
        await first_in.wait()
        started, release = asyncio.Event(), asyncio.Event()
        turn = asyncio.create_task(_hold_turn(started, release))
        await started.wait()
        # A listing is not held up by the turn.
        tags = await asyncio.wait_for(c.get("http://127.0.0.1:11434/api/tags"), 1)
        assert tags.status_code == 200
        await asyncio.sleep(0.1)
        assert sent == ["/api/generate", "/api/tags"]
        release.set()
        await turn
        r = await asyncio.wait_for(call, 2)
    assert r.json() == {"response": "passage 3"}
    assert sent == ["/api/generate", "/api/tags", "/api/generate"]


# ── review round 1 ─────────────────────────────────────────────────────


async def test_a_task_a_turn_started_yields_to_a_later_turn():
    """A voice "tell me a story" starts the writer in a task: once that turn
    is over, the passage is background work and gives way to the next."""
    attempts: list[str] = []
    first_started = asyncio.Event()

    async def make():
        n = len(attempts)
        attempts.append("started")
        if n == 0:
            first_started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                attempts[n] = "cancelled"
                raise
        return f"passage {n}"

    async with voice_activity.turn("story request"):
        writer = asyncio.create_task(voice_first.ahead_of_voice(make, poll_sec=FAST))
        await first_started.wait()
        await asyncio.sleep(0.05)
        assert attempts == ["started"]  # its own turn: not stopped
    started, release = asyncio.Event(), asyncio.Event()
    later = asyncio.create_task(_hold_turn(started, release))
    await started.wait()
    await asyncio.sleep(0.1)
    assert attempts == ["cancelled"]
    release.set()
    await later
    assert await asyncio.wait_for(writer, 2) == "passage 1"


async def test_a_call_is_stopped_at_most_three_times_then_runs_to_its_end():
    attempts = 0
    running = asyncio.Event()

    async def make():
        nonlocal attempts
        attempts += 1
        running.set()
        await asyncio.sleep(0.3)
        return attempts

    call = asyncio.create_task(voice_first.ahead_of_voice(make, poll_sec=FAST))
    for _ in range(voice_first.CALL_MAX_RESTARTS):
        await running.wait()
        running.clear()
        started, release = asyncio.Event(), asyncio.Event()
        turn = asyncio.create_task(_hold_turn(started, release))
        await started.wait()
        await asyncio.sleep(0.05)
        release.set()
        await turn
    await running.wait()
    # A fourth turn while it runs: no longer stopped.
    started, release = asyncio.Event(), asyncio.Event()
    turn = asyncio.create_task(_hold_turn(started, release))
    await started.wait()
    assert await asyncio.wait_for(call, 2) == voice_first.CALL_MAX_RESTARTS + 1
    release.set()
    await turn


async def test_a_chat_tool_working_for_a_turn_never_waits():
    started, release = asyncio.Event(), asyncio.Event()
    turn = asyncio.create_task(_hold_turn(started, release))
    await started.wait()

    async def make():
        return "ok"

    with voice_activity.exempt():
        assert await asyncio.wait_for(voice_first.ahead_of_voice(make, poll_sec=FAST), 1) == "ok"
    assert not voice_activity.in_turn()
    release.set()
    await turn


async def test_the_callers_cancellation_goes_through():
    async def make():
        await asyncio.sleep(10)

    call = asyncio.create_task(voice_first.ahead_of_voice(make, poll_sec=FAST))
    await asyncio.sleep(0.05)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(call, 1)


async def test_the_web_wait_ends_at_its_deadline(monkeypatch):
    from web.backend.api import chat_doc_jobs as jobs_api

    async def _slow_busy():
        await asyncio.sleep(0.05)  # asking the core takes time too
        return True

    monkeypatch.setattr(jobs_api, "voice_busy", _slow_busy)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await jobs_api.wait_for_voice(max_sec=0.2, poll_sec=0.01)
    assert loop.time() - t0 < 0.5
