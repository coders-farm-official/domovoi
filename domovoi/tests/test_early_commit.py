"""Which transcripts may end a satellite's capture early — and the corpus
check that keeps the tiers honest.

Early endpointing, part B (design notes 2026-09-28). A capture the core
ends early loses whatever the person says after the hold, so the tiers on
`FastPath.early_commit` are checked here against a corpus of real commands:
the research corpus of household commands behind the design, the voice
test plan's utterances (in-repo copy below, plus the plan itself when it
is next to the repo), and `scripts/routing_corpus.json`.

The rule, per the design: **no utterance may early-commit on a shorter
prefix that is itself a different command unless the hold covers it** —
a tier-A (short hold) prefix must never route to a different command than
the whole utterance; tier B (the longer hold) is where such prefixes are
allowed to live ("set a timer for ten minutes" before "… for the pasta",
"stop" before "stop the timer"). A continuation that turns the whole
utterance into a language-model question ("what time is it … in Tokyo")
is the owner-accepted cost of early commit, except for the ones listed in
KNOWN_LLM_CONTINUATIONS, which must never sit on tier A.

Also here: the predicate's own rules (punctuation, trailing off, dangling
words, one-word commands, confirmations), and that the router dry run it
stands on is pure — it dispatches nothing.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import os
import re
from pathlib import Path

import pytest

from domovoi.early_commit import TIER_A, TIER_B, early_commit_for
from domovoi.handlers import HANDLERS, register_handler, unregister_handler
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay, as_fast_path
from domovoi.plugins_runtime.contracts import dry_run_winner
from domovoi.router import plan_route

REPO_ROOT = Path(__file__).resolve().parents[2]

# Household commands the design's prefix-hazard analysis ran over (research
# corpus), the continuations it observed, the voice test plan's utterances
# (functional-testing/voice-plan.json, cleaned of harness syntax), and
# continuations the fast-path regexes themselves invite. Whisper's style:
# digits, lower case (the router lowercases anyway).
CORPUS = """
set a timer for 10 minutes
set a timer for 10 minutes and 30 seconds
set a timer for 10 minutes for the pasta
set a timer for 1 hour and a half
set a timer for 25 minutes
timer for 5 minutes called eggs
remind me to take out the trash in 10 minutes
remind me to call mom in 1 hour and 30 minutes
stop
stop the timer
stop the music
stop the call
stop saving that i like jazz
pause
pause the music
resume
resume the music
continue
continue my book
next
next song
next chapter
skip
skip this song
skip forward 30 seconds
back
go back
go back a chapter
previous
previous chapter
volume up
volume down
turn the volume down
turn it down
set the volume to 20
set the volume to 25
volume 5
volume 50
louder
quieter
what time is it
what time is it in tokyo
what's the date
what's today
what's today's weather
what day is it
what day is it today
what's the weather
play some jazz
play some jazz in the kitchen
play jazz by miles davis
play kind of blue by miles davis
play my favorites
play my workout playlist
play the latest episode of radiolab
play the audiobook dune
shuffle
shuffle my library
shuffle my favorites
cancel
cancel that
cancel the timer
cancel my reminder to call mom
no
no thanks
nope
never mind
nothing
nothing else
what's 5 plus 3
what's 5 plus 30
what's 5 plus 3 times 2
10 percent of 50
convert 5 miles to kilometers
repeat
repeat that
say that again
are you sure
is that right
really
i'm sarah
i'm sarah connor
i'm hungry
i'm going to bed
my name is bob
who am i
forget me
forget my voice
what's playing
what song is this
remember that my locker code is 1234
jot down buy milk
announce dinner is ready
tell everyone dinner is ready
drop in on the kitchen
hang up
fix the wifi
how's the wifi
what's the news
news about the election
let's chat
who wrote the odyssey
tell me a joke
what voices do you have
switch to amy
subscribe to radiolab
find the beatles in my library
do i have abbey road
how many songs do i have
my favorite color is blue
my favorite color is blue green
what's my favorite color
list my reminders
how much time left on the timer
next song please
volume up a little
turn it down a bit
never mind the timer
cancel the timer for the eggs
what day is it tomorrow
what's the date next friday
stop the timer for the pasta
play next
forget that i like jazz
resume my book
shuffle my road playlist
yes what time is it
what's today's date
what year is it
what month is it
what's tomorrow
what was yesterday
what's 47 times 89
5 plus 3
what is the square root of 144
12 divided by 0
calculate 2 to the power of 4000
what's 15 percent of 60
15% of $60
20% tip on $47
split $200 4 ways
split $200 4 ways with 20% tip
what percent of 89 is 47
20% off $89
how many ounces are in 100 grams
convert 5 ft into cm
100 grams in oz
5 days from today
3 hours ago
days until christmas
next monday
set a timer for 5 minutes called pasta
cancel the pasta timer
how long on timer
set a timer for 20 seconds
remind me to call mom in 1 minutes
what are my reminders
remind me to feed the cat in 30 minutes
cancel my reminder to feed the cat
what did you say
what was my last note
note that the gate code is 4 4 2 1
what did i jot down today
read my notes from yesterday
system status
what's my server doing
homelab status
how's your wifi
what voices are there
what voice are you using
switch to joe
false alarm
go back to sleep
my name is sarah
yes
what do you remember about me
remember that i like jazz
remember that my dog is called biscuit
forget the fact about my dog
my favorite team is the mariners
what's my favorite team
what are my favorites
forget my favorite team
never save my voice
play ember waltz
play kitchen lights by the hearth cats
who sings this
play something
surprise me
play bohemian rhapsody
set the volume to 5
set the volume to 11
how many songs
find ember waltz in my library
do i have purple rain
is sine sonata in my library
what did i add today
rescan my library
make a playlist called road trip
add this to my road trip playlist
play my road trip playlist
shuffle my road trip playlist
add sine sonata after this
play the audiobook the hearth spirit
what am i listening to
skip 30 seconds
how long is left in this chapter
set the playback speed to 1.5
subscribe to the daily
play the latest episode of the daily
announce pizza is here
tell everyone the package is here
drop in on the garage
let's have a chat
stop chatting
who wrote pride and prejudice
could you please start a five minute countdown for me
what's the weather like today
what's the price of bitcoin
what's the capital of france
give me 5 stories
fetch news about mars
headlines
my news
hey um please what time is it
okay so, set a timer for 2 minutes
yeah what year is it
stop the radio
stop streaming
set a timer for five minutes
what's forty seven times eighty nine
remind me to call mom in two minutes
can you verify that for me
favorite that
read me the latest
read the oldest
fetch new
what's new in technology
news in sports
enrich my library
fingerprint my music
identify my tracks
call me guy
this is jenny
put hearth fm on please
switch the radio off
i want to listen to cellar jazz
tell me about the history of rome
what do you think about cats
what's playing in the kitchen
pause the music in the kitchen
stop the music in the kitchen
next track
previous song
turn it up
turn up the volume
turn up the music a bit
what's the time
what's the time in london
what date is it today
what day of the week is it
what's the year
what month is it now
what's tomorrow's date
what day is tomorrow
what day was yesterday
repeat it
say it again
come again
hang up the call
end the call
list my reminders for today
how many songs do i have in my library
what am i listening to right now
next chapter please
skip ahead 30 seconds
skip back 10 seconds
play my favorites in the kitchen
shuffle my favorites please
never mind thanks
no thanks i'm good
i'm good
i'm good thanks
that's all
that's all for now
cancel it
cancel the reminder
forget it
set a timer for 10 minutes and 30 seconds for the eggs
set a timer for 1 hour
set a timer for 1 hour and 15 minutes
set the volume to 4
set the volume to 40
volume 4
volume 40
what's 15 percent of 60 plus 5
who am i talking to
do you know who i am
what voices are available
what's your voice
""".strip().splitlines()

# Continuations that turn a closed command into a question for the
# language model with a different answer. The prefix may commit early, but
# never on the short hold.
KNOWN_LLM_CONTINUATIONS = [
    ("what time is it", "what time is it in tokyo"),
    ("what's the time", "what's the time in london"),
    ("what's the date", "what's the date next friday"),
    ("what day is it", "what day is it tomorrow"),
    ("what month is it", "what month is it now"),
]


def _voice_plan_utterances() -> list[str]:
    """The voice test plan's spoken steps, when the plan is next to the
    repo (it lives outside it, in functional-testing/) or named by
    DOMOVOI_VOICE_PLAN. Harness syntax is dropped; what is left that
    isn't an utterance routes nowhere and costs nothing."""
    candidates = [os.environ.get("DOMOVOI_VOICE_PLAN"), REPO_ROOT.parent / "functional-testing" / "voice-plan.json"]
    for path in candidates:
        if path and Path(path).is_file():
            plan = json.loads(Path(path).read_text(encoding="utf-8"))
            break
    else:
        return []
    out: list[str] = []
    for test in plan.get("tests", []):
        for step in test.get("steps") or []:
            if not isinstance(step, str):
                continue
            quoted = re.findall(r'(?:tts|say)\b[^"]*?"([^"]+)"', step)
            if quoted:
                out.extend(quoted)
                continue
            step = re.sub(r"^(?:say|speak)?\s*(?:--\S+ \S+\s*)*:\s*", "", step)
            step = re.sub(r"\s*\([^)]*\)\s*$", "", step)
            if re.fullmatch(r"[a-z0-9$%' ,.?-]+", step):
                out.append(step)
    return out


