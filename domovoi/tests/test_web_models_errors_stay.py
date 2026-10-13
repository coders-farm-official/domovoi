"""Settings → Models: errors stay until dismissed, and a pull of a name
that doesn't exist says what was probably meant (owner report 2026-10-13).

Kamron typed ``qwen3-v1:2b`` (the model is ``qwen3-vl:2b``) into "pull by
name" on his phone: the error showed and vanished before he could read it.
A pull of an unknown name is no HTTP error (Ollama answers it with an error
LINE, so the job fails a second later), and the failed job leaves the job
list 30 s after it ends, taking its error with it.

Now (models.jsx, components.jsx useToast's ``stay``):

* the page watches the pulls it started and says how each one ended; a
  failure is a toast that stays until its close button, or until that same
  action is tried again;
* a name the registry doesn't have gets close matches from what the page
  already knows (the catalog, the installed list): look-alikes folded
  (1/l/i, 0/o), a small edit distance, or the same model with a longer tag.
  A catalog match is one press to install; an installed one is only named
  (pulling it again could be refused the same way), so there is no
  control that would fail like the first;
* delete and switch errors stay too; what went right still fades.

Driven for real (jsx_interact_harness.js): components.jsx and models.jsx
compiled with the dashboard's vendored Babel. No DB.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/models.jsx"]

# data.js can't load beside the harness's scripted data layer (its
# `const apiPost` would shadow it); its error wording, restated as in
# test_web_approval_code_refusal.py.
SETUP = r"""
globalThis.apiErrorText = (e) => {
  const nested = e && e.detail && e.detail.detail;
  return (typeof nested === 'string' && nested) || (e && e.message) || String(e);
};
globalThis.mutationErrorText = (e, verb) => {
  if (e && e.authCancelled) return verb + ' cancelled — not signed in.';
  if (e && e.loginPrompted) return null;
  return verb + ' failed: ' + apiErrorText(e);
};
"""

TYPED = "qwen3-v1:2b"
CATALOG_VL = "qwen3-vl:2b-instruct-q4_K_M"
MISSING = "pull model manifest: file does not exist"


def _model(name: str) -> dict:
    return {"name": name, "size_bytes": 2_000_000_000, "loaded": False, "quant": "Q4_K_M"}


def _cat(name: str) -> dict:
    return {"name": name, "role": "vision", "desc": "d", "est_vram_gb": 2}


def _api(installed: list[str], catalog: list[str], **extra) -> dict:
    return {
        "GET /api/config": {"bot_name": "Domovoi", "internet_access": ""},
        "GET /api/models/installed": {"installed": [_model(n) for n in installed],
                                      "ollama_reachable": True},
        "GET /api/models/catalog": {"ollama": [_cat(n) for n in catalog], "whisper": []},
        "GET /api/models/active": {"roles": []},
        "GET /api/models/hardware": {"gpus": [], "ram": None},
        "GET /api/models/jobs": {"jobs": []},
        "POST /api/models/pull": {"job": {"id": 9, "model": TYPED, "status": "pending"},
                                  "attached": False},
        **extra,
    }


HELPERS = r"""
const box = (el) => el.type === 'input' && String(el.props.placeholder || '').startsWith('Pull');
const stays = () => h.findAll((el) => String(el.props.className || '').includes('toast-stay')
                                      && el.props.role === 'alert')
  .map((t) => ({
    text: h.findAll((el) => el.type === 'div' && h.ancestors(el).includes(t)
                            && String(el.props.className || '') === 'toast-stay-text').map((el) => el.text).join(''),
    detail: h.findAll((el) => el.type === 'div' && h.ancestors(el).includes(t)
                              && String(el.props.className || '') === 'toast-stay-detail').map((el) => el.text).join(''),
    buttons: h.findAll((el) => el.type === 'button' && h.ancestors(el).includes(t))
      .map((b) => b.text || b.props['aria-label']),
    clickable: typeof t.props.onClick === 'function',
  }));
const pulls = () => h.calls.filter((c) => c.method === 'POST' && c.path === '/api/models/pull')
  .map((c) => c.body.model);
const failJob = (error) => {
  h.api['GET /api/models/jobs'] = { jobs: [{ id: 9, model: '%(typed)s', status: 'failed', pct: null, error }] };
};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
h.render(); await h.settle(); h.rerender();
""" % {"typed": TYPED}

# Type the wrong name, submit, let the job fail, let the job list forget
# it, wait out a fading toast, then take the suggestion.
PULL_MISSING = HELPERS + r"""
await h.type(box, '%(typed)s');
await h.submit((el) => el.type === 'form');
const started = { pulls: pulls(), texts: h.text() };
failJob('%(missing)s');
h.rerender(); await h.settle(); h.rerender();
const failed = stays();
h.api['GET /api/models/jobs'] = { jobs: [] };
h.rerender(); await h.settle(); h.rerender();
await wait(3600);   // "pulling …" (2.4 s + 35 ms a character) has faded by now
h.rerender();
const later = { stays: stays(), texts: h.text() };
const offer = h.find((el) => el.type === 'button' && String(el.text).startsWith('install ')
                             && h.inside(el, (a) => String(a.props.className || '').includes('toast-stay')));
