"""The admin sign-in and first-run setup put the caret in their first field.

Kamron, 2026-10-13, on the live box from a phone: the password prompt came
up and the keyboard with it, but the caret was not in the field, and typing
did nothing until he tapped it.

What the dialogs do now (web/static/components.jsx):

* The first field of the form showing takes the focus, with the caret at
  its end: the setup code on an unclaimed box, the password otherwise, the
  household token in "pair this browser". Never the dialog box or the
  backdrop: nothing else in them is focused at all.
* When the status read that says which form it is lands after the dialog
  opened, the field of the form that is showing then takes it.
* A dialog that is OPENING renders inside the call that opened it
  (AuthModalHost, ReactDOM.flushSync): "sign in" opens it from its click,
  so the focus lands inside the tap, which is what a phone needs to raise
  its keyboard on the field. Before, React rendered it just after the tap
  returned. Any other change re-renders on React's own schedule.

Driven through domovoi/tests/jsx_interact_harness.js (the dashboard's own
Babel, a small stateful React; ``refs`` records what focus() was called
on). No DB, never ``requires_db``; needs ``node`` and fails without it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
COMPONENTS = "web/static/components.jsx"
SOURCE = REPO_ROOT / COMPONENTS

CLAIMED = {"setup_complete": True, "authenticated": False}
UNCLAIMED = {"setup_complete": False, "authenticated": False}

CODE = "eight-words-separated-by-dashes"
TOKEN = "household token"


def _auth(status, *, later=None) -> str:
    """An Auth stub. `later`: what refreshStatus answers (a status read
    that lands after the dialog opened)."""
    return (
        "globalThis.Auth = {"
        f" status: {json.dumps(status)},"
        " subscribe: () => () => {}, isLoggedIn: () => false, headers: () => ({}),"
        " modalOpen: true, modalSeq: 1, loginReason: null,"
        f" refreshStatus: () => {{ Auth.status = {json.dumps(later)}; return Promise.resolve(Auth.status); }},"
        " closeModal: () => {}, login: () => Promise.resolve({}), setup: () => Promise.resolve({}),"
        "};"
    )


# What focus() was called on, as the field's placeholder or type.
FOCUSED = "h.focused().map((f) => f.props.placeholder || f.props.type || f.type)"

SCENARIOS = {
    "login": {
        "files": [COMPONENTS], "component": "LoginModal", "fnProps": ["onClose"], "refs": True,
        "setup": _auth(CLAIMED),
        "script": f"h.render(); await h.settle(); h.rerender(); return {{ focused: {FOCUSED} }};",
    },
    "setup": {
        "files": [COMPONENTS], "component": "LoginModal", "fnProps": ["onClose"], "refs": True,
        "setup": _auth(UNCLAIMED),
        "api": {"GET /api/capabilities": {"host_kind": "linux"}},
        "script": f"h.render(); await h.settle(); h.rerender(); return {{ focused: {FOCUSED} }};",
    },
    # Opened before anyone asked the server (a refused request on a page
    # that never read the status): the login form shows first, then the
    # read says the box is unclaimed.
    "status_lands_late": {
        "files": [COMPONENTS], "component": "LoginModal", "fnProps": ["onClose"], "refs": True,
        "setup": _auth(None, later=UNCLAIMED),
        "api": {"GET /api/capabilities": {"host_kind": "linux"}},
        "script": f"""
          h.render();
          const first = {FOCUSED};
          for (let i = 0; i < 4; i++) {{ await h.settle(); h.rerender(); }}
          return {{ first, after: {FOCUSED},
                    title: (h.find((e) => e.props.className === 'ttl') || {{}}).text }};
        """,
    },
    # Typing does not move the caret back to the start or re-focus.
    "typing_keeps_one_focus": {
        "files": [COMPONENTS], "component": "LoginModal", "fnProps": ["onClose"], "refs": True,
        "setup": _auth(CLAIMED),
        "script": f"""
          h.render();
          await h.type({{ type: 'input' }}, 'correct horse');
          await h.type({{ type: 'input' }}, 'correct horse battery');
          return {{ focused: {FOCUSED} }};
        """,
    },
    "pair": {
        "files": [COMPONENTS], "component": "PairModal", "fnProps": ["onClose"], "refs": True,
        "setup": _auth(CLAIMED),
        "script": f"h.render(); await h.settle(); h.rerender(); return {{ focused: {FOCUSED} }};",
    },
    # The caret helper on its own: focus without scrolling, caret at the
    # end; a field that can't place a caret, and nothing at all, are fine.
    "caret_helper": {
        "files": [COMPONENTS], "component": "focusFirstField",
        "script": r"""
          const f = h.global('window').__component;
          const seen = [];
          const field = { value: 'half-typed', focus: (o) => seen.push(['focus', o]),
                          setSelectionRange: (a, b) => seen.push(['caret', a, b]) };
          f(field);
          const old = [];
          f({ value: 'x', focus: (o) => { if (o) throw new Error('no options here'); old.push('focus'); } });
          let threw = null;
          try { f(null); f({}); f({ focus: () => {}, setSelectionRange: () => { throw new Error('email'); } }); }
          catch (e) { threw = String(e); }
          return { seen, old, threw };
        """,
    },
    # The host renders a dialog that is OPENING inside the call that opened
    # it, and only then.
    "host_opens_at_once": {
        "files": [COMPONENTS], "component": "AuthModalHost",
        "setup": r"""
          window.__flushed = [];
          window.__listener = null;
          globalThis.ReactDOM = { flushSync: (fn) => { window.__flushed.push(
            Auth.modalOpen ? 'login' : (Auth.pairModalOpen ? 'pair' : 'none')); fn(); } };
          globalThis.Auth = {
            status: { setup_complete: true, authenticated: false },
            modalOpen: false, pairModalOpen: false, modalSeq: 0, loginReason: null,
            subscribe: () => () => {},
            subscribeModal: (fn) => { window.__listener = fn; return () => { window.__listener = null; }; },
            isLoggedIn: () => false, headers: () => ({}),
            refreshStatus: () => Promise.resolve(Auth.status),
            closeModal: () => {}, closePairModal: () => {},
          };
          window.__notify = () => window.__listener && window.__listener();
        """,
        "script": r"""
          const w = h.global('window');
          const A = h.global('Auth');
          h.render();
          const titles = () => h.findAll((e) => e.props.className === 'ttl').map((e) => e.text);
          const step = (label) => ({ label, flushed: w.__flushed.slice(), titles: (h.rerender(), titles()) });
          const out = [];
          out.push(step('closed'));
          A.modalOpen = true; A.modalSeq = 1; w.__notify();
          out.push(step('login opens'));
          A.status = { setup_complete: true, authenticated: true }; w.__notify();
          out.push(step('a change while open'));
          A.modalOpen = false; w.__notify();
          out.push(step('closed again'));
          A.pairModalOpen = true; w.__notify();
          out.push(step('pair opens'));
          A.modalOpen = true; A.modalSeq = 2; w.__notify();
          out.push(step('login over the pair modal'));
          return out;
        """,
    },
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("auth-focus") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_the_login_puts_the_caret_in_the_password(driven) -> None:
    assert driven["login"]["focused"] == ["password"]


def test_first_run_setup_puts_the_caret_in_the_setup_code(driven) -> None:
    assert driven["setup"]["focused"] == [CODE]


def test_a_status_that_lands_late_moves_the_caret_to_the_form_now_showing(driven) -> None:
    o = driven["status_lands_late"]
    assert o["first"] == ["password"]
    assert o["after"] == ["password", CODE]
    assert o["title"] == "first-run admin setup"


def test_typing_does_not_take_the_focus_again(driven) -> None:
    assert driven["typing_keeps_one_focus"]["focused"] == ["password"]


def test_the_pair_modal_puts_the_caret_in_the_token(driven) -> None:
    assert driven["pair"]["focused"] == [TOKEN]


def test_the_caret_goes_to_the_end_and_odd_fields_are_harmless(driven) -> None:
    o = driven["caret_helper"]
    assert o["seen"] == [["focus", {"preventScroll": True}], ["caret", 10, 10]]
    assert o["old"] == ["focus"]          # a browser without focus options
    assert o["threw"] is None


def test_an_opening_dialog_renders_inside_the_call_that_opened_it(driven) -> None:
    steps = {s["label"]: s for s in driven["host_opens_at_once"]}
    assert steps["closed"]["flushed"] == [] and steps["closed"]["titles"] == []
    assert steps["login opens"]["flushed"] == ["login"]
    assert steps["login opens"]["titles"] == ["admin login"]
    # Anything else re-renders on React's schedule.
    assert steps["a change while open"]["flushed"] == ["login"]
    assert steps["closed again"]["flushed"] == ["login"]
    assert steps["closed again"]["titles"] == []
    assert steps["pair opens"]["flushed"] == ["login", "pair"]
    assert steps["pair opens"]["titles"] == ["pair this browser"]
    assert steps["login over the pair modal"]["flushed"] == ["login", "pair", "login"]
    assert steps["login over the pair modal"]["titles"] == ["admin login"]


def _component(name: str) -> str:
    src = SOURCE.read_text(encoding="utf-8")
    start = src.index(f"const {name} = (")
    end = src.index("\n};\n", start)
    return src[start:end]


@pytest.mark.parametrize("name", ["LoginModal", "PairModal"])
def test_only_inputs_take_the_focus_and_nothing_races_it(name) -> None:
    """One owner for the focus: no autoFocus beside the layout-pass focus,
    and the focus refs sit on <input> elements only (never the dialog box,
    the backdrop or a button)."""
    body = _component(name)
    assert "autoFocus" not in body
    refs = re.findall(r"<(\w+)[^<>]*\bref=\{(firstField|tokenField)\}", body)
    assert refs, name
    assert {tag for tag, _ in refs} == {"input"}
