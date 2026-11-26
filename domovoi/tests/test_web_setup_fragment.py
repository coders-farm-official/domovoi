"""The setup-code QR's address in the dashboard (1.5.0):
web/static/setup_fragment.js and the claim forms in components.jsx.

The QR opens ``http://<server>:6369/#setup-code=<code>``. What these pin:

* the fragment is read and the claim form's field starts with the code
  (the first-run welcome, past an earlier "Not now", and the admin setup
  dialog);
* the fragment is cleared from the address at once (history.replaceState,
  path and query kept), whatever it held; any other fragment (a page
  route) is left alone;
* the code is posted exactly as a typed one (POST /api/auth/setup, in the
  body) and never appears in a request path or query;
* index.html loads the script first, before anything else reads the
  address.

Driven through domovoi/tests/jsx_interact_harness.js on top of the REAL
auth.js, data.js and components.jsx, like test_web_first_run_welcome.py.
No DB; needs ``node``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi.tests.test_web_first_run_welcome import COMPONENT, DISMISS_KEY, PASSWORD, SNAP, table
from domovoi.tests.test_web_home_page import NOW, PRELUDE

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/setup_fragment.js", "web/static/auth.js", "web/static/data.js",
         "web/static/components.jsx"]

# Not a real code: a sentinel in the shape of one.
CODE = "apple-brick-cloud-drum-eagle-flute-grape-house"

# The address the page was opened at, and a history that records what the
# script asks of it (and updates the address the way a browser does).
_ADDRESS = r"""
window.location = { hash: __HASH, pathname: '/', search: __SEARCH, href: 'http://test/' + __SEARCH + __HASH };
window.__replaced = [];
window.history = { state: null, replaceState: (s, t, url) => {
  window.__replaced.push(String(url));
  const i = String(url).indexOf('#');
  window.location.hash = i < 0 ? '' : String(url).slice(i);
} };
"""

EXTRA_SNAP = r"""
const field = () => h.find((e) => e.type === 'input' && e.props.placeholder === 'eight-words-separated-by-dashes');
const full = () => ({ ...snap(), field: field() ? field().props.value : null,
  hash: w.location.hash, replaced: w.__replaced,
  urls: w.__fetches.map((f) => String(f.path)) });
