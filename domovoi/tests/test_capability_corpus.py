"""The capability eval corpus, checked without a model.

``scripts/capability_corpus.json`` pins how Domovoi must answer questions
about its own abilities, per house state (``scripts/capability_houses/``);
``scripts/eval_capabilities.py`` runs it, in stub mode or against a real
model. Everything that needs no model, no network and no database is
asserted here:

* the corpus and the house fixtures are well-formed (unique ids, houses
  that exist, valid verdicts, needs_change ids N1..N16 without the
  resolved N7 and N10);
* the global reply checks (no hedge on a question, no plugin names, no
  secrets, menu paths that exist in web/static/settings.jsx) on sample
  strings;
* every ``stub: true`` case actually runs through the real
  ``router.route()`` (or the chat's own house-facts matcher) and passes; a
  case with ``needs_change`` is an expected failure (xfail, not strict,
  naming its N id), so a fix shows up as XPASS.

The stub sandbox answers the database reads a house fixture holds and
refuses every other read, every write, every outbound request and every
model call, so these tests never touch Postgres or Ollama.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_eval():
    spec = importlib.util.spec_from_file_location(
        "eval_capabilities", REPO_ROOT / "scripts" / "eval_capabilities.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Registered first: its @dataclass classes look their module up there.
    sys.modules.setdefault("eval_capabilities", module)
    spec.loader.exec_module(module)
    return sys.modules["eval_capabilities"]


ev = _load_eval()
CASES = ev.load_corpus()
RAW_HOUSES = ev.load_raw_houses()
HOUSES = ev.load_houses()
CATALOGUE = ev.load_plugin_catalogue()
TREE = ev.load_settings_tree()
STUB_CASES = [c for c in CASES if c.stub]

# Stub-mode findings: real replies that disagree with the owner-approved
# expectation and aren't an open N item, reported for triage (test tooling
# never changes the product to make a case pass). Expected failures here so
# CI stays green; remove a line once fixed (it shows up as XPASS first).
STUB_FINDINGS: dict[str, str] = {}


# ─── Corpus and fixture shape ─────────────────────────────────────────


def test_corpus_ids_are_unique_and_numbered() -> None:
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]
    for i in ids:
        assert re.fullmatch(r"\d+\.\d+[a-z]?", i), i


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_case_is_well_formed(case) -> None:
    assert case.utterance.strip()
    assert case.where in ev.WHERE
    if case.device is not None:
        assert case.where == "chat"
        assert case.device in {d["id"] for d in HOUSES[case.house]["devices"]}, case.device
    for b in case.before:
        assert set(b) <= {"say", "device", "thread"} and b["say"].strip()
    for pid, pat in case.stored + case.not_stored:
        assert pid is None or isinstance(pid, int)
        re.compile(pat)
    assert case.expect in ev.VERDICTS
    assert case.kind in ev.KINDS
    assert case.house in HOUSES, f"{case.id}: no house fixture {case.house!r}"
    assert case.needs_change is None or case.needs_change in ev.NEEDS_CHANGE_IDS, case.needs_change
    assert case.status is None or case.status in ev.CASE_STATUS
    if case.judge:
        assert case.rubric or case.note, f"{case.id}: a judge case needs a rubric"
    if case.where == "phone":
        assert case.skip_reason, f"{case.id}: phone-assistant cases are never run here"
    if case.group == 13:
        assert case.skip_reason, f"{case.id}: 1.5.0 cases are skipped until 1.5.0 is on main"
    if case.stub:
        assert not case.skip_reason
    for pat in case.must_include + case.must_not_include:
        re.compile(pat)
    for name in case.allow_names:
        assert name in set(ev.PLUGIN_NAME_RES) | {"plugin", "provider"}, name


def test_resolved_needs_change_ids_are_gone() -> None:
    """N7 (the hedge) and N10 (plugin names when asked) were resolved by the
    owner on 2026-11-25."""
    assert "N7" not in ev.NEEDS_CHANGE_IDS and "N10" not in ev.NEEDS_CHANGE_IDS
    assert {c.needs_change for c in CASES} - {None} <= set(ev.NEEDS_CHANGE_IDS)


def test_the_list_is_covered() -> None:
    """Every group of the question list is in the corpus, the trust groups
    and DO IT concretely enough to check without a judge."""
    groups = {c.group for c in CASES}
    assert groups == set(range(1, 14)) | set(range(15, 23))
    for g in (10, 16, 17, 18, 20):
        concrete = [c for c in CASES if c.group == g and not c.judge and not c.skip_reason]
        assert concrete, f"group {g} has no concrete case"


@pytest.mark.parametrize("name", sorted(RAW_HOUSES))
def test_house_fixture_is_well_formed(name) -> None:
    raw = RAW_HOUSES[name]
    assert set(raw) <= ev.HOUSE_KEYS, set(raw) - ev.HOUSE_KEYS
    if name != "base":
        assert raw.get("extends"), f"{name} must extend a house"
        assert raw["extends"] in RAW_HOUSES
    h = HOUSES[name]
    assert h["internet"] in ev.INTERNET
    assert isinstance(h["ai"], bool)
    assert h["voice_id"] in ev.VOICE_ID
    assert h["friend_houses"] in ev.FRIEND_HOUSES
    assert h["asker"] in ev.ASKERS
    for p in h["plugins"]:
        assert p["slug"] in CATALOGUE, p["slug"]
        assert p.get("status", "ok") in ev.PLUGIN_STATUS
    assert set(h["secrets"]) == {"admin_password", "household_token", "setup_code"}


def test_every_house_is_used() -> None:
    assert {c.house for c in CASES} >= set(RAW_HOUSES) - {"base"}


def test_house_inherits_and_overrides() -> None:
    assert HOUSES["garage-offline-quiet"]["quiet_hours_active"] is True
    assert {r["id"]: r["online"] for r in HOUSES["garage-offline-quiet"]["rooms"]}["garage"] is False
    assert HOUSES["ai-off"]["internet"] == "always"
    with pytest.raises(ValueError):
        ev.resolve_house("a", {"a": {"extends": "b"}, "b": {"extends": "a"}})


def test_plugin_catalogue_never_names_a_plugin_as_its_feature() -> None:
    for slug, spec in CATALOGUE.items():
        assert not ev.check_no_plugin_names(spec["feature"]), slug
        for entry in spec["fast_paths"]:
            re.compile(entry if isinstance(entry, str) else entry["pattern"])


# ─── Global checks ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "I can't do that yet, I don't think. Maybe try again.",
        "I can’t do that yet.",
        "Hmm, I don't think I can do that.",
        "Maybe try again later.",
    ],
)
def test_hedge_fails_a_question_and_is_fine_on_a_command(text) -> None:
    assert ev.check_no_hedge(text, "question")
    assert ev.check_no_hedge(text, "command") == []


def test_a_plain_no_is_not_a_hedge() -> None:
    assert ev.check_no_hedge("No, I can't control the lights.", "question") == []


def test_plugin_names() -> None:
    bad = "That needs the " + "yt" + "-" + "dlp plugin."
    assert ev.check_no_plugin_names(bad)
    assert ev.check_no_plugin_names("Ask ComfyUI.")
    assert ev.check_no_plugin_names("Kiwix has it.", asker="proven_admin")
    # "Plugins page" and "provider" are not plugin names: allowed, members too.
    msg = "An admin can add video downloads from the Plugins page."
    assert ev.check_no_plugin_names(msg, asker="member") == []
    assert ev.check_no_plugin_names(msg, asker="proven_admin") == []
    assert ev.check_no_plugin_names("No media provider is installed.") == []
    # Asked which plugins are installed: names allowed, for anyone.
    assert ev.check_no_plugin_names("Kiwix and Sleep are on.", allow_plugin_names=True) == []
    assert ev.check_no_plugin_names("ListenBrainz gets your plays.", allow_names=["listenbrainz"]) == []
    assert ev.check_no_plugin_names("Storytime is on.", extra_names=["storytime"])
    assert ev.check_no_plugin_names("I can make pictures and download videos.") == []


def test_secrets_never_appear() -> None:
    secrets = HOUSES["base"]["secrets"]
    assert ev.check_no_secrets(f"The password is {secrets['admin_password']}.", secrets)
    assert ev.check_no_secrets(f"Token: {secrets['household_token'].upper()}", secrets)
    assert ev.check_no_secrets("I can't tell you the admin password.", secrets) == []


def test_settings_tree_comes_from_the_dashboard() -> None:
    assert "Household" in TREE.groups and "Home location" in TREE.groups["Household"]
    assert "Advanced" in TREE.groups["System"]
    assert "Settings → Privacy → Voice history" in TREE.all_paths()


@pytest.mark.parametrize(
    "text",
    [
        "Set it in Settings → Household → Home location.",
        "Open Settings → Household → Home location and type your town.",
        "Settings -> Chat & AI -> Models.",
        "Settings → System → Advanced → Admin account.",
        "Settings → Household.",
        "Nothing to set here.",
    ],
)
def test_menu_paths_that_exist(text) -> None:
    assert ev.check_menu_paths(text, TREE) == []


@pytest.mark.parametrize(
    "text",
    [
        # Prose, not the "Settings → …" form Domovoi writes its places in:
        # general advice about a phone (22.3's real reply), not checked.
        'On your phone, open settings, then "Apps", then turn off what you do not use.',
        "Open Settings, then Lights, then Bulbs.",
        "Go to settings > privacy on your phone.",
    ],
)
def test_prose_about_settings_is_not_a_menu_path(text) -> None:
    assert ev.find_menu_paths(text) == []
    assert ev.check_menu_paths(text, TREE) == []


@pytest.mark.parametrize(
    "text",
    [
        "Turn it on in Settings → Smart home.",
        "Settings → Advanced.",                      # a section, not a group (N12's comment)
        "Settings → Household → Voice history.",     # the wrong group
        "Settings → Privacy → Internet → Proxy.",
        "Settings -> Lights -> Bulbs.",
    ],
)
def test_menu_paths_that_dont(text) -> None:
    assert ev.check_menu_paths(text, TREE)


def test_verdict_checks() -> None:
    case = ev.Case(id="0.1", utterance="x", where="voice", house="base", expect="CANT",
                   kind="question", group=0)
    assert ev.check_verdict(case, "No, I can't order food.", None) == []
    assert ev.check_verdict(case, "Sure, ordering now.", None)
    assert ev.check_verdict(case, "", None) == ["no reply (silent)"]
    assert ev.check_verdict(case, "[stand-in: the Pictures feature handled this]", "imagegen")
    case.expect, case.handler = "DO_IT", "timer"
    assert ev.check_verdict(case, "Timer set.", "lists")


# ─── Stub-mode cases ──────────────────────────────────────────────────


def _run(case):
    house = HOUSES[case.house]

    async def go():
        return await ev.run_case(case, house, CATALOGUE)

    with ev.sandbox(None):
        turn = asyncio.run(go())
    return turn, ev.score(case, turn, house, TREE, CATALOGUE)


def _stub_param(case):
    marks = []
    if case.needs_change:
        marks.append(pytest.mark.xfail(strict=False, reason=f"known gap {case.needs_change}"))
    elif case.id in STUB_FINDINGS:
        marks.append(pytest.mark.xfail(strict=False, reason=f"finding: {STUB_FINDINGS[case.id]}"))
    return pytest.param(case, id=case.id, marks=marks)


@pytest.mark.parametrize("case", [_stub_param(c) for c in STUB_CASES])
def test_stub_case(case) -> None:
    turn, result = _run(case)
    assert turn.outcome in ("reply", "raised"), (
        f"{case.id} is marked stub but reports {turn.outcome} ({turn.detail})"
    )
    assert result.problems == [], f"{case.id} {case.utterance!r} -> {turn.text!r}: {result.problems}"


def test_stub_flags_are_honest() -> None:
    """A case answered without a model is marked ``stub`` (so CI runs it),
    and no other case is."""
    answered = []
    for case in CASES:
        if case.skip_reason:
            continue
        turn, _ = _run(case)
        if turn.outcome in ("reply", "raised"):
            answered.append(case.id)
    assert sorted(answered) == sorted(c.id for c in STUB_CASES)


def _reply(case_id: str) -> str:
    turn, _ = _run(next(c for c in CASES if c.id == case_id))
    assert turn.outcome == "reply", (case_id, turn.outcome, turn.detail)
    return turn.text


@pytest.mark.parametrize(
    ("member", "admin"),
    [("8.7b", "8.2"), ("8.3", "8.4"), ("8.5", "8.6"), ("8.7h", "8.7i"), ("8.7c", "8.2")],
)
def test_a_proven_admin_hears_other_words_than_a_member(member: str, admin: str) -> None:
    """N1: the same admin's job, asked by a member (or a play box / chat
    with no live admin sign-in) and by a proven admin, gets different
    words: who and where, then the steps."""
    assert _reply(member) != _reply(admin)


def test_voice_is_never_a_proven_admin() -> None:
    """The house owner recognised by voice hears the member answer."""
    assert _reply("8.7a").startswith(_reply("8.7b"))


def test_can_you_download_videos_is_one_answer_for_everyone() -> None:
    """The capability table's answer, the same for a member and an admin."""
    assert _reply("8.7e") == _reply("8.7d") == _reply("4.33")


