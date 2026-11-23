"""Pull the logs of one XVF3800 noisy-room trial and compare the settings.

Read-only everywhere: on the satellite it runs ``journalctl``; on the server
one SELECT in a read-only session. Nothing is written but the files in
``--out``.

    # 1. pull (any machine with ssh + psql that reaches both)
    python xvf_trial_report.py pull --sat alex@192.168.1.41 --room living_room \\
        --since "2026-11-29 14:00" --until "2026-11-29 15:10" --out trial/ \\
        --dsn "postgresql://domovoi:...@192.168.1.20:6432/domovoi"
    # 2. the table
    python xvf_trial_report.py report --out trial/ --commands commands.txt

Where each number comes from:

* Satellite journal (``journalctl -u domovoi-satellite``, satellite/client.py):
  ``wake word ... detected (peak score ...)``: a wake hit;
  ``wake score peaked ... under the threshold`` / ``... in a row needed``: a
  near miss; ``capture ended (<reason>): frames=N (X s), ... dBFS
  median=.. p95=..``: how the capture ended and how long it ran;
  ``capture: hit max_record_seconds``: ran to the 30 s cap; and the
  ``xvf-trial: setting=X`` / ``mark=1m`` lines xvf_dsp.sh leaves.
* Server, ``conversation_log`` (V001, V011, V043, V044): per turn the
  transcript (``user_text``), ``turn_status``, ``no_answer_reason``
  (``overheard`` = dropped as background talk, domovoi/overheard.py),
  ``audio_ms`` and ``wake_score``.
* Wake misses are counted against the plan: each block is 8 commands said
  twice (16 tries); detections that match no planned command are counted as
  false wakes instead.
* The best-mic metric needs two satellites; with one, the capture's p95 over
  its median (from the journal line) stands in: how far the command stands
  out of the room.
"""

from __future__ import annotations

import argparse
import csv
import re
import shlex
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

SQL = """COPY (SELECT at, room_id, user_text, turn_status, no_answer_reason, audio_ms, wake_score
FROM conversation_log WHERE room_id = {room} AND at BETWEEN {since} AND {until} ORDER BY at)
TO STDOUT WITH CSV HEADER"""


def q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def pull(a) -> None:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    jcmd = (f"journalctl -o short-iso --no-pager --since {shlex.quote(a.since)} --until {shlex.quote(a.until)} "
            "_SYSTEMD_UNIT=domovoi-satellite.service + SYSLOG_IDENTIFIER=xvf-trial")
    log = subprocess.run(["ssh", "-o", "BatchMode=yes", a.sat, jcmd], capture_output=True, text=True, check=True)
    (out / "satellite.log").write_text(log.stdout, encoding="utf-8")
    sql = SQL.format(room=q(a.room), since=q(a.since + a.tz), until=q(a.until + a.tz))
    psql = shlex.split(a.psql) + [a.dsn, "-v", "ON_ERROR_STOP=1", "-X", "-q",
                                  "-c", "SET default_transaction_read_only = on", "-c", "BEGIN READ ONLY",
                                  "-c", sql, "-c", "ROLLBACK"]
    rows = subprocess.run(psql, capture_output=True, text=True, check=True)
    (out / "turns.csv").write_text(rows.stdout, encoding="utf-8")
    print(f"{out / 'satellite.log'}: {log.stdout.count(chr(10))} lines; {out / 'turns.csv'}: "
          f"{max(0, rows.stdout.count(chr(10)) - 1)} turns")


# ── report ──────────────────────────────────────────────────────────────────

TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)")
RE_WAKE = re.compile(r"wake word.* detected \(peak score ([\d.]+)")
RE_NEAR = re.compile(r"wake.* score (peaked|[\d.]+ for).*not a wake")
RE_END = re.compile(r"capture ended \((\w+)\): frames=(\d+) \(([\d.]+)s\).*?median=(-?[\d.]+) p95=(-?[\d.]+)")
RE_CAP = re.compile(r"hit max_record_seconds.*frames=(\d+) \(([\d.]+)s\).*?median=(-?[\d.]+) p95=(-?[\d.]+)")
RE_MARK = re.compile(r"xvf-trial.*: (setting|mark)=(\S+)")


def words(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]", " ", s.lower().replace("-", " ")).split()


def wer(ref: list[str], hyp: list[str]) -> float:
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[-1] / max(1, len(ref))


