"""B1 — Settings → Devices shows the household token to an admin only, and
the server switcher asks before it trusts a newly found server.

Acceptance (security batch B1, FE-2 + the device-tier client work):

2. Settings → Devices shows the household token to an admin session and
   nothing to a cookie-less viewer — and fetches nothing for the viewer,
   so opening the tab signed out never pops the login modal.
3. Selecting a newly discovered server in the switcher shows its address
   and persists it only after the trust confirmation.

The panels are driven for real through domovoi/tests/jsx_interact_harness.js
(the dashboard's own Babel, a small stateful React, a scripted data
layer). No DB, never ``requires_db``; needs ``node`` and fails without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
# Runs the SHIPPED web/static/auth.js and reports what its
# normalizeDeviceToken did to a table of values.
NORMALIZE_HARNESS = Path(__file__).with_name("device_token_normalize_harness.js")
COMPONENTS = "web/static/components.jsx"
SETTINGS = "web/static/settings.jsx"

# The two lines the set dialog chooses between, live as the field is typed
# (custom-token-spec.md ADDENDUM). Copied here character for character on
# purpose: this is the copy, and a silent edit to it is a change to what
# the product promises about how a token will be matched.
RULE_FORGIVING = (
    "Typing this back is forgiving — capitals, spaces and underscores all match."
)
RULE_EXACT = "This one must be typed exactly, character for character."

# Typed into the dialog, one after another, to watch the line change.
RULE_PROBES = [
    "$$bills_yall-market!!1999",
    "$$bills-yall-market!!1999",
    "frontdoorcats1999",
    "My House Is Red",
    "acorn-maple-otter-basin-cedar-ridge-harbor-willow",
    "Maple Street, 1984!",
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "  padded token here  ",
    "  frontdoorcats1999  "
]

# Values the two implementations have to agree about. The ADDENDUM names
# the first ten; the rest are the tokens this feature's own tests and docs
# use, a generated phrase, and the whole printable-ASCII run the SET rule
# allows.
AWKWARD_TOKENS = [
    "$$bills_yall-market!!1999",      # the ADDENDUM's example: NOT canonical
    "$$bills-yall-market!!1999",      # the near miss that gets refused
    "$$billsyall1999",
    "My House Is Red",
    "  padded  ",
    "a--b",
    "a__b",
    "a_-b",
    "-leading",
    "trailing-",
    "0123456789abcdef" * 4,           # a 64-hex token from an older install
    "acorn-maple-otter-basin-cedar-ridge-harbor-willow",
    "ACORN MAPLE OTTER BASIN CEDAR RIDGE HARBOR WILLOW",
    "frontdoorcats1999",
    "Maple Street, 1984!",
    "MyT0ken!!going",
    'Say "hi", pal$x`y` z',
    "a - b _ c",
    "tab\there too",
    "\u00a0nbsp\u00a0padded\u00a0",
    "".join(chr(c) for c in range(0x20, 0x7F)),
    "---",
    "   ",
    "",
]
# ...and each one's stored form, since that is what the dialog tests.
AWKWARD_TOKENS += [v.strip() for v in AWKWARD_TOKENS if v.strip() not in AWKWARD_TOKENS]

# The copy of the helper the scenarios below stub onto their Auth object,
# because a `setup` snippet replaces Auth wholesale and auth.js is not one
# of the files the JSX harness loads. It goes through the SAME agreement
# table as the shipped helper (test_the_stub_the_scenarios_use_is_the_shipped_helper),
# so a scenario can never prove the dialog right against a stub that has
# drifted from what a browser actually runs.
NORMALIZE_STUB = (
    "(v) => String(v == null ? '' : v).trim().toLowerCase()"
    ".replace(/[\\s_-]+/g, '-').replace(/^-+|-+$/g, '')"
)

TOKEN = "hh-0123456789abcdef"
ROTATED = "hh-rotated-fedcba9876543210"
# A token an admin chose: spaces, a comma and punctuation, none of which
# the old lowercase-and-hyphens rule would have allowed through.
CHOSEN = "Maple Street, 1984!"
SET_OK = {"token": CHOSEN, "header": "X-Device-Token", "rotated": True}

SAVE_SCRIPT = (
    """
          h.render();
          await h.click({ type: 'button', text: 'set\u2026' });
          await h.type({ placeholder: 'at least 12 characters' }, '  """
    + CHOSEN
    + """  ');
          await h.click({ type: 'button', text: 'Save token' });
          return { calls: h.calls.map((c) => `${c.method} ${c.path}`),
                   body: h.calls[0] && h.calls[0].body,
                   paired: h.global('window').__paired,
                   dialog: !!h.find({ text: 'Set a custom household token' }) };
    """
)

