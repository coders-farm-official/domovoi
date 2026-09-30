"""The dashboard chat's timestamps and "details" rows (web/static/chat_details.js).

They are worded exactly like the Android app's (ChatMessageActions.kt and
its ChatMessageActionsTest): the same inputs here give the same strings.
Runs the real file under node in a fixed time zone. No DB.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("chat_details_harness.js")
NOW = "2026-09-30T17:37:00Z"   # wed 30 sep, 12:37 in Chicago

STATS = {
    "prompt_tokens": 1234, "output_tokens": 42, "tokens_per_sec": 35.04,
    "total_ms": 3100.0, "load_ms": 1800.0, "prompt_ms": 300.0, "generate_ms": 1200.0,
    "first_token_ms": 2150.0, "wall_ms": 3400.0, "done_reason": "length",
    "context_sent": 30, "context_in_thread": 44, "context_limit": 30, "num_ctx": 8192,
    "model_role": "vision",
}

CASES = {
    "today": {"stamp": "2026-09-30T17:29:10+00:00", "now": NOW},
    "yesterday": {"stamp": "2026-09-29T14:05:00Z", "now": NOW},
    "this_week": {"stamp": "2026-09-27T02:40:00Z", "now": NOW},
    "this_year": {"stamp": "2026-09-03T13:00:00Z", "now": NOW},
    "older": {"stamp": "2025-12-25T00:00:00Z", "now": NOW},
    "no_time": {"stamp": None, "now": NOW},
    "reply": {"now": NOW, "thread": 2, "rows": {
        "id": 9, "role": "assistant", "content": "Hope this helps!\nBye",
        "created_at": "2026-09-30T17:37:05Z", "model": "llava:7b", "stats": STATS}},
    "warm_reply": {"now": NOW, "thread": 1, "rows": {
        "id": 1, "role": "assistant", "content": "x", "model": "m",
        "stats": {"load_ms": 40, "prompt_ms": 90, "generate_ms": 800, "done_reason": "stop",
                  "context_sent": 3, "context_in_thread": 3}}},
    "user_unsaved": {"now": NOW, "thread": 7, "rows": {
        "id": "u-123", "role": "user", "content": "what is this?", "model": "ignored",
        "error": "500 boom", "device": "Kitchen tablet",
        "images": [{"token": "a", "name": "cat.jpg"}, {"token": "b", "name": ""}]}},
}


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    assert node, "node is required to run chat_details.js (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(CASES)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        env={**os.environ, "TZ": "America/Chicago"},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_stamps_read_by_how_long_ago(out) -> None:
    assert out["today"] == "12:29"
    assert out["yesterday"] == "yesterday 9:05"
    assert out["this_week"] == "sat 21:40"
    assert out["this_year"] == "3 sep 8:00"
    assert out["older"] == "24 dec 2025 18:00"
    assert out["no_time"] is None


def test_a_replys_details(out) -> None:
    rows = dict(out["reply"])
    assert [r[0] for r in out["reply"]] == [
        "from", "sent", "model", "reply time", "time spent", "tokens", "speed",
        "stopped", "context", "length", "message",
    ]
    assert rows["from"] == "domovoi"
    assert rows["sent"] == "Wed 30 Sep 2026, 12:37:05 (just now)"
    assert rows["model"] == "llava:7b (vision model: images attached)"
    assert rows["reply time"] == "first words after 2.2s · finished in 3.4s"
    assert rows["time spent"] == "loading the model 1.8s · reading 0.3s · writing 1.2s"
    assert rows["tokens"] == "1,234 in · 42 out"
    assert rows["speed"] == "35.0 tokens/s"
    assert rows["stopped"] == "cut off at the length limit"
    assert rows["context"] == "saw the last 30 of 44 messages; older ones were left out · 8,192-token window"
    assert rows["length"] == "20 characters · 4 words · 2 lines"
    assert rows["message"] == "#9 in thread #2"


def test_a_warm_model_and_a_short_thread_read_plainly(out) -> None:
    rows = dict(out["warm_reply"])
    assert rows["time spent"] == "reading 0.1s · writing 0.8s"
    assert rows["stopped"] == "finished normally"
    assert rows["context"] == "saw all 3 messages in the thread"
    assert rows["model"] == "m"


def test_a_user_message_not_saved_yet(out) -> None:
    rows = dict(out["user_unsaved"])
    assert rows["from"] == "you"
    assert rows["device"] == "Kitchen tablet"
    assert "model" not in rows and "sent" not in rows and "reply time" not in rows
    assert rows["images"] == "2 · cat.jpg, image"
    assert rows["error"] == "500 boom"
    assert rows["message"] == "thread #7 (not saved yet)"
