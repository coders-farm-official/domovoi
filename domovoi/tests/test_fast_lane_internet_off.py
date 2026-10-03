"""B12: the fast-lane model download refuses under INTERNET_ACCESS=never
(CONTRACT 7.4). With no model on disk the lane reports unavailable, saying
the model isn't downloaded and the internet is turned off; no request is
made. Unset (and always/sometimes) download as before. DB-free.
"""

from __future__ import annotations

import logging
import sys
import types

import pytest

from domovoi import egress, fast_lane


def _no_http(monkeypatch):
    import httpx

    def boom(*a, **k):  # pragma: no cover - the assertion is that it never runs
        raise AssertionError("httpx.stream called under never")

    monkeypatch.setattr(httpx, "stream", boom)


def test_download_refused_under_never_without_a_request(monkeypatch, tmp_path) -> None:
    _no_http(monkeypatch)
    with egress.override_policy("never"):
        with pytest.raises(egress.InternetTurnedOff):
            fast_lane._download("https://github.com/k2-fsa/x.tar.bz2", tmp_path / "a", size=10)
    assert not (tmp_path / "a").exists()


@pytest.mark.parametrize("answer", ["", "always", "sometimes"])
def test_download_runs_otherwise(monkeypatch, tmp_path, answer) -> None:
    import httpx

    seen = []

    class FakeResp:
        def raise_for_status(self):
            return None

        def iter_bytes(self, _n):
            yield b"abc"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_stream(method, url, **kw):
        seen.append(url)
        return FakeResp()

    monkeypatch.setattr(httpx, "stream", fake_stream)
    with egress.override_policy(answer):
        digest = fast_lane._download("https://github.com/k2-fsa/x.tar.bz2", tmp_path / "a", size=10)
    assert seen == ["https://github.com/k2-fsa/x.tar.bz2"]
    assert len(digest) == 64


def test_lane_reports_unavailable_under_never_with_no_model(monkeypatch, tmp_path, caplog) -> None:
    _no_http(monkeypatch)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", types.ModuleType("sherpa_onnx"))
    monkeypatch.setattr(fast_lane, "MODELS_ROOT", tmp_path / "fastlane")
    monkeypatch.setattr(fast_lane, "_GENERATION", 41)
    monkeypatch.setattr(fast_lane, "_STATE", "loading")
    monkeypatch.setattr(fast_lane, "_ENGINE", None)
    caplog.set_level(logging.WARNING, logger="domovoi.fast_lane")
    with egress.override_policy("never"):
        fast_lane._load(41)
    assert fast_lane._STATE == "unavailable"
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "the fast-lane model isn't downloaded" in msg
    assert egress.TURNED_OFF_REASON in msg
    # nothing (not even an empty archive or staging dir) is left behind
    root = tmp_path / "fastlane"
    assert not root.exists() or list(root.iterdir()) == []
