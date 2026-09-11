"""Satellite in-RAM log ring (`satellite/log_buffer.py`).

Pure units — no PortAudio, no DB, no event loop — so they run everywhere
and cannot silently skip. The module is deliberately importable off a Pi
for exactly this reason; `client.py` is not.
"""

from __future__ import annotations

import logging

import pytest

from satellite.log_buffer import (
    MAX_RECORD_BYTES,
    RingLogBuffer,
    RingLogHandler,
    chunk_text,
)


# ─── Budget + eviction ────────────────────────────────────────────────────


def test_holds_everything_under_budget() -> None:
    b = RingLogBuffer(1024)
    b.append("first")
    b.append("second")
    assert b.tail() == "first\nsecond\n"
    assert b.stats()["dropped_lines"] == 0


def test_evicts_oldest_past_budget() -> None:
    # Each record is "line NN" + newline = 8 bytes; 40 bytes holds 5.
    b = RingLogBuffer(40)
    for i in range(20):
        b.append(f"line {i:02d}")
    out = b.tail()
    assert out.endswith("line 19\n")
    assert "line 00" not in out
    assert b.stats()["bytes"] <= 40
    assert b.stats()["dropped_lines"] == 15


def test_never_exceeds_budget_under_churn() -> None:
    b = RingLogBuffer(500)
    for i in range(5000):
        b.append(f"record {i} " + "x" * (i % 97))
        assert b.stats()["bytes"] <= 500


def test_rejects_nonpositive_budget() -> None:
    with pytest.raises(ValueError):
        RingLogBuffer(0)


# ─── tail() ───────────────────────────────────────────────────────────────


def test_tail_returns_whole_lines_only() -> None:
    """A byte-offset slice would open the console view mid-record."""
    b = RingLogBuffer(10_000)
    for i in range(100):
        b.append(f"line {i:03d}")
    out = b.tail(50)
    assert len(out.encode()) <= 50
    for line in out.splitlines():
        assert line.startswith("line ")
        assert len(line) == len("line 000")
    assert out.endswith("line 099\n")


def test_tail_larger_than_contents_returns_all() -> None:
    b = RingLogBuffer(10_000)
    b.append("only")
    assert b.tail(10_000) == "only\n"
    assert b.tail(None) == "only\n"


def test_tail_zero_or_negative_returns_empty() -> None:
    b = RingLogBuffer(1024)
    b.append("something")
    assert b.tail(0) == ""
    assert b.tail(-5) == ""


def test_tail_smaller_than_one_line_returns_empty_not_a_fragment() -> None:
    b = RingLogBuffer(1024)
    b.append("a-very-long-single-record")
    assert b.tail(4) == ""


def test_empty_buffer_tails_empty() -> None:
    b = RingLogBuffer(1024)
    assert b.tail() == ""
    assert b.tail(100) == ""
    assert b.stats()["lines"] == 0


# ─── Pathological records ─────────────────────────────────────────────────


def test_oversized_record_is_truncated_not_dropped() -> None:
    """One multi-MB traceback must not evict the whole buffer."""
    b = RingLogBuffer(1024 * 1024)
    b.append("keep me")
    b.append("y" * (MAX_RECORD_BYTES * 4))
    out = b.tail()
    assert "keep me" in out
    assert "[truncated]" in out
    assert b.stats()["bytes"] < MAX_RECORD_BYTES + 1024


def test_multibyte_text_survives_a_round_trip() -> None:
    b = RingLogBuffer(1024)
    b.append("wake word: привет · émoji 🎙")
    assert "привет" in b.tail()
    assert "🎙" in b.tail()


def test_oversized_multibyte_record_cuts_on_a_character_boundary() -> None:
    """Slicing mid-sequence would decode into replacement characters."""
    b = RingLogBuffer(1024 * 1024)
    b.append("🎙" * MAX_RECORD_BYTES)
    assert "�" not in b.tail()