# The harness's default Auth is a signed-in admin. These `setup` snippets
# replace it for the other cases; they run inside the sandbox before the
# page files load.
VIEWER_SETUP = r"""
Auth = { status: { setup_complete: true, authenticated: false }, subscribe: () => () => {},
         isLoggedIn: () => false, headers: () => ({}), modalOpen: false,
         refreshStatus: () => Promise.resolve(Auth.status), deviceToken: () => null, pair: () => true };
"""
COOKIE_SETUP = r"""
Auth = { status: { setup_complete: true, authenticated: true }, subscribe: () => () => {},
         isLoggedIn: () => false, headers: () => ({}), modalOpen: false,
         refreshStatus: () => Promise.resolve(Auth.status), deviceToken: () => null, pair: () => true };
"""
ADMIN_SETUP = r"""
window.__paired = null;
Auth = { status: { setup_complete: true, authenticated: true }, subscribe: () => () => {},
         isLoggedIn: () => true, headers: () => ({}), modalOpen: false,
         refreshStatus: () => Promise.resolve(Auth.status),
         deviceToken: () => window.__paired, pair: (t) => { window.__paired = t; return true; } };
navigator.clipboard = { writeText: (t) => { window.__copied = t; return Promise.resolve(); } };
"""
ADMIN_SETUP_CANON = ADMIN_SETUP + f"Auth.normalizeDeviceToken = {NORMALIZE_STUB};\n"

# A ServerStore stub with the real trust semantics, recording what the
# switcher persisted; `probe` answers for the manual-add path.
SWITCHER_SETUP = r"""
window.__store = { trusted: [], saved: [], selected: null, trustCalls: 0 };
ServerStore = {
  current: () => '',
  currentLabel: () => 'domovoi.lan:6369',
  hostOf: (url) => url.replace(/^https?:\/\//, ''),
  list: () => window.__store.saved.slice(),
  upsert: (url, name) => { window.__store.saved.push({ url, name: name || null }); },
  remove: () => {},
  isTrusted: (url) => !url || window.__store.trusted.includes(url),
  trust: (url) => { window.__store.trusted.push(url); window.__store.trustCalls += 1; },
  untrust: () => {},
  select: (url) => {
    if (url && !window.__store.trusted.includes(url)) return false;
    window.__store.selected = url; return true;
  },
  scanPrefix: () => null,
  probe: (url) => Promise.resolve({ url, name: 'kitchen-box' }),
  scan: () => Promise.resolve([]),
};
"""

