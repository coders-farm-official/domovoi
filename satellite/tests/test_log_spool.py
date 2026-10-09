"""The offline log spool: a bounded ring file, send-sized chunks, and the
handler that routes lines by connection state. Off a Pi, in tmp_path."""

from __future__ import annotations

import logging

import pytest

from satellite import log_spool
from satellite.log_spool import LogSpool, PushBuffer, SpoolHandler, chunk_lines


def test_append_then_drain_reproduces_the_lines(tmp_path):
    spool = LogSpool(tmp_path / "log-spool.txt", max_bytes=1024)
    assert spool.append("first") and spool.append("second\n")
    assert spool.size() == len("first\nsecond\n")
    assert spool.drain() == ["first\nsecond\n"]
    assert spool.size() == 0 and not (tmp_path / "log-spool.txt").exists()
    assert spool.drain() == []


def test_the_spool_truncates_from_the_front_at_its_budget(tmp_path):
    spool = LogSpool(tmp_path / "s.txt", max_bytes=1000, max_per_sec=10_000)
    for i in range(200):
        spool.append(f"line {i:04d} " + "x" * 20)      # 31 bytes a line
    assert spool.size() <= 1000
    text = spool.read()
    assert text.endswith("line 0199 " + "x" * 20 + "\n")
    assert "line 0000" not in text
    assert text.startswith("line "), "trimmed on a line boundary"
    assert spool.stats()["trims"] >= 1


def test_a_trim_keeps_three_quarters_so_it_does_not_rewrite_every_line(tmp_path):
    spool = LogSpool(tmp_path / "s.txt", max_bytes=1000, max_per_sec=10_000)
    for i in range(40):
        spool.append("y" * 29)      # 30 bytes a line; crosses 1000 at line 34
    # One trim (to 750 bytes = 25 lines), then six more lines on top of it.
    assert spool.stats()["trims"] == 1
    assert spool.size() == 750 + 6 * 30


def test_drain_chunks_are_bounded_and_cut_on_lines(tmp_path):
    spool = LogSpool(tmp_path / "s.txt", max_bytes=100_000, max_per_sec=10_000)
    for i in range(100):
        spool.append(f"{i:03d} " + "z" * 95)         # 100 bytes a line
    chunks = spool.drain(chunk_bytes=1024)
    assert len(chunks) == 10
    for c in chunks:
        assert len(c.encode("utf-8")) <= 1024
        assert c.endswith("\n")
    assert "".join(chunks).count("\n") == 100


def test_chunk_lines_handles_one_oversized_line_and_utf8():
    text = "short\n" + "é" * 700 + "\nlast\n"      # é is 2 bytes
    pieces = chunk_lines(text, 1000)
    assert "".join(pieces) == text
    for p in pieces:
        assert len(p.encode("utf-8")) <= 1000
    assert pieces[0] == "short\n"
    assert chunk_lines("", 100) == []
    with pytest.raises(ValueError):
        chunk_lines("x", 0)


def test_a_storm_is_counted_not_written(tmp_path):
    assert log_spool.MAX_APPENDS_PER_SEC == 50
    spool = LogSpool(tmp_path / "s.txt", max_bytes=10_000, max_per_sec=5)
    accepted = sum(1 for _ in range(20) if spool.append("burst"))
    assert accepted == 5
    assert spool.stats()["dropped_lines"] == 15


def test_push_buffer_is_bounded_per_push():
    push = PushBuffer(max_bytes=64)
    assert push.append("a" * 30)
    assert push.append("b" * 30)
    assert not push.append("c" * 30), "a third line would exceed one push"
    text, dropped = push.take()
    assert text == "a" * 30 + "\n" + "b" * 30 + "\n" and dropped == 1
    assert push.take() == ("", 0)


def test_handler_routes_by_mode(tmp_path):
    spool = LogSpool(tmp_path / "s.txt")
    handler = SpoolHandler(spool)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("satellite.tests.spool")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        logger.info("while off")
        handler.set_mode("offline")
        logger.info("while offline")
        handler.set_mode("online")
        logger.warning("while online")
    finally:
        logger.removeHandler(handler)
    assert spool.read() == "INFO while offline\n"
    assert handler.push.take()[0] == "WARNING while online\n"
    with pytest.raises(ValueError):
        handler.set_mode("sideways")


def test_handler_never_raises(tmp_path):
    class Broken(LogSpool):
        def append(self, line):
            raise RuntimeError("disk on fire")

    handler = SpoolHandler(Broken(tmp_path / "s.txt"))
    handler.set_mode("offline")
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", (), None)
    handler.handle(record)      # handleError swallows it