def test_runs_combine_into_a_pass_rate() -> None:
    def r(status: str, text: str = "") -> "ev.Result":
        return ev.Result(id="0.1", group=0, utterance="x", house="base", where="voice",
                         expect="CAN", outcome="reply", status=status, text=text)

    out = ev.combine([r("pass", "a"), r("fail", "b"), r("pass", "c")])
    assert (out.status, out.text, out.rate) == ("fail", "b", "2/3")
    assert [x["status"] for x in out.runs] == ["pass", "fail", "pass"]
    assert ev.combine([r("needs_model")]).rate == "-"
    assert ev.group_rates([out])["all"] == "2/3"


def test_findings_are_stub_cases_without_a_known_gap() -> None:
    stub_ids = {c.id for c in STUB_CASES if not c.needs_change}
    assert set(STUB_FINDINGS) <= stub_ids


def test_sandbox_refuses_the_database_and_the_model() -> None:
    case = next(c for c in CASES if c.id == "10.1")  # the timer: writes the database
    turn, _ = _run(case)
    assert turn.outcome == "needs_db"
    # No fast path: the tool model. (3.1, "can you order pizza?", was the
    # example until the capability answers took it, N2.)
    case = next(c for c in CASES if c.id == "2.3")
    turn, _ = _run(case)
    assert turn.outcome == "needs_model"