def _routing_corpus() -> list[str]:
    doc = json.loads((REPO_ROOT / "scripts" / "routing_corpus.json").read_text(encoding="utf-8"))
    return [c["utterance"] for c in doc["cases"]]


def _all_utterances() -> list[str]:
    seen: dict[str, None] = {}
    for u in CORPUS + _routing_corpus() + _voice_plan_utterances():
        u = u.strip()
        if u:
            seen.setdefault(u, None)
    return list(seen)


def _route_key(plan) -> tuple:
    """What a route does, for comparing a prefix with its whole utterance:
    the language model, a confirmation, or a fast path with its slots."""
    if plan is None:
        return ("llm",)
    if plan.path == "confirmation":
        return ("confirmation", plan.handler.name, plan.kind)
    return ("fast", plan.handler.name, plan.fast_path.method.__name__, plan.match.groups())


# ─── the corpus check ─────────────────────────────────────────────────────


def test_no_short_hold_prefix_is_a_different_command() -> None:
    """Every word-prefix of every corpus utterance: if it would commit on
    tier A, the whole utterance must route to the same command (or be a
    language-model question — the accepted cost, checked separately)."""
    hazards: list[str] = []
    checked = 0
    for utterance in _all_utterances():
        whole = _route_key(plan_route(utterance))
        words = utterance.split()
        for n in range(1, len(words)):
            prefix = " ".join(words[:n])
            ec = early_commit_for(prefix)
            if ec is None:
                continue
            checked += 1
            if ec.tier != TIER_A or whole == ("llm",):
                continue
            if _route_key(ec.plan) != whole:
                hazards.append(f"{prefix!r} (tier A) is a prefix of {utterance!r} -> {whole[:3]}")
    assert checked > 50, "the corpus no longer exercises early commit"
    assert not hazards, "tier-A prefix hazards:\n  " + "\n  ".join(hazards)