def test_clear_resets_everything() -> None:
    b = RingLogBuffer(1024)
    for i in range(10):
        b.append(f"line {i}")
    b.clear()
    assert b.tail() == ""
    assert b.stats() == {
        "bytes": 0, "lines": 0, "dropped_lines": 0, "max_bytes": 1024,
    }


# ─── Handler wiring ───────────────────────────────────────────────────────


def test_handler_writes_formatted_records() -> None:
    b = RingLogBuffer(64 * 1024)
    h = RingLogHandler(b)
    h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger = logging.getLogger("test_log_buffer.wiring")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(h)
    try:
        logger.info("barge-in detected")
        logger.warning("output open at %d Hz failed", 16000)
    finally:
        logger.removeHandler(h)
    out = b.tail()
    assert "INFO test_log_buffer.wiring: barge-in detected" in out
    assert "WARNING test_log_buffer.wiring: output open at 16000 Hz failed" in out


def test_handler_survives_a_bad_format_string() -> None:
    """A logging handler that raises takes the whole process down with it."""
    b = RingLogBuffer(1024)
    h = RingLogHandler(b)
    logger = logging.getLogger("test_log_buffer.badfmt")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(h)
    logging.raiseExceptions = False  # emulate production: no stderr noise
    try:
        logger.info("missing arg %s %s", "only-one")  # raises inside format()
    finally:
        logging.raiseExceptions = True
        logger.removeHandler(h)
    # The point is that the call above returned instead of propagating.
    assert b.stats()["lines"] == 0


# ─── chunk_text (the WS transport split) ──────────────────────────────────


def _collect(text, size):
    return list(chunk_text(text, size))


def test_empty_text_still_yields_one_final_chunk() -> None:
    """The core resolves its pending request on `final`; a silent path would
    hang the dashboard until a timeout that explains nothing."""
    out = _collect("", 10)
    assert out == [(0, "", True)]


def test_text_shorter_than_a_chunk_is_one_final_chunk() -> None:
    assert _collect("hello", 10) == [(0, "hello", True)]


def test_exact_multiple_does_not_emit_a_trailing_empty_chunk() -> None:
    """Classic off-by-one: len(text) % size == 0 tempting an extra round."""
    out = _collect("abcdef", 3)
    assert out == [(0, "abc", False), (1, "def", True)]


def test_splits_and_marks_only_the_last_chunk_final() -> None:
    out = _collect("abcdefgh", 3)
    assert [p for _, p, _ in out] == ["abc", "def", "gh"]
    assert [f for _, _, f in out] == [False, False, True]


def test_sequence_numbers_start_at_zero_and_increment() -> None:
    out = _collect("x" * 1000, 7)
    assert [seq for seq, _, _ in out] == list(range(len(out)))


def test_exactly_one_chunk_is_final_and_it_is_last() -> None:
    for size in (1, 2, 3, 7, 100, 10_000):
        out = _collect("y" * 999, size)
        finals = [i for i, (_, _, f) in enumerate(out) if f]
        assert finals == [len(out) - 1], size


def test_pieces_rejoin_to_the_original_exactly() -> None:
    text = "line one\nline two\nemoji 🎙 tail\n"
    for size in (1, 3, 5, 64):
        assert "".join(p for _, p, _ in _collect(text, size)) == text


def test_rejects_a_nonpositive_chunk_size() -> None:
    """A zero size would loop forever emitting empty pieces."""
    with pytest.raises(ValueError):
        _collect("abc", 0)
    with pytest.raises(ValueError):
        _collect("abc", -1)


def test_a_full_ring_chunks_without_loss() -> None:
    """End-to-end shape check at the size the client actually uses."""
    b = RingLogBuffer(256 * 1024)
    for i in range(4000):
        b.append(f"2026-09-10 21:35:57,955 INFO satellite: record {i} " + "x" * 40)
    text = b.tail()
    out = _collect(text, 128 * 1024)
    assert len(out) >= 2
    assert "".join(p for _, p, _ in out) == text
    assert out[-1][2] is True