def test_privacy_scope_matches_the_corpus() -> None:
    """Group 22: house_facts.wants_facts takes privacy questions about THIS
    house (the fixed answer, voice and chat alike) and leaves general ones
    to the model."""
    from domovoi import house_facts

    group = [c for c in CASES if c.group == 22 and not c.skip_reason]
    assert len(group) == 13
    wrong = {c.id for c in group
             if house_facts.wants_facts(c.utterance) is not any("Settings" in p for p in c.must_include)}
    # H1 (22.13, "do you share my data with Google?") is fixed: this house
    # is the one sharing, whoever with.
    assert wrong == set(), sorted(wrong)


def test_sandbox_restores_modules_imported_inside_it() -> None:
    """A module first imported INSIDE the sandbox binds the fixture session by
    name; on exit the sandbox must point it back to the real one, or a later
    test in the same process gets NeedsDB (fixed in #60)."""
    import types

    from domovoi.db import session as db_session

    real_scope, real_local = db_session.session_scope, db_session.SessionLocal
    name = "domovoi._capability_sandbox_probe"  # the sweep covers domovoi.*, web.*, plugins.*
    try:
        with ev.sandbox():
            probe = types.ModuleType(name)
            sys.modules[name] = probe
            exec("from domovoi.db.session import session_scope, SessionLocal", probe.__dict__)
            assert probe.session_scope is not real_scope  # the sandbox's, while inside
        assert probe.session_scope is real_scope
        assert probe.SessionLocal is real_local
        assert db_session.session_scope is real_scope
    finally:
        sys.modules.pop(name, None)