def test_the_tier_b_prefix_hazards_are_the_ones_the_design_names() -> None:
    """The hold covers these — and they are exactly the cases the longer
    hold is for. A new one appearing means a new tier-B hazard to look at."""
    found = set()
    for utterance in CORPUS:
        whole = _route_key(plan_route(utterance))
        words = utterance.split()
        for n in range(1, len(words)):
            prefix = " ".join(words[:n])
            ec = early_commit_for(prefix)
            if ec is not None and whole != ("llm",) and _route_key(ec.plan) != whole:
                assert ec.tier == TIER_B
                found.add(prefix)
    assert found >= {
        "stop", "cancel", "next", "skip", "resume", "continue", "shuffle", "previous",
        "set a timer for 10 minutes", "go back", "forget that", "stop the timer",
    }


@pytest.mark.parametrize(("prefix", "whole"), KNOWN_LLM_CONTINUATIONS)
def test_a_command_a_question_can_continue_is_never_on_the_short_hold(prefix, whole) -> None:
    ec = early_commit_for(prefix)
    assert ec is not None and ec.tier == TIER_B, prefix
    assert plan_route(whole) is None


def test_the_voice_plan_is_read_when_it_is_there(tmp_path, monkeypatch) -> None:
    plan = {"tests": [{"steps": [
        'tts "what\'s my favorite team" → speak (Guy)',
        "--room vt-sat: set the volume to 5",
        "say: my news",
        "what time is it (same session)",
        'curl -X POST /v1/admin/announce -d \'{"room_id":"kitchen"}\'',
        {"not": "a string"},
    ]}]}
    path = tmp_path / "voice-plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setenv("DOMOVOI_VOICE_PLAN", str(path))
    assert _voice_plan_utterances() == [
        "what's my favorite team", "set the volume to 5", "my news", "what time is it",
    ]


# ─── the tiers themselves ─────────────────────────────────────────────────


_OPEN_SLOT_RE = re.compile(r"\.\+|\.\*|\\w\+|\\S\+")