if (offer) await h.click((el) => el === offer);
return { started, failed, later, afterOffer: { pulls: pulls(), stays: stays() } };
""" % {"typed": TYPED, "missing": MISSING}

# Same failure, but the only close name is already installed: named, no button.
ONLY_INSTALLED = HELPERS + r"""
await h.type(box, '%(typed)s');
await h.submit((el) => el.type === 'form');
failJob('%(missing)s');
h.rerender(); await h.settle(); h.rerender();
return { stays: stays() };
""" % {"typed": TYPED, "missing": MISSING}

# A failure that says nothing about the name: no "did you mean".
NOT_A_NAME = HELPERS + r"""
await h.type(box, '%(typed)s');
await h.submit((el) => el.type === 'form');
failJob('dial tcp: lookup registry.ollama.ai: no such host');
h.rerender(); await h.settle(); h.rerender();
const first = stays();
h.api['POST /api/models/pull'] = { job: { id: 10, model: '%(typed)s', status: 'pending' }, attached: false };
await h.type(box, '%(typed)s');
await h.submit((el) => el.type === 'form');   // trying again clears it
return { first, retried: stays() };
""" % {"typed": TYPED}

# A delete the server refuses: the error stays past a toast's life, and
# its own button closes it.
DELETE_REFUSED = HELPERS + r"""
await h.click({ type: 'button', title: 'delete from disk' });
await h.click({ type: 'button', text: 'Delete' });
await wait(3600);
h.rerender();
const later = stays();
await h.click((el) => el.type === 'button' && el.props['aria-label'] === 'dismiss');
return { later, closed: stays() };
"""

SUGGEST = r"""
const ps = h.global('window').__pullSuggestions;
const known = %(known)s;
const cases = %(cases)s;
const out = {};
for (const [label, [typed, missing]] of Object.entries(cases)) out[label] = ps(typed, known, new Set(missing));
return out;
"""

KNOWN = [
    {"name": "qwen3-vl:2b", "installed": True},
    {"name": CATALOG_VL, "installed": False},
    {"name": "qwen3-vl:4b-instruct-q4_K_M", "installed": False},
    {"name": "llama3.2:1b", "installed": False},
    {"name": "llama3.2:3b", "installed": True},
    {"name": "mistral:7b", "installed": False},
    {"name": "nomic-embed-text:latest", "installed": False},
]
CASES = {
    "typo": ["qwen3-v1:2b", []],
    "upper": ["QWEN3-V1:2B", []],
    "itself": ["qwen3-vl:2b", []],                 # the name itself is never offered back
    "no_tag": ["lama3.2", []],
    "wrong_tag": ["llama3.2:4b", []],
    "short_tag": ["mistral:7", []],
    "latest": ["nomic-embed-txt", []],
    "prefixed": ["registry.ollama.ai/library/mistra1:7b", []],
    "unrelated": ["something-else:13b", []],
    # refused before here: not offered again
    "missing": ["qwen3-v1:2b", ["qwen3-vl:2b-instruct-q4_k_m"]],
}

SCENARIOS = {
    "pull_missing": {"files": FILES, "component": "ModelsPanel", "setup": SETUP,
                     "api": _api(["qwen3-vl:2b", "llama3.2:3b"], [CATALOG_VL, "llama3.2:1b"]),
                     "script": PULL_MISSING},
    "only_installed": {"files": FILES, "component": "ModelsPanel", "setup": SETUP,
                       "api": _api(["qwen3-vl:2b"], []), "script": ONLY_INSTALLED},
    "not_a_name": {"files": FILES, "component": "ModelsPanel", "setup": SETUP,
                   "api": _api(["qwen3-vl:2b"], [CATALOG_VL]), "script": NOT_A_NAME},
    "delete_refused": {"files": FILES, "component": "ModelsPanel", "setup": SETUP,
                       "api": _api(["qwen2.5:7b"], [], **{
                           "DELETE /api/models/qwen2.5%3A7b": {"__error": {
                               "status": 502, "message": "502 Bad Gateway",
                               "detail": {"detail": "delete failed: model is in use"}}}}),
                       "script": DELETE_REFUSED},
    "suggest": {"files": FILES, "component": "(window.__pullSuggestions = pullSuggestions, ModelsPanel)",
                "setup": SETUP, "api": _api([], []),
                "script": SUGGEST % {"known": json.dumps(KNOWN), "cases": json.dumps(CASES)}},
}


@pytest.fixture(scope="module")
def driven() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


def test_a_pull_of_a_missing_name_says_so_and_offers_the_catalog_match(driven) -> None:
    o = driven["pull_missing"]
    assert o["started"]["pulls"] == [TYPED]
    assert f"pulling {TYPED}…" in o["started"]["texts"]
    (err,) = o["failed"]
    assert err["text"] == f"Install {TYPED} failed: there is no model by that name"
    # The installed look-alike is named first (an exact look-alike), the
    # catalog's longer tag second; only the catalog one is a button.
    assert f"Did you mean qwen3-vl:2b (installed), {CATALOG_VL}?" in err["detail"]
    assert MISSING in err["detail"]
    assert f"install {CATALOG_VL}" in err["buttons"]
    assert "install qwen3-vl:2b" not in err["buttons"]
    assert not err["clickable"], "a tap on the error's text must not close it"


def test_the_error_outlives_the_job_list_and_a_fading_toast(driven) -> None:
    later = driven["pull_missing"]["later"]
    assert [s["text"] for s in later["stays"]] == [f"Install {TYPED} failed: there is no model by that name"]
    assert f"pulling {TYPED}…" not in later["texts"]


def test_one_press_installs_the_suggestion_and_clears_the_error(driven) -> None:
    after = driven["pull_missing"]["afterOffer"]
    assert after["pulls"] == [TYPED, CATALOG_VL]
    assert after["stays"] == []


def test_an_installed_match_is_named_not_offered(driven) -> None:
    (err,) = driven["only_installed"]["stays"]
    assert "Did you mean qwen3-vl:2b? It's already installed." in err["detail"]
    assert [b for b in err["buttons"] if b.startswith("install")] == []


def test_a_failure_that_is_not_about_the_name_suggests_nothing_and_a_retry_clears_it(driven) -> None:
    o = driven["not_a_name"]
    (err,) = o["first"]
    assert err["text"] == f"Install {TYPED} failed: dial tcp: lookup registry.ollama.ai: no such host"
    assert "Did you mean" not in err["detail"]
    assert o["retried"] == []


def test_a_refused_delete_stays_until_closed(driven) -> None:
    o = driven["delete_refused"]
    (err,) = o["later"]
    assert err["text"] == "Delete qwen2.5:7b failed: delete failed: model is in use"
    assert o["closed"] == []


def _names(rows: list[dict]) -> list[str]:
    return [r["name"] for r in rows]


def test_suggestions(driven) -> None:
    s = driven["suggest"]
    assert s["typo"] == [{"name": "qwen3-vl:2b", "installed": True},
                         {"name": CATALOG_VL, "installed": False}]
    assert _names(s["upper"]) == ["qwen3-vl:2b", CATALOG_VL]
    assert _names(s["itself"]) == [CATALOG_VL]
    assert _names(s["no_tag"]) == ["llama3.2:1b", "llama3.2:3b"]
    assert _names(s["wrong_tag"]) == ["llama3.2:1b", "llama3.2:3b"]
    assert _names(s["short_tag"]) == ["mistral:7b"]
    assert _names(s["latest"]) == ["nomic-embed-text:latest"]
    assert _names(s["prefixed"]) == ["mistral:7b"]
    assert s["unrelated"] == []


def test_a_name_refused_here_is_not_suggested_again(driven) -> None:
    assert _names(driven["suggest"]["missing"]) == ["qwen3-vl:2b"]


# ── the delete's reason, in Ollama's words ───────────────────────────
# The page shows the server's detail; for an Ollama refusal that used to
# be httpx's "Server error '500 Internal Server Error' for url '…' For more
# information check: https://developer.mozilla.org/…".

def _refusing(status: int, body):
    import httpx

    async def delete_model(name: str, base_url=None, timeout: float = 30.0) -> None:
        req = httpx.Request("DELETE", "http://ollama/api/delete")
        resp = httpx.Response(status, request=req,
                              **({"json": body} if not isinstance(body, str) else {"text": body}))
        resp.raise_for_status()

    return delete_model


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,said", [
    (404, {"error": "model 'qwen3-v1:2b' not found"}, "Ollama said: model 'qwen3-v1:2b' not found"),
    (500, {"error": {"message": "model is in use"}}, "Ollama said: model is in use"),
    (500, "<html>oops</html>", "Ollama said: HTTP 500"),
])
async def test_a_refused_delete_says_ollamas_reason(monkeypatch, status, body, said) -> None:
    from fastapi import HTTPException

    from web.backend.api import models as api

    monkeypatch.setattr(api.ollama_client, "delete_model", _refusing(status, body))
    with pytest.raises(HTTPException) as exc:
        await api.delete_installed("qwen3-v1:2b")
    assert exc.value.status_code == 502
    assert exc.value.detail == said


@pytest.mark.asyncio
async def test_an_unreachable_ollama_still_says_delete_failed(monkeypatch) -> None:
    from fastapi import HTTPException

    from web.backend.api import models as api

    async def boom(name: str, base_url=None, timeout: float = 30.0) -> None:
        raise ConnectionError("connection refused")

    monkeypatch.setattr(api.ollama_client, "delete_model", boom)
    with pytest.raises(HTTPException) as exc:
        await api.delete_installed("x")
    assert exc.value.detail == "delete failed: connection refused"
