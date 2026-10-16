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
    # Pictures with no vision model to read them, and coded reply errors
    # (2026-10-11). Same words as the server and Android's ChatErrors.kt.
    "vision_text": {"call": "visionMissingText", "args": ["qwen2.5vl:7b"]},
    "vision_text_unset": {"call": "visionMissingText", "args": [""]},
    "accepts": {"call": "ACCEPTS"},
    "missing_false": {"call": "visionMissing", "args": [{"vision_installed": False, "vision_model": "v"}]},
    "missing_true": {"call": "visionMissing", "args": [{"vision_installed": True}]},
    "missing_unknown": {"call": "visionMissing", "args": [{"vision_installed": None}]},
    "missing_unread": {"call": "visionMissing", "args": [None]},
    "err_vision": {"call": "errorView", "args": [{
        "role": "assistant", "error": "whatever the server said", "model": "qwen2.5vl:7b",
        "stats": {"model_role": "vision", "error_code": "vision_model_missing"}}]},
    "err_live": {"call": "errorView", "args": [{
        "role": "assistant", "error": "Domovoi couldn't reach its language model.",
        "error_code": "model_unreachable"}]},
    "err_missing": {"call": "errorView", "args": [{
        "role": "assistant", "error": "The model m isn't installed.", "stats": {"error_code": "model_missing"}}]},
    "err_uncoded": {"call": "errorView", "args": [{"role": "assistant", "error": "Failed to fetch"}]},
    "err_none": {"call": "errorView", "args": [{"role": "assistant", "content": "hi"}]},
    # The speaker marker on a message said into the app's chat mic (V025),
    # read from the row exactly as GET /api/chat/threads/{id}/messages sends it.
    "mark_sure": {"call": "speakerMark", "args": [{
        "role": "user", "source": "voice", "speaker_person_id": 3, "speaker_name": "Ana",
        "speaker_confidence": 0.86, "speaker_confident": True}]},
    "mark_unsure": {"call": "speakerMark", "args": [{
        "role": "user", "source": "voice", "speaker_person_id": 3, "speaker_name": "Ana",
        "speaker_confidence": 0.77, "speaker_confident": False}]},
    "mark_nobody": {"call": "speakerMark", "args": [{
        "role": "user", "source": "voice", "speaker_person_id": None, "speaker_confidence": 0.41}]},
    "mark_forgotten": {"call": "speakerMark", "args": [{
        "role": "user", "source": "voice", "speaker_person_id": 9, "speaker_name": None,
        "speaker_confident": True}]},
    "mark_typed": {"call": "speakerMark", "args": [{"role": "user", "source": "typed", "speaker_person_id": 3}]},
    "mark_old_row": {"call": "speakerMark", "args": [{"role": "user", "content": "from before V025"}]},
    "mark_reply": {"call": "speakerMark", "args": [{"role": "assistant", "source": "voice"}]},
}


def _server_refusal(data: bytes) -> dict:
    """The body the chat upload really answers ``data`` with (named .jpg)."""
    import web.backend.api.chat as chat_api

    return json.loads(chat_api._unsupported(data).body)


_HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 32
_AVIF = b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00mif1avifmiaf" + b"\x00" * 32
_PIXELS = {"detail": "That picture is too large for the chat: 9000 x 6000 is 54 megapixels; at most 40 "
                     "are decoded.", "code": "too_many_pixels", "max_megapixels": 40}
# A picture the chat can't take (2026-10-16): the upload's refusal, worded.
CASES.update({
    "up_heic": {"call": "uploadRefusalText", "args": [415, _server_refusal(_HEIC)]},
    "up_heif": {"call": "uploadRefusalText", "args": [415, {**_server_refusal(_HEIC), "format": "HEIF"}]},
    "up_avif": {"call": "uploadRefusalText", "args": [415, _server_refusal(_AVIF)]},
    "up_unknown": {"call": "uploadRefusalText", "args": [415, _server_refusal(b"%PDF-1.7\n")]},
    "up_pixels": {"call": "uploadRefusalText", "args": [413, _PIXELS]},
    "up_old_415": {"call": "uploadRefusalText", "args": [415, {"detail": "unsupported image type .heic"}]},
    "up_old_413": {"call": "uploadRefusalText", "args": [413, {"detail": "request body too large (limit 1 bytes)"}]},
    "up_no_body": {"call": "uploadRefusalText", "args": [415, None]},
    "up_500": {"call": "uploadRefusalText", "args": [500, None]},
    "up_other_code": {"call": "uploadRefusalText", "args": [409, {"detail": "x", "code": "something_else"}]},
})


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


