"""The spoken-match gate: the spoken-name resolver on the frozen, held-out
audit set (scripts/eval_spoken_match.py) — opt-in, measurement only.

SKIPS unless ``DOMOVOI_SPOKEN_GATE_DIR`` points at the gate data (household
data kept outside the repo; see the script's docstring). A skip is NOT a
pass: it means the gate was not run.

When it runs it prints the table and asserts REGRESSION FLOORS set below
the audit's honest held-out numbers (stylized ≈64 % auto-play / ≈83 % with
confirmations, wrong plays ≤ ~1 %, ordinary ≥ ~74 %, not-in-library false
plays ≤ ~2 %). They catch breakage; they are not targets to tune toward.
Never change a constant, a rule, an alias or a fixture to move these
numbers — the gate measures, it never tunes.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.spoken_gate

GATE_DIR = os.environ.get("DOMOVOI_SPOKEN_GATE_DIR")
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval_spoken_match.py"

#: Floors (percent of transcripts). Below the honest targets on purpose.
EXPECTED_TRANSCRIPTS = 1011
STYLIZED_AUTO_PLAY_MIN = 60.0
STYLIZED_WITH_CONFIRM_MIN = 80.0
STYLIZED_WRONG_MAX = 1.5
ORDINARY_AUTO_PLAY_MIN = 70.0
ABSENT_FALSE_PLAY_MAX = 3.0


def _script():
    spec = importlib.util.spec_from_file_location("eval_spoken_match", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(
    not GATE_DIR, reason="spoken gate NOT RUN (set DOMOVOI_SPOKEN_GATE_DIR)"
)
def test_spoken_match_gate() -> None:
    gate = _script()
    result = gate.run_gate(GATE_DIR, source="core", mode="resolver", ask=True)
    print()
    print(gate.format_table(result))
    meta, groups = result["meta"], result["groups"]
    assert meta["is_gate"], f"NOT THE GATE: {meta['problems']}"
    assert meta["transcripts"] == EXPECTED_TRANSCRIPTS
    stylized, ordinary, absent = groups["stylized"], groups["ordinary"], groups["absent"]
    failures = []
    if stylized["auto_play"] < STYLIZED_AUTO_PLAY_MIN:
        failures.append(f"stylized auto-play {stylized['auto_play']}% < {STYLIZED_AUTO_PLAY_MIN}%")
    if stylized["with_confirm"] < STYLIZED_WITH_CONFIRM_MIN:
        failures.append(
            f"stylized with confirmations {stylized['with_confirm']}% < {STYLIZED_WITH_CONFIRM_MIN}%"
        )
    if stylized["wrong"] > STYLIZED_WRONG_MAX:
        failures.append(f"stylized wrong plays {stylized['wrong']}% > {STYLIZED_WRONG_MAX}%")
    if ordinary["auto_play"] < ORDINARY_AUTO_PLAY_MIN:
        failures.append(f"ordinary auto-play {ordinary['auto_play']}% < {ORDINARY_AUTO_PLAY_MIN}%")
    if absent["false_play"] > ABSENT_FALSE_PLAY_MAX:
        failures.append(f"not-in-library false plays {absent['false_play']}% > {ABSENT_FALSE_PLAY_MAX}%")
    assert not failures, "; ".join(failures)


def test_the_harness_refuses_a_modified_gate(tmp_path) -> None:
    """DB-free and gate-free: a directory whose files do not match its
    manifest is never reported as the gate."""
    gate = _script()
    for name in gate.GATE_FILES:
        (tmp_path / name).write_text("[]\n", encoding="utf-8")
    (tmp_path / "MANIFEST.sha256").write_text(
        "".join(f"{'0' * 64} *{name}\n" for name in gate.GATE_FILES), encoding="utf-8"
    )
    ok, _, problems = gate.verify_manifest(tmp_path)
    assert not ok and len(problems) == 3
    with pytest.raises(SystemExit) as exc:
        gate.run_gate(tmp_path)
    assert exc.value.code == 2


def test_the_harness_scores_a_synthetic_gate(tmp_path) -> None:
    """The scoring plumbing on a tiny made-up gate built from the
    synthetic test library (never the real gate)."""
    import hashlib
    import json

    gate = _script()
    fixture = Path(__file__).parent / "fixtures" / "spoken_match_library.json"
    tracks = json.loads(fixture.read_text(encoding="utf-8"))["tracks"]
    items = [
        {"qid": "s1", "group": "stylized", "cat": "artist-symbol", "say": "Play suicide boys.", "gt_ids": [1, 2, 3, 4], "gt": None},
        {"qid": "s2", "group": "stylized", "cat": "artist-acronym", "say": "Play subtract.", "gt_ids": [28], "gt": None},
        {"qid": "s3", "group": "stylized", "cat": "artist-plain", "say": "Pause.", "gt_ids": [5], "gt": None},
        {"qid": "o1", "group": "ordinary", "cat": "ordinary-title", "say": "Play hollow clock.", "gt_ids": [11], "gt": None},
        {"qid": "a1", "group": "absent", "cat": "absent-popular", "say": "Play quartz meridian.", "gt_ids": [], "gt": None},
    ]
    (tmp_path / "audit_set_v2.json").write_text(json.dumps(items), encoding="utf-8")
    (tmp_path / "audit_tr.jsonl").write_text(
        "".join(json.dumps({"qid": it["qid"], "voice": "en_US-made-up", "core": it["say"]}) + "\n" for it in items),
        encoding="utf-8",
    )
    (tmp_path / "library.json").write_text(json.dumps(tracks), encoding="utf-8")
    (tmp_path / "MANIFEST.sha256").write_text(
        "".join(
            f"{hashlib.sha256((tmp_path / n).read_bytes()).hexdigest()} *{n}\n"
            for n in gate.GATE_FILES
        ),
        encoding="utf-8",
    )
    dump = tmp_path / "decisions.jsonl"
    result = gate.run_gate(tmp_path, dump_decisions=str(dump))
    meta, g = result["meta"], result["groups"]
    assert meta["is_gate"] and meta["transcripts"] == 5 and meta["evaluated"] == 4
    assert g["stylized"]["auto_play"] == pytest.approx(33.3)
    assert g["stylized"]["with_confirm"] == pytest.approx(66.7)
    assert g["stylized"]["not_routed"] == pytest.approx(33.3)
    assert g["ordinary"]["auto_play"] == 100.0
    assert (g["absent"]["false_play"], g["absent"]["nothing"]) == (0.0, 100.0)
    assert g["absent: popular"]["n"] == 1
    assert len(dump.read_text(encoding="utf-8").splitlines()) == 5
    assert "stylized" in gate.format_table(result)
    # Without asking, the middle band is a miss; production mode adds
    # today's search (which has no quartz meridian either).
    no_ask = gate.run_gate(tmp_path, ask=False, mode="production")["groups"]
    assert no_ask["stylized"]["with_confirm"] == pytest.approx(33.3)
    assert no_ask["absent"]["false_play"] == 0.0


def test_the_harness_parses_requests_as_production_does() -> None:
    gate = _script()
    assert gate.parse_request("Play Paris Dawn by $uicideboy$.") == {
        "title": "paris dawn", "artist": "$uicideboy$",
    }
    assert gate.parse_request("Could you please play tech nine?") == {"any": "tech nine"}
    # Not music's "play X": a random pick, another handler, or no fast path.
    assert gate.parse_request("Play something.") is None
    assert gate.parse_request("Pause the music.") is None
    assert gate.parse_request("What time is it?") is None