SCENARIOS = {
    "admin_sees_the_token": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": ADMIN_SETUP,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"}},
        "script": r"""
          h.render();
          const code = h.find({ type: 'code' });
          const copy = h.find({ type: 'button', text: 'copy' });
          await h.click({ type: 'button', text: 'copy' });
          return { token: code && code.text, texts: h.text(), hookCalls: h.hookCalls,
                   copied: h.global('window').__copied, hasCopy: !!copy,
                   hasRotate: !!h.find({ type: 'button', text: 'rotate' }),
                   hasSet: !!h.find({ type: 'button', text: 'set…' }) };
        """,
    },
    "cookie_session_sees_the_token": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": COOKIE_SETUP,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"}},
        "script": "h.render(); const c = h.find({ type: 'code' }); return { token: c && c.text };",
    },
    "viewer_sees_nothing": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": VIEWER_SETUP,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"}},
        "script": "h.render(); return { tree: h.tree().length, texts: h.text(), hookCalls: h.hookCalls };",
    },
    # The whole Devices tab, signed out: the token card is absent and the
    # tab never asked for the token.
    "devices_panel_signed_out": {
        "files": [COMPONENTS, SETTINGS], "component": "DevicesPanel",
        "setup": VIEWER_SETUP,
        "api": {"GET /api/devices": [], "GET /api/music/queue-blocks": [], "GET /api/music/now-playing": [],
                "GET /api/files/device-blocks": [], "GET /api/auth/device-token": {"token": TOKEN}},
        "script": r"""
          h.render();
          const cards = h.findAll((el) => el.type === 'div' && (el.props.className || '') === 't').map((d) => d.text);
          return { texts: h.text(), hookCalls: h.hookCalls, cards };
        """,
    },
    "rotate_asks_then_posts_and_re_pairs_this_browser": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": ADMIN_SETUP,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"},
                "POST /api/auth/device-token/rotate": {"token": ROTATED, "header": "X-Device-Token", "rotated": True}},
        "script": r"""
          h.render();
          await h.click({ type: 'button', text: 'rotate' });
          const dialog = h.find({ text: 'Rotate the household token?' });
          const afterAsk = { dialog: !!dialog, calls: h.calls.length };
          await h.click({ type: 'button', text: 'Cancel' });
          const afterCancel = { dialog: !!h.find({ text: 'Rotate the household token?' }), calls: h.calls.length };
          await h.click({ type: 'button', text: 'rotate' });
          await h.click({ type: 'button', text: 'Rotate' });
          return { afterAsk, afterCancel, calls: h.calls.map((c) => `${c.method} ${c.path}`),
                   paired: h.global('window').__paired, texts: h.text() };
        """,
    },
    # ── the set dialog ────────────────────────────────────────────────
    "set_asks_first_and_posts_nothing_until_it_is_saved": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": ADMIN_SETUP,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"},
                "POST /api/auth/device-token": SET_OK},
        "script": r"""
          h.render();
          const before = h.calls.length;
          await h.click({ type: 'button', text: 'set\u2026' });
          const dialog = h.find({ text: 'Set a custom household token' });
          const afterOpen = { dialog: !!dialog, calls: h.calls.length,
                              counter: h.text().some((t) => t.includes('0 characters.')) };
          await h.click({ type: 'button', text: 'Cancel' });
          const afterCancel = { dialog: !!h.find({ text: 'Set a custom household token' }),
                                calls: h.calls.length };
          return { before, afterOpen, afterCancel };
        """,
    },
    "eleven_characters_leaves_the_save_button_disabled": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": ADMIN_SETUP,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"},
                "POST /api/auth/device-token": SET_OK},
        "script": r"""
          h.render();
          await h.click({ type: 'button', text: 'set\u2026' });
          const at = async (v) => {
            await h.type({ placeholder: 'at least 12 characters' }, v);
            const btn = h.find({ type: 'button', text: 'Save token' });
            return { disabled: !!btn.props.disabled,
                     counter: h.text().some((t) => t.includes(v.trim().length + ' characters.')) };
          };
          const eleven = await at('12345678901');
          // Padding does not buy length: the counter counts the STORED form.
          const padded = await at('   12345678901   ');
          const twelve = await at('123456789012');
          return { eleven, padded, twelve, calls: h.calls.length };
        """,
    },
    "saving_posts_the_token_and_re_pairs_this_browser": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": ADMIN_SETUP,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"},
                "POST /api/auth/device-token": SET_OK},
        "script": SAVE_SCRIPT,
    },
    "a_server_refusal_lands_inline_next_to_the_field": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": ADMIN_SETUP,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"},
                "POST /api/auth/device-token":
                    {"__error": {"status": 400, "message": "at least 12 characters"}}},
        "script": r"""
          h.render();
          await h.click({ type: 'button', text: 'set\u2026' });
          await h.type({ placeholder: 'at least 12 characters' }, 'not-a-good-one');
          await h.click({ type: 'button', text: 'Save token' });
          return { dialog: !!h.find({ text: 'Set a custom household token' }),
                   inline: h.text().some((t) => t.includes('at least 12 characters')),
                   paired: h.global('window').__paired,
                   calls: h.calls.map((c) => `${c.method} ${c.path}`) };
        """,
    },
    # ── the ADDENDUM: which rule will match this token ───────────────
    "the_rule_line_follows_what_is_typed": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": ADMIN_SETUP_CANON,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"},
                "POST /api/auth/device-token": SET_OK},
        "script": "const PROBES = " + json.dumps(RULE_PROBES) + r""";
          h.render();
          await h.click({ type: 'button', text: 'set…' });
          const rule = () => {
            const el = h.find((e) => e.props && e.props['data-testid'] === 'set-token-rule');
            return el ? el.text : null;
          };
          const empty = rule();
          const lines = {};
          const saveDisabled = {};
          for (const v of PROBES) {
            await h.type({ placeholder: 'at least 12 characters' }, v);
            lines[v] = rule();
            saveDisabled[v] = !!h.find({ type: 'button', text: 'Save token' }).props.disabled;
          }
          // Two characters: under the floor, so Save is blocked — but the
          // line still tells the truth about what was typed.
          await h.type({ placeholder: 'at least 12 characters' }, 'Ab');
          return { empty, lines, saveDisabled, shortLine: rule(),
                   shortDisabled: !!h.find({ type: 'button', text: 'Save token' }).props.disabled,
                   calls: h.calls.length };
        """,
    },
    "reopening_set_after_a_chosen_token_is_already_the_one_in_use": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": ADMIN_SETUP_CANON,
        "api": {"GET /api/auth/device-token": {"token": CHOSEN, "header": "X-Device-Token"}},
        "script": r"""
          h.render();
          const card = h.find({ type: 'code' });
          await h.click({ type: 'button', text: 'set…' });
          return { currentToken: card && card.text,
                   intro: h.text().filter((t) => t.includes('At least 12')) };
        """,
    },
    "a_refusal_stops_describing_a_value_that_has_been_typed_over": {
        "files": [COMPONENTS, SETTINGS], "component": "HouseholdTokenCard", "fnProps": ["fire"],
        "setup": ADMIN_SETUP_CANON,
        "api": {"GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"},
                "POST /api/auth/device-token":
                    {"__error": {"status": 400,
                                 "message": "use something other than spaces, hyphens and underscores"}}},
        "script": r"""
          h.render();
          await h.click({ type: 'button', text: 'set…' });
          const errs = () => h.findAll((e) => (e.props.className || '') === 'err').map((e) => e.text);
          await h.type({ placeholder: 'at least 12 characters' }, '- - - - - - -');
          await h.click({ type: 'button', text: 'Save token' });
          const afterSave = errs();
          await h.type({ placeholder: 'at least 12 characters' }, 'a perfectly fine token');
          const whileTyping = errs();
          await h.type({ placeholder: 'at least 12 characters' }, '- - - - - - -');
          const retyped = errs();
          return { afterSave, whileTyping, retyped,
                   value: h.find({ placeholder: 'at least 12 characters' }).props.value };
        """,
    },
    "switcher_asks_before_trusting_a_manual_server": {
        "files": [COMPONENTS], "component": "ServerSwitcher", "fnProps": ["onClose"],
        "setup": SWITCHER_SETUP,
        "script": r"""
          h.render();
          await h.type({ placeholder: '192.168.1.30:6369' }, '10.0.0.42');
          await h.click({ type: 'button', text: 'add' });
          const store = () => JSON.parse(JSON.stringify(h.global('window').__store));
          const prompt = h.find({ type: 'div', text: 'trust this server?' });
          const afterPick = { prompt: !!prompt, host: !!h.find({ type: 'div', text: '10.0.0.42:6369' }),
                              name: h.text().some((t) => t.includes('kitchen-box')), store: store() };
          await h.click({ type: 'button', text: 'cancel' });
          const afterCancel = { prompt: !!h.find({ type: 'div', text: 'trust this server?' }), store: store() };
          await h.click({ type: 'button', text: 'add' });
          await h.click({ type: 'button', text: 'trust this server' });
          return { afterPick, afterCancel, afterConfirm: store() };
        """,
    },
    "switcher_uses_a_trusted_server_without_asking": {
        "files": [COMPONENTS], "component": "ServerSwitcher", "fnProps": ["onClose"],
        "setup": SWITCHER_SETUP + "window.__store.trusted.push('http://10.0.0.42:6369');",
        "script": r"""
          h.render();
          await h.type({ placeholder: '192.168.1.30:6369' }, '10.0.0.42');
          await h.click({ type: 'button', text: 'add' });
          const s = h.global('window').__store;
          return { prompt: !!h.find({ type: 'div', text: 'trust this server?' }), selected: s.selected, trustCalls: s.trustCalls };
        """,
    },
}


