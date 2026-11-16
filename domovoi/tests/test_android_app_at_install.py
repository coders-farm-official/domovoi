"""The installer puts the Android app on the server (owner decision
2026-11-15): with the other install-time downloads, whatever the internet
answer, never fatal.

It is the ``phone-app`` step of scripts/linux/provision.sh (which the
Windows installer runs inside its WSL distro too). The step runs two Python
snippets as the service user; this file runs those very snippets, read out
of the script, against the signed fixtures and the fake GitHub of
test_android_app.py (no network), so the glue between the bash step and
domovoi/android_app.py is tested as it ships. The step's bash side (the
skip when the app is here, the warning and the finish step's repeat, the
dry-run listing, the final line's ``phone_app``) is the hermetic harness's
(scripts/linux/tests/test-provision.sh, ``phone_app_*`` and the full and
dry runs).

The core's own first-start download that b41f4f26 added is gone: the
refresh after an update is as it was (test_android_app_after_update.py),
and nothing here or in domovoi/main.py hands it a "setup" start. The
dashboard's reminder, shown only while the app isn't on the server, is
held by test_web_spare_device_enrol.py and test_web_settings_health.py.
"""

from __future__ import annotations

import ast
import inspect
import logging
import re
from pathlib import Path

import pytest

from domovoi import android_app as aa
from domovoi import egress
from domovoi.tests.test_android_app import (  # noqa: F401 — fixtures
    GOOD,
    VERSION,
    cache,
    github,
    load,
    manifest_doc,
)

REPO = Path(__file__).resolve().parents[2]
PROVISION = REPO / "scripts" / "linux" / "provision.sh"
MISSING_MSG = ("The phone app couldn't be downloaded: this server needs the internet once, at some point, "
               "to put it here (or upload it in Settings)")


def _script() -> str:
    return PROVISION.read_text(encoding="utf-8")


def _snippet(var: str) -> str:
    """The Python a provision.sh variable holds (VAR='#provision:...')."""
    m = re.search(rf"^{var}='(#provision:[^']*)'$", _script(), re.MULTILINE)
    assert m, f"{var} not found in provision.sh"
    return m.group(1)


def _bash_function(name: str) -> str:
    s = _script()
    start = s.index(f"\n{name}() {{\n") + 1
    return s[start:s.index("\n}\n", start) + 2]


def _run(var: str, manifest: dict, monkeypatch, capsys) -> tuple[int, list[str]]:
    """Run one snippet the way the step does (``python -c``), with
    android/release.json read as ``manifest``. (exit code, stdout lines)"""
    monkeypatch.setattr(aa, "load_manifest", lambda path=None: manifest)
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: None)
    capsys.readouterr()
    code = 0
    try:
        exec(compile(_snippet(var), f"<provision.sh {var}>", "exec"), {"__name__": "__main__"})  # noqa: S102 — the script's own snippet
    except SystemExit as e:
        code = int(e.code or 0)
    return code, capsys.readouterr().out.splitlines()


# ─── the step in the script ───────────────────────────────────────────────

def test_the_step_is_one_of_the_install_downloads():
    s = _script()
    ids = re.search(r"^STEP_IDS=\(([^)]*)\)", s, re.MULTILINE).group(1).split()
    assert ids[ids.index("piper") + 1] == "phone-app", "fetched ahead with the voices"
    assert ids.index("phone-app") < ids.index("start")
    assert '[phone-app]="Downloading the phone app"' in s
    weights = dict(re.findall(r"\[([a-z-]+)\]=([0-9]+)", re.search(r"STEP_WEIGHT=\(([^)]*)\)", s, re.DOTALL).group(1)))
    assert set(weights) == set(ids) and sum(int(w) for w in weights.values()) == 100
    assert "phone-app" in re.search(r"^after_clone\(\) \{[^\n]*", s, re.MULTILINE).group(0)
    # The header's list of what it does names it, and the final line's field.
    assert "#   phone-app    the Android app phones install from this server" in s
    assert '"phone_app":%s' in s


def test_the_download_holds_the_gate_open_like_the_voices():
    src = _snippet("PY_PHONE_APP_DOWNLOAD")
    assert 'with egress.override_policy("always"):' in src and "aa.fetch(m)" in src
    assert src.splitlines()[0] == "#provision:phone-app-download"


def test_the_step_is_never_fatal_and_says_why_in_plain_words():
    s = _script()
    assert f'PHONE_APP_MISSING_MSG="{MISSING_MSG}"' in s
    body = _bash_function("do_phone_app")
    assert "die " not in body and "exit " not in body, "a failed download never stops the install"
    assert 'warn "$PHONE_APP_MISSING_MSG' in body and "STEP_DONE_MSG=$PHONE_APP_MISSING_MSG" in body
    # The end of the install says it again.
    assert 'no) log "$PHONE_APP_MISSING_MSG"' in _bash_function("do_finish")


def test_a_present_app_is_skipped_and_a_missing_one_tried_again():
    check = _bash_function("check_phone_app")
    assert "here) PHONE_APP=yes; return 0" in check and "none) return 0" in check
    assert check.rstrip().endswith("return 1\n}") or "return 1\n}" in check


