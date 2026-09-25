"""What the dashboard tells a person that admin is FOR.

Two sentences say it, and they are the only two places anybody is told:

* Settings → Configuration, the Admin card's subtitle
  (``web/static/settings.jsx``, ``AdminSection``);
* the admin-password modal's hint, which is the ONLY explanation on the
  Files page of why a password is being asked for at all
  (``web/static/components.jsx``, ``LoginModal``).

Both used to claim admin gates "file writes". That stopped being true
when saving became a household action and deleting stayed an admin one,
and both were corrected — with nothing in the repo asserting either. A
per-file revert of both files passed 83 tests, which is the same thing
as saying the correction was not made: the next merge resolution, refactor
or reword restores "and file writes" and every suite stays green. The
write-tier spec opens with exactly this — a stale rationale is worse than
none, because the next person restores the RULE to match it.

WHAT THIS MODULE ASSERTS, AND WHY IT IS NOT A COPY TEST. Pinning the
sentence would be the trap in the other direction: this is a pass whose
whole subject is wording, and a test that owns the words makes the next
improvement look like a regression. So it asserts the CLAIM instead, in
the two directions that matter and in a way that survives a rewrite:

* nothing in either sentence claims admin gates writing or saving a
  file — the specific untruth, in any of the shapes it comes in;
* every mention of saving is in the same breath as the word that says it
  is NOT gated, so "saving needs admin" cannot be reintroduced by
  rewording rather than by adding the old phrase back;
* deleting is named, because it is the one admin action left on the
  Files page and losing it silently costs the whole explanation.

Rendered, never grepped: both components are compiled with the
dashboard's own vendored Babel and rendered through
``jsx_interact_harness.js``, so what is asserted is what a person sees,
including a subtitle that reaches the screen only because ``Card``
renders the prop.

No DB, no ``requires_db`` — this must never skip. It needs ``node``, the
runtime the JSX compile check already relies on, and fails rather than
skips without it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INTERACT = Path(__file__).with_name("jsx_interact_harness.js")
COMPONENTS = "web/static/components.jsx"
SETTINGS = "web/static/settings.jsx"

# A signed-out, set-up install: the Admin card renders its subtitle and
# the modal renders its password field and hint.
SETUP = r"""
globalThis.Auth = {
  token: null,
  status: { setup_complete: true, authenticated: false },
  subscribe: () => () => {},
  refreshStatus: () => {},
  isLoggedIn: () => false,
  headers: () => ({}),
  login: () => Promise.resolve(true),
  setup: () => Promise.resolve(true),
  modalOpen: true,
};
"""

SCENARIOS = {
    "admin_card": {
        "files": [COMPONENTS, SETTINGS],
        "component": "AdminSection",
        "setup": SETUP,
        "script": "h.render(); await h.settle(); h.rerender(); "
                  "return { texts: h.text().map(String) };",
    },
    "login_modal": {
        "files": [COMPONENTS],
        "component": "LoginModal",
        "fnProps": ["onClose"],
        "setup": SETUP,
        "script": "h.render(); await h.settle(); h.rerender(); "
                  "return { texts: h.text().map(String) };",
    },
}


@pytest.fixture(scope="module")
def rendered() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(INTERACT), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return {k: " ".join(t for t in v["texts"] if t.strip()) for k, v in out.items()}


# The claim that is false: admin gates changing the CONTENT of a file.
# Several shapes of it, because the point is the claim and not a phrase.
WRITE_CLAIMS = re.compile(
    r"file writes?|writing files?|writes? to files?|file editing|editing files?",
    re.IGNORECASE,
)
# Saving, however it is conjugated.
SAVING = re.compile(r"\bsav(?:e|es|ing)\b", re.IGNORECASE)
# The words that put something on the NOT-admin side of a sentence.
EXEMPTING = re.compile(r"\bnever\b|\bdon'?t\b|\bdo not\b|\bwithout\b|\bno admin\b",
                       re.IGNORECASE)
# Removing something, however it is said.
REMOVING = re.compile(r"\bdelet\w*|\bremov\w*|\btrash\w*", re.IGNORECASE)


@pytest.mark.parametrize("where", ["admin_card", "login_modal"])
def test_the_rendered_sentence_is_actually_on_screen(rendered, where: str):
    """The guard on the guards: if the component stops rendering its
    explanation at all, every assertion below passes vacuously."""
    text = rendered[where]
    assert "admin" in text.lower(), text
    assert len(text) > 40, text


@pytest.mark.parametrize("where", ["admin_card", "login_modal"])
def test_neither_sentence_claims_admin_gates_changing_a_file(rendered, where: str):
    """The correction, as a property. Reword freely; put the claim back
    and this fails, which is the whole point."""
    text = rendered[where]
    found = WRITE_CLAIMS.search(text)
    assert found is None, (
        f"{where} tells the household that admin gates {found.group(0)!r} — "
        "saving a document is a device-tier action; only deleting is admin: "
        + text
    )


@pytest.mark.parametrize("where", ["admin_card", "login_modal"])
def test_saving_is_only_ever_named_as_something_admin_does_not_gate(
    rendered, where: str
):
    """Stronger than banning one phrase, and reword-proof: wherever these
    sentences mention saving at all, the same sentence has to say it is
    not gated. "Admin is needed for saving" cannot be smuggled back in by
    choosing different words for it."""
    text = rendered[where]
    mentions = [s for s in re.split(r"(?<=[.;])\s+", text) if SAVING.search(s)]
    assert mentions, (
        f"{where} no longer tells anyone whether saving needs admin — that is "
        "the question this sentence exists to answer: " + text
    )
    for sentence in mentions:
        assert EXEMPTING.search(sentence), (
            f"{where} mentions saving without saying it never needs admin: "
            f"{sentence!r}"
        )


@pytest.mark.parametrize("where", ["admin_card", "login_modal"])
def test_deleting_is_named_as_the_thing_that_does_need_admin(rendered, where: str):
    """Deleting is the only admin action left on the Files page, so the
    modal that pops there is the only place anybody is told why. Dropping
    it costs the explanation, not just a word."""
    text = rendered[where]
    assert REMOVING.search(text), (
        f"{where} does not say that removing files is what needs admin: " + text
    )


def test_the_two_sentences_do_not_contradict_each_other(rendered):
    """They are read minutes apart by the same person — one in Settings,
    one over whatever they were doing. If they ever disagree about
    saving, the one that is wrong is the one that will be believed."""
    card, modal = rendered["admin_card"], rendered["login_modal"]
    assert bool(WRITE_CLAIMS.search(card)) == bool(WRITE_CLAIMS.search(modal))
    assert bool(REMOVING.search(card)) == bool(REMOVING.search(modal))
