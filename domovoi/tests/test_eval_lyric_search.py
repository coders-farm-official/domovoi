"""scripts/eval_lyric_search.py (lyrics-build CONTRACT §17): the parts that
need no gate data — the manifest check, the database guard, routing as
production does, the MPD emulation and the ship bars. DB-free; every
phrase here is invented, and nothing is read from a real gate directory.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval_lyric_search.py"


@pytest.fixture(scope="module")
def E():
    spec = importlib.util.spec_from_file_location("eval_lyric_search", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gate(tmp_path: Path, E) -> Path:
    gate = tmp_path / "gate"
    (gate / "raw").mkdir(parents=True)
    for name in E.GATE_FILES:
        (gate / name).write_text(json.dumps({"invented": name}), encoding="utf-8")
    (gate / "raw" / "1.json").write_text("{}", encoding="utf-8")
    lines = [f"{E._sha256(gate / n)}  {n}" for n in E.GATE_FILES]
    lines.append(f"{E.raw_listing_hash(gate)}  {E.RAW_ENTRY}")
    (gate / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return gate


def test_the_manifest_holds_and_any_change_is_not_the_gate(tmp_path, E) -> None:
    gate = _gate(tmp_path, E)
    ok, digest, problems = E.verify_manifest(gate)
    assert ok and digest and problems == []
    (gate / "raw" / "2.json").write_text("{}", encoding="utf-8")
    ok, _, problems = E.verify_manifest(gate)
    assert not ok and problems == ["raw/: listing hash differs"]
    (gate / "raw" / "2.json").unlink()
    (gate / "gate_set.json").write_text("[]", encoding="utf-8")
    ok, _, problems = E.verify_manifest(gate)
    assert not ok and problems == ["gate_set.json: hash differs"]
    assert E.verify_manifest(tmp_path / "nowhere")[0] is False


@pytest.mark.parametrize("url, ok", [
    ("postgresql+asyncpg://domovoi:domovoi@127.0.0.1:6432/domovoi6_test", True),
    ("postgresql+asyncpg://domovoi:domovoi@127.0.0.1:6432/domovoi_test", False),
    ("postgresql+asyncpg://domovoi:domovoi@127.0.0.1:6432/domovoi2_test", False),
    ("postgresql+asyncpg://domovoi:domovoi@127.0.0.1:6432/domovoi", False),
    ("", False),
])
def test_only_a_lane_test_database_is_loaded(monkeypatch, E, url, ok) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    if ok:
        assert E.check_database_url() == "domovoi6_test"
    else:
        with pytest.raises(SystemExit):
            E.check_database_url()


@pytest.mark.parametrize("said, kind, payload", [
    ("Play the song that goes the lantern hums beside the river door.",
     "lyric", ("explicit", "the lantern hums beside the river door")),
    ("who sings the song that goes copper kettles sing at dawn",
     "lyric", ("identify", "copper kettles sing at dawn")),
    ("what song goes we carried paper boats along the hall",
     "lyric", ("identify", "we carried paper boats along the hall")),
    ("play stand by the river door",
     "play", ({"title": "stand", "artist": "the river door"}, "stand by the river door")),
    ("play the velvet kites", "play", ({"any": "the velvet kites"}, None)),
    ("play a random song", "other", None),
    ("what time is it", "other", None),
])
def test_routing_is_productions(E, said, kind, payload) -> None:
    assert E.route(said) == (kind, payload)


def test_the_mpd_emulation_searches_tags_then_file_names_by_id(E) -> None:
    rows = [
        {"id": 3, "title": "Glass Harbor", "artist": "The Example Band", "album": None,
         "file_path": "/m/Example/glass.mp3"},
        {"id": 2, "title": "Lantern Song", "artist": "The Example Band", "album": "Velvet Kites",
         "file_path": "/m/Example/lantern.mp3"},
        {"id": 5, "title": None, "artist": None, "album": None,
         "file_path": "/m/loose/The Example Band - River Door.mp3"},
    ]
    mpd = E.MpdEmulation(rows)
    assert mpd.play({"any": "example band"}) == 2                      # first by id
    assert mpd.play({"any": "velvet"}) == 2                            # the album
    assert mpd.play({"title": "glass", "artist": "example"}) == 3
    assert mpd.play({"any": "river door"}) == 5                        # the file name
    assert mpd.play({"any": "copper kettle"}) is None


def test_the_bars(E) -> None:
    counts = {g: {v: {"n": 100} for v in E.TTS_VOICES} for g in (*E.POSITIVE, *E.NEGATIVE)}
    assert E.bars(counts)["wrong_play_pass"] and E.bars(counts)["false_play_pass"]
    counts["P-EC"]["amy"]["wrong_play"] = 37                            # 37 of 1800 = 2.06 %
    counts["N-IE"]["ryan"]["false_play"] = 18                           # 18 of 1800 = 1.0 %
    b = E.bars(counts)
    assert (b["positive_items"], b["wrong_plays"], b["wrong_play_pass"]) == (1800, 37, False)
    assert (b["negative_items"], b["false_plays"], b["false_play_pass"]) == (1800, 18, True)
    counts["say"] = {}
    assert E.bars(counts)["wrong_plays"] == 37


def test_the_report_is_numbers_only(E) -> None:
    result = {
        "meta": {"is_gate": True, "problems": [], "git": "abc1234", "manifest_sha256": "0" * 64,
                 "pool": {"targets": 1, "absent": 1, "distractors": 1}, "db": "domovoi6_test"},
        "thresholds": {"play": 0.9, "ask": 0.75},
        "latency_ms": {"n": 2, "p50": 40.0, "p95": 60.0, "max": 61.0},
        "counts": {"P-EC": {"lessac": {"n": 2, "right_play": 1, "wrong_play": 1}},
                   "N-AE": {"lessac": {"n": 2, "none": 2}}},
    }
    result["bars"] = E.bars(result["counts"])
    out = E.format_report(result)
    assert "SHIP BARS" in out and "P-EC" in out and "50.0%" in out
    assert "lantern" not in out and "kettle" not in out