def test_the_picture_refusal_reads_like_the_server(out) -> None:
    import web.backend.api.chat as chat_api

    assert out["vision_text"] == chat_api.vision_missing_text("qwen2.5vl:7b")
    assert out["vision_text_unset"] == chat_api.vision_missing_text("")
    assert out["accepts"] == (
        "Chat takes text and pictures: PNG, JPEG, GIF, WebP or BMP, up to 4 a message. No other files."
    )


def test_the_accepted_types_are_the_uploads_own(out) -> None:
    import web.backend.api.chat as chat_api

    named = {"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "WebP": ".webp", "BMP": ".bmp"}
    assert all(word in out["accepts"] for word in named)
    assert set(named.values()) | {".jpeg"} == set(chat_api._UPLOAD_EXTS)


def test_only_a_server_that_says_so_stops_the_paperclip(out) -> None:
    assert out["missing_false"] is True
    assert out["missing_true"] is False
    assert out["missing_unknown"] is False
    assert out["missing_unread"] is False


def test_coded_errors_read_plainly(out) -> None:
    import web.backend.api.chat as chat_api

    assert out["err_vision"] == {
        "text": chat_api.vision_missing_text("qwen2.5vl:7b"),
        "code": "vision_model_missing", "models": True,
    }
    assert out["err_live"] == {
        "text": "Domovoi couldn't reach its language model.", "code": "model_unreachable", "models": False,
    }
    assert out["err_missing"]["models"] is True
    assert out["err_uncoded"] == {"text": "Failed to fetch", "code": None, "models": False}
    assert out["err_none"] is None


def test_the_speaker_marker_reads_like_the_apps(out) -> None:
    """Same words as SpeakerMarker.kt: the name for a clear match, "maybe"
    and softer for a weak one, "voice not recognized" for nobody."""
    assert out["mark_sure"] == {"label": "Ana", "name": "Ana", "soft": False, "description": "spoken by Ana"}
    assert out["mark_unsure"]["label"] == "maybe Ana" and out["mark_unsure"]["soft"] is True
    assert out["mark_nobody"] == {
        "label": "voice not recognized", "name": None, "soft": True,
        "description": "spoken; Domovoi didn't recognize the voice",
    }
    assert out["mark_forgotten"]["label"] == "someone it knows"
    for typed in ("mark_typed", "mark_old_row", "mark_reply"):
        assert out[typed] is None


def test_a_heic_photo_says_what_to_do_instead(out) -> None:
    """A browser can't convert a phone's HEIC photo as the app does: the
    refusal says to send it from the app or save it as a JPEG."""
    assert out["up_heic"] == (
        "This photo is in HEIC format, which the chat can't read. "
        "Send it from the Domovoi app (it converts photos), or save it as a JPEG and try again."
    )
    assert out["up_heif"].startswith("This photo is in HEIF format")


def test_other_refusals_are_the_servers_own_words(out) -> None:
    assert out["up_avif"] == ("That picture is in AVIF format, which the chat can't read. "
                              "Send a JPEG, PNG, GIF, WebP or BMP.")
    assert out["up_unknown"] == ("That file isn't a picture the chat can read. "
                                 "Send a JPEG, PNG, GIF, WebP or BMP.")
    assert out["up_pixels"] == _PIXELS["detail"]


def test_an_older_servers_refusal_still_reads_plainly(out) -> None:
    """Worded as the app's ChatPhotoPlan.kt uploadRefusalText (its unit test
    holds the same strings)."""
    assert out["up_old_415"] == out["up_no_body"] == (
        "That file isn't a picture the chat can read. Send a JPEG, PNG, GIF, WebP or BMP."
    )
    assert out["up_old_413"] == "That picture is too large for the chat."
    assert out["up_500"] is None and out["up_other_code"] is None