def test_tiers_are_a_b_or_never_and_a_is_closed() -> None:
    tier_a = 0
    for handler in HANDLERS:
        for entry in handler.fast_paths:
            fp = as_fast_path(entry)
            assert fp.early_commit in (None, TIER_A, TIER_B), (handler.name, fp.early_commit)
            if fp.early_commit == TIER_A:
                tier_a += 1
                assert not _OPEN_SLOT_RE.search(fp.pattern.pattern), (
                    f"{handler.name}.{fp.method.__name__} is tier A but has an open slot"
                )
    assert tier_a >= 10


def test_open_slot_commands_never_commit_early() -> None:
    for text in (
        "play some jazz", "play jazz by miles davis", "remember that i like jazz",
        "jot down buy milk", "announce dinner is ready", "drop in on the kitchen",
        "news about the election", "find the beatles in my library", "switch to amy",
        "i'm sarah", "who wrote the odyssey", "tell me a joke", "let's chat",
    ):
        assert early_commit_for(text) is None, text


def test_the_owners_examples_land_on_their_tiers() -> None:
    for text in ("pause the music", "stop the music", "volume up", "turn it down",
                 "next song", "how much time left on the timer", "what's playing"):
        ec = early_commit_for(text)
        assert ec is not None and ec.tier == TIER_A, text
    for text in ("set a timer for 10 minutes", "remind me to call mom in 10 minutes",
                 "set the volume to 40", "volume 5", "what time is it", "what's the date",
                 "pause", "stop", "next", "louder"):
        ec = early_commit_for(text)
        assert ec is not None and ec.tier == TIER_B, text


def test_one_word_is_always_the_long_hold() -> None:
    # "pause" is a tier-A path, but a lone word is what Whisper hears worst
    # and what longer commands start with.
    ec = early_commit_for("Pause.")
    assert ec is not None and ec.tier == TIER_B
    # Filler doesn't make it longer: the router strips it.
    ec = early_commit_for("please pause")
    assert ec is not None and ec.tier == TIER_B


# ─── the predicate's own rules ────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "Set a timer for 10 minutes. And 30 seconds.",   # a pause between clauses
    "What time is it? In Tokyo.",
    "Pause… the music",
    "Set a timer for 2-",                            # cut off at the pause
    "Set a timer for 10 minutes...",
    "What's 5 plus",                                  # ends on an operator
    "set a timer for 10 minutes for the",            # ends on "the"
    "turn it down a bit",                             # a language-model question
    "",
    "   ",
])
def test_what_never_commits(text) -> None:
    assert early_commit_for(text) is None


def test_terminal_punctuation_and_decimals_are_fine() -> None:
    assert early_commit_for("Pause the music.") is not None
    assert early_commit_for("What's playing?") is not None
    ec = early_commit_for("What's 1.5 times 4?")
    assert ec is not None and ec.tier == TIER_B


def _pending(handler: str = "voice_profile") -> dict:
    h = next(h for h in HANDLERS if h.name == handler)
    kind = sorted(h.confirmation_kinds)[0]
    return {"handler": handler, "kind": kind, "data": {}}


def test_a_whole_yes_or_no_to_a_parked_question_commits_on_the_long_hold() -> None:
    pending = _pending()
    for text in ("yes", "Yeah.", "no", "nope", "no thanks", "yes please", "that's right"):
        ec = early_commit_for(text, pending=pending)
        assert ec is not None, text
        assert ec.tier == TIER_B and ec.plan.path == "confirmation", text
    # An answer with more after it is not whole.
    assert early_commit_for("yes and add the other one", pending=pending) is None
    # Nothing parked: "yes" alone is a question for the language model.
    assert early_commit_for("yes") is None


def test_a_parked_music_choice_commits_early_only_on_a_whole_yes_or_no() -> None:
    """"Did you mean …?" (core.music_choice) takes free-text replies, but
    only a whole yes/no ends the capture early (tier B, as any parked
    question). A correction or a name has an open tail — "no, play the
    velvet kites", "velvet kites", "the second one" — and runs to the
    satellite's own silence timeout."""
    from domovoi.router import goes_to_the_tool_model

    pending = _pending("music")
    assert pending["kind"] == "core.music_choice"
    for text in ("yes", "Yeah.", "yeah", "no", "No thanks.", "yes please"):
        ec = early_commit_for(text, pending=pending)
        assert ec is not None, text
        assert ec.tier == TIER_B and ec.plan.path == "confirmation", text
        assert ec.plan.handler.name == "music", text
    for text in (
        "no, play the velvet kites", "No, play the Velvet Kites.", "no play velvet kites",
        "velvet kites", "the second one", "yes, the second one", "no, the velvet kites",
        "no, i said velvet kites",
    ):
        assert early_commit_for(text, pending=pending) is None, text
    # A bare name is heard again on the 30 s path before it is routed (it
    # would go to the tool model if nothing were parked); a yes/no isn't.
    assert goes_to_the_tool_model("velvet kites") is True
    assert goes_to_the_tool_model("no, play the velvet kites") is False