def parse_log(path: Path):
    setting, pos = "?", "?"
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = TS.match(line)
        if not m:
            continue
        t = datetime.fromisoformat(m.group(1))
        if mm := RE_MARK.search(line):
            if mm.group(1) == "setting":
                setting = mm.group(2)
            else:
                pos = mm.group(2)
            continue
        block = (setting, pos)
        if mm := RE_WAKE.search(line):
            events.append((t, block, "wake", float(mm.group(1))))
        elif RE_NEAR.search(line):
            events.append((t, block, "near", None))
        elif mm := RE_END.search(line):
            events.append((t, block, "end", (mm.group(1), float(mm.group(3)), float(mm.group(5)) - float(mm.group(4)))))
        elif mm := RE_CAP.search(line):
            events.append((t, block, "end", ("max_record_seconds", float(mm.group(2)),
                                             float(mm.group(4)) - float(mm.group(3)))))
    return events


def report(a) -> None:
    out = Path(a.out)
    cmds = [ln.strip() for ln in Path(a.commands).read_text(encoding="utf-8").splitlines() if ln.strip()]
    events = parse_log(out / "satellite.log")
    turns = []
    with open(out / "turns.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            at = datetime.fromisoformat(r["at"].replace(" ", "T")[:19])
            turns.append((at, r))
    used = set()
    stats = defaultdict(lambda: defaultdict(list))
    for t, block, kind, val in events:
        s = stats[block]
        if kind == "wake":
            s["wakes"].append(val)
        elif kind == "near":
            s["near"].append(1)
        else:
            reason, secs, snr = val
            s["ends"].append(reason)
            s["secs"].append(secs)
            s["snr"].append(snr)
            # the turn row this capture made: the first unused one within 20 s after
            # (the satellite and server clocks are both NTP; --skew if not)
            tt = t + timedelta(seconds=a.skew)
            row = next(((i, r) for i, (at, r) in enumerate(turns)
                        if i not in used and timedelta(0) <= at - tt + timedelta(seconds=3) <= timedelta(seconds=23)), None)
            if row is None:
                s["no_row"].append(1)
                continue
            used.add(row[0])
            r = row[1]
            if r.get("no_answer_reason") == "overheard":
                s["overheard"].append(1)
            hyp = words(r.get("user_text") or "")
            best = min(cmds, key=lambda c: wer(words(c), hyp))
            w = wer(words(best), hyp)
            s["wer"].append(w)
            s["matched"].append(w <= 0.5)
    print(f"{'setting':>8} {'pos':>4} | wakes near | ends: silence/cap/server | capt s med | overheard | "
          f"matched/16  WER  | miss  false | p95-med dB")
    for block in sorted(stats):
        s = stats[block]
        ends = s["ends"]
        matched = sum(s["matched"])
        print(f"{block[0]:>8} {block[1]:>4} | {len(s['wakes']):>5} {len(s['near']):>4} | "
              f"{ends.count('vad_silence_after_speech'):>7}/{ends.count('max_record_seconds')}/{ends.count('server_endpoint'):<10} | "
              f"{median(s['secs']) if s['secs'] else 0:>10.1f} | {len(s['overheard']):>9} | "
              f"{matched:>6}/16 {median(s['wer']) if s['wer'] else 1:>5.2f} | "
              f"{max(0, 16 - matched):>4} {len(s['wakes']) - matched:>5} | "
              f"{median(s['snr']) if s['snr'] else 0:>6.1f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull")
    p.add_argument("--sat", required=True, help="user@satellite, for ssh")
    p.add_argument("--room", required=True, help="the satellite's room id")
    p.add_argument("--since", required=True, help='"YYYY-MM-DD HH:MM", the satellite\'s local time')
    p.add_argument("--until", required=True)
    p.add_argument("--tz", default="", help='appended to since/until for the server, e.g. "+00"')
    p.add_argument("--dsn", required=True, help="the server database; a read-only role is best")
    p.add_argument("--psql", default="psql", help="psql command (prefix), e.g. 'wsl -d NAME -- psql'")
    p.add_argument("--out", required=True)
    r = sub.add_parser("report")
    r.add_argument("--out", required=True)
    r.add_argument("--commands", required=True, help="the planned commands, one per line")
    r.add_argument("--skew", type=float, default=0.0, help="server clock minus satellite clock, seconds")
    a = ap.parse_args()
    (pull if a.cmd == "pull" else report)(a)


if __name__ == "__main__":
    sys.exit(main())