const finish = async () => {
  const empty = (e) => e.type === 'input' && e.props.type === 'password' && e.props.value === '';
  await h.type(empty, __PASSWORD);
  await h.type(empty, __PASSWORD);
  await h.click({ type: 'button', text: 'set password' });
  await w.__flush(h, 10);
};
"""


def scenario(hash_: str, script: str, *, search: str = "", ls: dict | None = None, tbl: dict | None = None) -> dict:
    head = (f"const __NOW0 = {NOW}; const __TABLE = {json.dumps(tbl or table())};"
            f" const __LS = {json.dumps(ls or {})}; const __PHONE = null;"
            " const __MANIFEST = null;\n"
            f"const __HASH = {json.dumps(hash_)}; const __SEARCH = {json.dumps(search)};\n")
    return {"files": FILES, "component": COMPONENT, "props": {},
            "setup": head + PRELUDE + _ADDRESS,
            "script": (f"const __PASSWORD = {json.dumps(PASSWORD)};\n"
                       "const w = h.global('window'); h.render(); await w.__flush(h);\n"
                       + SNAP + EXTRA_SNAP + script)}


SCENARIOS: dict[str, dict] = {
    "welcome": scenario(f"#setup-code={CODE}", "const before = full(); await finish();"
                        " return { before, after: full() };", search="?installed=abc"),
    "welcome_dismissed": scenario(f"#setup-code={CODE}", "return full();", ls={DISMISS_KEY: "1"}),
    "escaped": scenario("#setup-code=apple%20brick", "return full();"),
    "unreadable": scenario("#setup-code=%E0%A4%A", "return full();"),
    "too_long": scenario("#setup-code=" + "a" * 201, "return full();"),
    "none": scenario("", "return full();"),
    "route": scenario("#music", "return full();"),
    "dialog": scenario(f"#setup-code={CODE}",
                       "await h.click({ type: 'button', text: 'Not now' }); await w.__flush(h);"
                       "w.__Auth.openModal(); await w.__flush(h);"
                       "const before = full(); await finish(); return { before, after: full() };"),
    "wrong_code": scenario(
        "#setup-code=not-the-code", "await finish(); return full();",
        tbl=table(**{"POST /api/auth/setup": {"__status": 403, "__body": {"detail": "setup code required"}}})),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("setup-fragment") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), "@" + str(spec)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
        env={**os.environ, "TZ": "UTC"},
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def _never_in_an_address(s: dict, secret: str) -> None:
    assert s["urls"], "the page made no requests at all"
    for u in s["urls"]:
        assert secret not in u, u


def test_the_fragment_prefills_the_welcome_and_is_cleared(driven) -> None:
    before = driven["welcome"]["before"]
    assert before["dialog"] == "Welcome to Domovoi"
    assert before["field"] == CODE
    # Out of the address at once; the path and the query stay.
    assert before["hash"] == ""
    assert before["replaced"] == ["/?installed=abc"]


def test_the_code_is_posted_as_a_typed_one_and_never_in_an_address(driven) -> None:
    after = driven["welcome"]["after"]
    assert after["dialog"] == "You're all set"
    setups = [b for b in after["bodies"] if b["path"] == "/api/auth/setup"]
    assert len(setups) == 1
    body = setups[0]["body"]
    body = json.loads(body) if isinstance(body, str) else body
    assert body == {"setup_code": CODE, "password": PASSWORD}
    _never_in_an_address(after, CODE)
    _never_in_an_address(driven["welcome"]["before"], CODE)


def test_a_scan_opens_the_welcome_past_not_now(driven) -> None:
    s = driven["welcome_dismissed"]
    assert s["dialog"] == "Welcome to Domovoi"
    assert s["field"] == CODE
    assert s["hash"] == ""


def test_the_admin_setup_dialog_is_prefilled_too(driven) -> None:
    out = driven["dialog"]
    assert "first-run admin setup" in out["before"]["text"]
    assert out["before"]["field"] == CODE
    assert out["after"]["dialog"] == "You're all set"
    _never_in_an_address(out["after"], CODE)


def test_an_escaped_code_is_decoded(driven) -> None:
    assert driven["escaped"]["field"] == "apple brick"


@pytest.mark.parametrize("name", ["unreadable", "too_long"])
def test_a_bad_fragment_is_cleared_and_fills_nothing(driven, name) -> None:
    s = driven[name]
    assert s["hash"] == "" and s["replaced"] == ["/"]
    assert s["field"] == ""


@pytest.mark.parametrize("name,hash_", [("none", ""), ("route", "#music")])
def test_any_other_address_is_left_alone(driven, name, hash_) -> None:
    s = driven[name]
    assert s["replaced"] == []
    assert s["hash"] == hash_
    assert s["field"] in ("", None)


def test_a_refused_code_is_not_repeated(driven) -> None:
    s = driven["wrong_code"]
    assert s["dialog"] == "Welcome to Domovoi"
    assert "setup code required" in s["text"]
    _never_in_an_address(s, "not-the-code")


def test_index_loads_it_first() -> None:
    html = (REPO_ROOT / "web" / "static" / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script\b[^>]*>", html)
    assert scripts[0] == '<script src="/setup_fragment.js"></script>'.replace("</script>", "")
    # Plain script, synchronous: not deferred, not async, not Babel.
    assert "defer" not in scripts[0] and "async" not in scripts[0] and "babel" not in scripts[0]
    sw = (REPO_ROOT / "web" / "static" / "sw.js").read_text(encoding="utf-8")
    assert "'/setup_fragment.js'" in sw


def test_the_script_keeps_it_in_memory_only() -> None:
    src = (REPO_ROOT / "web" / "static" / "setup_fragment.js").read_text(encoding="utf-8")
    code_lines = [ln for ln in src.splitlines() if not ln.lstrip().startswith(("/*", "*", "//"))]
    body = "\n".join(code_lines)
    for banned in ("localStorage", "sessionStorage", "document.cookie", "fetch(", "XMLHttpRequest",
                   "console.", "sendBeacon", "indexedDB"):
        assert banned not in body, banned
    assert "replaceState" in body