def test_a_legacy_unnamespaced_kind_still_plans_as_its_confirmation() -> None:
    pending = _pending()
    pending["kind"] = pending["kind"].removeprefix("core.")
    plan = plan_route("yes", pending=pending)
    assert plan is not None and plan.path == "confirmation"
    assert plan.kind.startswith("core.")


def test_a_plugin_fast_path_never_commits_early() -> None:
    import re as _re

    class _PluginHandler(Handler):
        name = "zz_probe"
        priority_band = 350
        tool_schema = {"name": "zz_probe"}
        display = HandlerDisplay(label="probe")
        plugin_slug = "probe"

        def __init__(self) -> None:
            self.fast_paths = [
                FastPath(_re.compile(r"^frobnicate the widget$"), _PluginHandler._go, early_commit="A"),
            ]

        async def _go(self, m, ctx, session):  # pragma: no cover - never dispatched
            raise AssertionError("dispatched")

        async def execute(self, intent, ctx, session):  # pragma: no cover
            raise AssertionError("dispatched")

    register_handler(_PluginHandler())
    try:
        plan = plan_route("frobnicate the widget")
        assert plan is not None and plan.handler.name == "zz_probe"
        assert early_commit_for("frobnicate the widget") is None
    finally:
        unregister_handler("zz_probe")


# ─── the router dry run is pure ───────────────────────────────────────────


def test_the_dry_run_dispatches_nothing(monkeypatch) -> None:
    """plan_route() is a plain function (no session, nothing awaited), and
    with every fast path's method and every confirmation resume swapped
    for one that fails the test, it still plans the whole corpus."""
    assert not inspect.iscoroutinefunction(plan_route)
    assert list(inspect.signature(plan_route).parameters) == ["raw_transcript", "pending"]

    def _boom(*a, **kw):
        raise AssertionError("the dry run dispatched a handler")

    originals = {h.name: list(h.fast_paths) for h in HANDLERS}
    try:
        for h in HANDLERS:
            h.fast_paths = [dataclasses.replace(as_fast_path(e), method=_boom) for e in h.fast_paths]
            monkeypatch.setattr(h, "handle_confirmation", _boom)
            monkeypatch.setattr(h, "execute", _boom)
        planned = 0
        for utterance in _all_utterances():
            if plan_route(utterance) is not None:
                planned += 1
            early_commit_for(utterance)
            early_commit_for(utterance, pending=_pending())
        assert planned > 100
    finally:
        for h in HANDLERS:
            h.fast_paths = originals[h.name]


def test_the_dry_run_agrees_with_the_plugin_contract_dry_run() -> None:
    """Two callers, one implementation: the install-time collision check
    and the early-commit plan pick the same handler for every utterance."""
    for utterance in _all_utterances():
        plan = plan_route(utterance)
        assert dry_run_winner(utterance, HANDLERS) == (plan.handler.name if plan else None), utterance


def test_a_plugin_that_sets_a_tier_is_told_it_is_ignored() -> None:
    import re as _re

    from domovoi.plugins_runtime.contracts import ContractReport, check_handlers

    class _Probe(Handler):
        name = "probe_thing"
        priority_band = 350
        tool_schema = {"name": "probe_thing"}
        display = HandlerDisplay(label="probe")

        def __init__(self) -> None:
            self.fast_paths = [
                FastPath(_re.compile(r"^frob$"), _Probe._go, early_commit="A"),
                FastPath(_re.compile(r"^frob more$"), _Probe._go),
            ]

        async def _go(self, m, ctx, session):  # pragma: no cover
            raise AssertionError

        async def execute(self, intent, ctx, session):  # pragma: no cover
            raise AssertionError

    report = ContractReport()
    check_handlers("probe", [_Probe()], report)
    assert report.ok(), report.errors
    assert [w for w in report.warnings if "early_commit" in w] == [
        "handler 'probe_thing': fast path '^frob$' sets early_commit='A', which is "
        "ignored on plugin fast paths: they never end a capture early"
    ]