@pytest.fixture(scope="module")
def outcomes() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive the Settings panels (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for name, o in out.items():
        assert "__harness_error" not in o, f"{name}: {o.get('__harness_error')}"
    return out


@pytest.fixture(scope="module")
def js_normalize() -> dict:
    """What the SHIPPED web/static/auth.js does to AWKWARD_TOKENS, plus
    what NORMALIZE_STUB does to the same table."""
    node = shutil.which("node")
    assert node, "node is required to run the shipped auth.js (see jsxcheck)"
    payload = json.dumps({"values": AWKWARD_TOKENS, "extra": {"scenario_stub": NORMALIZE_STUB}})
    proc = subprocess.run(
        [node, str(NORMALIZE_HARNESS), str(REPO_ROOT), payload],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# ── 2. the household token card ─────────────────────────────────────────


def test_admin_session_sees_the_household_token_with_copy_and_rotate(outcomes):
    o = outcomes["admin_sees_the_token"]
    assert o["token"] == TOKEN
    assert "/api/auth/device-token" in o["hookCalls"]
    assert o["hasCopy"] and o["hasRotate"] and o["hasSet"]
    assert o["copied"] == TOKEN
    assert any("Settings → Connection → Household token" in t for t in o["texts"])


def test_cookie_session_sees_the_household_token_too(outcomes):
    assert outcomes["cookie_session_sees_the_token"]["token"] == TOKEN


def test_signed_out_viewer_sees_nothing_and_nothing_is_fetched(outcomes):
    o = outcomes["viewer_sees_nothing"]
    assert o["tree"] == 0
    assert o["texts"] == []
    assert o["hookCalls"] == []


def test_devices_tab_signed_out_has_no_token_card_and_never_asks_for_it(outcomes):
    o = outcomes["devices_panel_signed_out"]
    assert "Household token" not in o["cards"]
    assert "/api/auth/device-token" not in o["hookCalls"]
    assert not any(TOKEN in t for t in o["texts"])
    assert "This device" in o["cards"]              # the rest of the tab is intact


def test_rotate_confirms_first_then_posts_and_re_pairs_this_browser(outcomes):
    o = outcomes["rotate_asks_then_posts_and_re_pairs_this_browser"]
    assert o["afterAsk"] == {"dialog": True, "calls": 0}
    assert o["afterCancel"] == {"dialog": False, "calls": 0}
    assert o["calls"] == ["POST /api/auth/device-token/rotate"]
    assert o["paired"] == ROTATED


# ── the set dialog ──────────────────────────────────────────────────────


def test_set_asks_first_and_posts_nothing_until_it_is_saved(outcomes):
    o = outcomes["set_asks_first_and_posts_nothing_until_it_is_saved"]
    assert o["afterOpen"] == {"dialog": True, "calls": 0, "counter": True}
    assert o["afterCancel"] == {"dialog": False, "calls": 0}


def test_the_twelve_character_floor_is_a_hard_block_on_the_save_button(outcomes):
    o = outcomes["eleven_characters_leaves_the_save_button_disabled"]
    assert o["eleven"] == {"disabled": True, "counter": True}
    # Padding does not buy length — the counter and the gate both measure
    # the STORED form, because that is what the server measures.
    assert o["padded"] == {"disabled": True, "counter": True}
    assert o["twelve"] == {"disabled": False, "counter": True}
    assert o["calls"] == 0


def test_saving_posts_the_trimmed_token_and_re_pairs_this_browser(outcomes):
    o = outcomes["saving_posts_the_token_and_re_pairs_this_browser"]
    assert o["calls"] == ["POST /api/auth/device-token"]
    assert o["body"] == {"token": CHOSEN}        # trimmed, nothing else
    # Re-paired from the RESPONSE, exactly as rotate does, so the card does
    # not turn round and claim this browser is unpaired.
    assert o["paired"] == CHOSEN
    assert o["dialog"] is False


def test_a_server_refusal_lands_inline_and_leaves_the_dialog_open(outcomes):
    """Inline next to the field, not as a toast: the server's message is
    the actionable part and the dialog is still standing."""
    o = outcomes["a_server_refusal_lands_inline_next_to_the_field"]
    assert o["dialog"] is True
    assert o["inline"] is True
    assert o["paired"] is None                    # nothing was re-paired
    assert o["calls"] == ["POST /api/auth/device-token"]


# ── 3. the switcher's trust prompt ──────────────────────────────────────


def test_switcher_shows_the_address_and_persists_only_after_the_confirmation(outcomes):
    o = outcomes["switcher_asks_before_trusting_a_manual_server"]
    assert o["afterPick"]["prompt"] is True
    assert o["afterPick"]["host"] is True             # the address, prominently
    assert o["afterPick"]["name"] is True             # and what it calls itself
    assert o["afterPick"]["store"] == {"trusted": [], "saved": [], "selected": None, "trustCalls": 0}
    assert o["afterCancel"]["prompt"] is False
    assert o["afterCancel"]["store"]["selected"] is None
    assert o["afterCancel"]["store"]["saved"] == []
    assert o["afterConfirm"] == {
        "trusted": ["http://10.0.0.42:6369"],
        "saved": [{"url": "http://10.0.0.42:6369", "name": "kitchen-box"}],
        "selected": "http://10.0.0.42:6369",
        "trustCalls": 1,
    }


def test_switcher_does_not_re_ask_for_a_trusted_server(outcomes):
    o = outcomes["switcher_uses_a_trusted_server_without_asking"]
    assert o == {"prompt": False, "selected": "http://10.0.0.42:6369", "trustCalls": 0}


# ── the ADDENDUM: the dialog says which rule will match ─────────────────
#
# A household sets `$$bills_yall-market!!1999`, reads it down the hall, and
# the other person types the underscore as a hyphen. That is refused, and
# correctly so — the token is not its own canonical form, so it is matched
# character for character. What must not happen is the product springing
# that on the person who set it, which is what the two lines below are for.


def test_the_js_canonical_helper_agrees_with_the_python_one(js_normalize) -> None:
    """The dialog's whole claim rests on this. If the browser's
    normalizeDeviceToken and the server's normalize_device_token disagree
    about one value, the dialog tells somebody their token is forgiving
    when it is not (or the reverse), which is worse than saying nothing."""
    from domovoi.admin_auth import normalize_device_token

    disagree = []
    for value in AWKWARD_TOKENS:
        # normalize_device_token returns None for "nothing left"; the JS
        # helper returns the empty string for the same input. Every other
        # character has to match exactly.
        want = normalize_device_token(value) or ""
        got = js_normalize["auth"][value]
        if got != want:
            disagree.append((value, want, got))
    assert disagree == [], disagree
    assert len(AWKWARD_TOKENS) >= 12


def test_the_two_agree_on_the_verdict_the_dialog_actually_renders(js_normalize) -> None:
    """Not the string — the yes/no the line is keyed on."""
    from domovoi.admin_auth import normalize_device_token

    checked = 0
    for value in AWKWARD_TOKENS:
        stored = value.strip()
        if not stored:
            continue  # the dialog renders no line at all for an empty field
        checked += 1
        py = normalize_device_token(stored) == stored
        js = js_normalize["auth"][stored] == stored
        assert py == js, (stored, py, js)
    assert checked >= 12
    # The ADDENDUM's own example, spelled out: not canonical, so exact.
    assert normalize_device_token("$$bills_yall-market!!1999") == "$$bills-yall-market!!1999"
    assert js_normalize["auth"]["$$bills_yall-market!!1999"] == "$$bills-yall-market!!1999"


def test_the_only_input_they_disagree_about_is_one_the_dialog_never_asks_about(
    js_normalize,
) -> None:
    """Python says the empty string has no canonical form (None); JS says
    its canonical form is the empty string, so JS would call it canonical
    and Python would not. Pinned rather than hidden: the dialog guards on
    a non-empty trimmed value before it asks, and the 12-character floor
    blocks Save anyway."""
    from domovoi.admin_auth import normalize_device_token

    assert normalize_device_token("") is None
    assert js_normalize["auth"][""] == ""


def test_the_stub_the_scenarios_use_is_the_shipped_helper(js_normalize) -> None:
    """The JSX harness replaces Auth wholesale, so the scenarios below
    stub normalizeDeviceToken. This is what stops that stub drifting into
    a second, differently-wrong implementation."""
    assert js_normalize["extra"]["scenario_stub"] == js_normalize["auth"]


def test_the_dialog_says_which_rule_will_match_live_as_it_is_typed(outcomes) -> None:
    o = outcomes["the_rule_line_follows_what_is_typed"]
    assert o["empty"] is None, "nothing to say about an empty field"
    assert o["lines"]["$$bills_yall-market!!1999"] == RULE_EXACT
    assert o["lines"]["$$bills-yall-market!!1999"] == RULE_FORGIVING
    assert o["lines"]["frontdoorcats1999"] == RULE_FORGIVING
    assert o["lines"]["My House Is Red"] == RULE_EXACT
    assert o["lines"]["acorn-maple-otter-basin-cedar-ridge-harbor-willow"] == RULE_FORGIVING
    assert o["lines"]["Maple Street, 1984!"] == RULE_EXACT
    assert o["lines"]["0123456789abcdef" * 4] == RULE_FORGIVING
    # Both read the STORED form: the outer padding is trimmed before
    # either of them looks. The inner space is not — it collapses to a
    # hyphen, so "padded token here" is not its own canonical form and is
    # matched exactly, while the same value with no space to collapse is
    # forgiving even when it was typed with padding.
    assert o["lines"]["  padded token here  "] == RULE_EXACT
    assert o["lines"]["  frontdoorcats1999  "] == RULE_FORGIVING


def test_the_rule_line_never_blocks_saving(outcomes) -> None:
    """It describes what the token will do; it is not a complaint about
    it. Kamron's whole point was that any printable ASCII is allowed."""
    o = outcomes["the_rule_line_follows_what_is_typed"]
    assert set(o["saveDisabled"].values()) == {False}
    assert o["calls"] == 0, "typing posts nothing"
    # ...and it keeps telling the truth below the floor, where the only
    # thing blocking Save is the length.
    assert o["shortLine"] == RULE_EXACT
    assert o["shortDisabled"] is True


def test_the_dialog_does_not_call_a_chosen_token_the_generated_phrase(outcomes) -> None:
    """Reopening set… on a household that already runs a chosen token used
    to greet the admin with "Replace the generated phrase" — wrong on the
    one screen that also shows the chosen token, two inches away."""
    o = outcomes["reopening_set_after_a_chosen_token_is_already_the_one_in_use"]
    assert o["currentToken"] == CHOSEN
    assert len(o["intro"]) == 1
    assert "Replace the current household token" in o["intro"][0]
    assert "generated phrase" not in o["intro"][0]


def test_a_refusal_belongs_to_the_value_it_was_raised_for(outcomes) -> None:
    """The error is the card's state and only the next submit replaces it,
    so a red line about `- - - - - - -` used to sit under a field that now
    held something perfectly legal."""
    o = outcomes["a_refusal_stops_describing_a_value_that_has_been_typed_over"]
    assert o["afterSave"] == ["use something other than spaces, hyphens and underscores"]
    assert o["whileTyping"] == []
    assert o["value"] == "- - - - - - -"
    # Type the refused value back and the reason comes back with it.
    assert o["retyped"] == ["use something other than spaces, hyphens and underscores"]