# ─── the snippets themselves, against a fake GitHub ───────────────────────

def test_info_says_missing_then_the_download_puts_it_here(tmp_path, cache, github, monkeypatch, capsys):
    m = load(tmp_path, manifest_doc(GOOD))
    code, out = _run("PY_PHONE_APP_INFO", m, monkeypatch, capsys)
    assert code == 0 and out[0] == "missing" and out[1] == VERSION
    assert int(out[2]) == sum(len(b) for b in GOOD.values())
    assert Path(out[3]) == cache, "the core's own cache folder"

    code, out = _run("PY_PHONE_APP_DOWNLOAD", m, monkeypatch, capsys)
    assert code == 0 and out[-1] == "2 app files verified (2 downloaded)"
    assert all(re.search(r"github\.com|githubusercontent\.com", s) for s in github.seen)

    code, out = _run("PY_PHONE_APP_INFO", m, monkeypatch, capsys)
    assert out[0] == "here"
    st = aa.status(m)
    assert st["ready"] is True and st["current_ready"] is True and st["missing"] == []


@pytest.mark.parametrize("answer", ["never", "sometimes", ""])
def test_the_internet_answer_does_not_matter_at_install(tmp_path, cache, github, monkeypatch, capsys, answer):
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy(answer or None):
        code, out = _run("PY_PHONE_APP_DOWNLOAD", m, monkeypatch, capsys)
        assert code == 0, out
        # The dashboard's own download still follows the answer.
        if answer == "never":
            assert aa.fetch(m)[0] is False or aa.status(m)["ready"]
    assert aa.status(m)["ready"] is True


def test_no_internet_fails_the_snippet_not_the_install(tmp_path, cache, github, monkeypatch, capsys):
    m = load(tmp_path, manifest_doc(GOOD))
    github.files = {}   # the line is down: nothing comes back
    code, out = _run("PY_PHONE_APP_DOWNLOAD", m, monkeypatch, capsys)
    assert code == 1 and out[-1].startswith("2 of 2 app files failed")
    code, out = _run("PY_PHONE_APP_INFO", m, monkeypatch, capsys)
    assert out[0] == "missing", "nothing partial kept"
    assert list(cache.glob("*.apk")) == []


def test_a_file_the_release_key_did_not_sign_is_not_kept(tmp_path, cache, github, monkeypatch, capsys):
    from domovoi.tests.test_android_app import fixture

    other = {"arm64-v8a": fixture("other-arm64-v8a.apk"), "armeabi-v7a": GOOD["armeabi-v7a"]}
    m = load(tmp_path, manifest_doc(other))
    github.files = {aa.file_name(VERSION, abi): body for abi, body in other.items()}
    code, out = _run("PY_PHONE_APP_DOWNLOAD", m, monkeypatch, capsys)
    assert code == 1 and "1 of 2 app files failed" in out[-1]
    assert _run("PY_PHONE_APP_INFO", m, monkeypatch, capsys)[1][0] == "missing"


def test_no_release_yet_is_nothing_to_fetch(tmp_path, cache, monkeypatch, capsys):
    m = load(tmp_path, {"manifest_version": 1, "release": None})
    code, out = _run("PY_PHONE_APP_INFO", m, monkeypatch, capsys)
    assert code == 0 and out == ["none"]


# ─── the core's first-start download is gone (b41f4f26 reverted) ──────────

def test_the_core_never_makes_the_first_download_by_itself():
    for gone in ("SETUP_DELAYS", "SETUP_ANSWER_POLL", "WAITING_FOR_ANSWER"):
        assert not hasattr(aa, gone), gone
    for fn in (aa.auto_refresh, aa.auto_fetch_decision, aa.schedule_auto_refresh):
        assert "setup" not in inspect.signature(fn).parameters, fn.__name__
    assert set(aa._auto) == {"state", "reason", "tries", "next_at"}
    assert aa.AUTO_DELAYS[0] >= 60, "never in the middle of the core's own start"


def test_main_starts_only_the_refresh_after_an_update():
    src = (REPO / "domovoi" / "main.py").read_text(encoding="utf-8")
    assert "first_run_setup" not in src
    calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", None) == "schedule_auto_refresh"]
    assert len(calls) == 1
    assert [k.arg for k in calls[0].keywords] == ["frozen"] and not calls[0].args
    assert "await admin_auth_mod.ensure_setup_code_if_unclaimed()" in src


def test_the_refresh_on_a_server_without_the_app_downloads_nothing(tmp_path, cache, github):
    import asyncio

    async def no_wait(_):
        return None

    aa._job.update(running=False, ok=None, message=None, bytes_done=0, bytes_todo=0)
    m = load(tmp_path, manifest_doc(GOOD))
    try:
        with egress.override_policy("always"):
            why = asyncio.run(aa.auto_refresh(lambda: m, delays=(0.0,), sleep=no_wait))
    finally:
        aa._auto.update(state="idle", reason=None, tries=0, next_at=None)
    assert github.seen == [] and "isn't on this server yet" in why and "internet once" in why
