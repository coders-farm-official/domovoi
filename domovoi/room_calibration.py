"""Speaker calibration (1.5.0): each device's real output latency, measured
by the room's own microphones (docs/ROOM_AUDIO_PROTOCOL.md §11).

Audio APIs report their own buffer, not the DAC, amplifier, USB and OS
buffers behind it. One speaker of the room plays a chirp at a stamped time
while every device records; then the next one plays. From the arrival
times the server solves each device's output latency, stores it
(``satellites.output_latency_us``, ``calibrated_at``) and sends it to the
device (``latency``), which plays that much earlier.

* :func:`solve`: the least-squares solve of §11.3 (pure);
* :class:`RoomCalibration`: one run per room over the room-audio sockets,
  the stored values, the last run's outcome for the room drawer, and the
  automatic run when a satellite joins or moves into a shared room
  (``room_calibration_auto``, off by default: it plays an audible chirp).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import secrets
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import numpy as np

log = logging.getLogger(__name__)

CHIRP = {"f0_hz": 1000, "f1_hz": 6000, "ms": 80, "level": 0.3}
LISTEN_MS = 400
LEAD_US = 1_000_000          # a step's chirp plays this far after it is sent
ANSWER_GRACE_S = 2.0         # answers due this long after the window ends
#: A measured delay outside this is not the chirp (an echo, a clock fault).
MIN_DELAY_US, MAX_DELAY_US = -20_000.0, 300_000.0
#: The solve is thrown away when a measurement misses it by more than this.
MAX_RESIDUAL_US = 2_000.0
#: Weight of the "same input latency" prior, against 1 per measurement.
PRIOR_WEIGHT = 1e-3
#: Stored values are clamped to this (relative, so the room's lowest is 0).
MAX_LATENCY_US = 250_000
#: The automatic run waits this long after a device joins (its clock fit
#: settles, a second device arriving with it joins the same run).
AUTO_DELAY_S = 15.0


# ─── the solve (§11.3) ────────────────────────────────────────────────────


@dataclass
class Solution:
    latency_us: dict[str, int]                  # solved devices, room's lowest = 0
    unsolved: list[str]                         # not linked by a pair heard both ways
    distance_us: dict[tuple[str, str], float] = field(default_factory=dict)
    rms_us: float = 0.0
    max_residual_us: float = 0.0

    @property
    def consistent(self) -> bool:
        return self.max_residual_us <= MAX_RESIDUAL_US


def _linked(devices: list[str], m: dict[tuple[str, str], float]) -> list[str]:
    """The largest group of devices linked by pairs heard both ways (ties:
    the one holding the first device)."""
    adj: dict[str, set[str]] = {d: set() for d in devices}
    for (i, j) in m:
        if i != j and (j, i) in m and i in adj and j in adj:
            adj[i].add(j)
            adj[j].add(i)
    seen: set[str] = set()
    best: list[str] = []
    for d in devices:
        if d in seen:
            continue
        group, todo = [], [d]
        seen.add(d)
        while todo:
            x = todo.pop()
            group.append(x)
            for y in adj[x] - seen:
                seen.add(y)
                todo.append(y)
        if len(group) > len(best):
            best = group
    return [d for d in devices if d in best] if len(best) >= 2 else []


def solve(devices: list[str], measurements: dict[tuple[str, str], float]) -> Solution:
    """Each device's output latency from ``measurements[(player, listener)]
    = arrival - at`` in microseconds (``player == listener``: the device
    hearing itself).

    ``m(i,j) = L(i) + d(i,j) + M(j)`` and ``m(i,i) = L(i) + M(i)``: least
    squares over every ``L``, ``M`` and pair distance ``d``, plus a weak
    prior that the input latencies ``M`` are alike (it only decides what
    the measurements leave open: a device that cannot hear itself). ``L``
    is known up to one constant for the room; the lowest is set to 0.
    """
    m = {k: float(v) for k, v in measurements.items()
         if MIN_DELAY_US <= float(v) <= MAX_DELAY_US}
    group = _linked(list(devices), m)
    unsolved = [d for d in devices if d not in group]
    if not group:
        return Solution({}, unsolved)
    n = len(group)
    idx = {d: k for k, d in enumerate(group)}
    pairs = [(group[a], group[b]) for a in range(n) for b in range(a + 1, n)]
    pidx = {p: 2 * n + k for k, p in enumerate(pairs)}
    cols = 2 * n + len(pairs)
    rows: list[np.ndarray] = []
    rhs: list[float] = []
    used: list[tuple[str, str]] = []
    for (i, j), v in m.items():
        if i not in idx or j not in idx:
            continue
        r = np.zeros(cols)
        r[idx[i]] += 1.0                         # L(i)
        r[n + idx[j]] += 1.0                     # M(j)
        if i != j:
            key = (i, j) if (i, j) in pidx else (j, i)
            r[pidx[key]] = 1.0                   # d(i, j)
        rows.append(r)
        rhs.append(v)
        used.append((i, j))
    n_obs = len(rows)
    for k in range(n):                           # M(k) - mean(M) = 0, weakly
        r = np.zeros(cols)
        r[n:2 * n] = -PRIOR_WEIGHT / n
        r[n + k] += PRIOR_WEIGHT
        rows.append(r)
        rhs.append(0.0)
    a, b = np.vstack(rows), np.asarray(rhs)
    x, *_ = np.linalg.lstsq(a, b, rcond=None)
    resid = (a[:n_obs] @ x) - b[:n_obs]
    lat = x[:n]
    lat = lat - lat.min()
    return Solution(
        latency_us={d: int(round(min(MAX_LATENCY_US, lat[idx[d]]))) for d in group},
        unsolved=unsolved,
        distance_us={p: float(x[pidx[p]]) for p in pairs},
        rms_us=float(np.sqrt(np.mean(resid ** 2))) if n_obs else 0.0,
        max_residual_us=float(np.max(np.abs(resid))) if n_obs else 0.0,
    )


# ─── the runs ─────────────────────────────────────────────────────────────


class CalibrationRefused(Exception):
    """A run that cannot start: ``status`` is the HTTP code, the message the
    sentence for the drawer."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


Store = Callable[[dict[str, int]], Awaitable[None]]


async def store_latencies(values: dict[str, int]) -> None:
    """Write the solved values and stamp ``calibrated_at``."""
    from domovoi.db.repositories import SatellitesRepository
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        repo = SatellitesRepository(s)
        for sid, us in values.items():
            await repo.set_calibration(sid, us)


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


class RoomCalibration:
    """Calibration runs over the room-audio sockets of ``playout``
    (``domovoi.room_playout.service.RoomPlayout``)."""

    def __init__(self, playout: Any, *, store: Store = store_latencies,
                 clock: Callable[[], int] | None = None,
                 lead_us: int = LEAD_US, listen_ms: int = LISTEN_MS,
                 answer_grace_s: float = ANSWER_GRACE_S,
                 auto: Callable[[], bool] | None = None,
                 auto_delay_s: float = AUTO_DELAY_S) -> None:
        self.playout = playout
        self.store = store
        self.clock = clock or playout.clock
        self.lead_us = lead_us
        self.listen_ms = listen_ms
        self.answer_grace_s = answer_grace_s
        self.auto = auto or _auto_setting
        self.auto_delay_s = auto_delay_s
        self.last: dict[str, dict[str, Any]] = {}       # room -> last run's outcome
        self._running: set[str] = set()
        self._auto_tasks: dict[str, asyncio.Task] = {}
        self._waiting: dict[tuple[str, int], tuple[set[str], dict[str, dict], asyncio.Event]] = {}
        playout.calibration = self

    # ── who takes part ─────────────────────────────────────────────────
    def participants(self, room_id: str) -> list[str]:
        return [s for s in self.playout.speakers(room_id) if self.playout.can_calibrate(s)]

    def room_busy(self, room_id: str) -> bool:
        st = self.playout.streamers.get(room_id)
        return st is not None and (st.active or st.music is not None)

    # ── frames in ──────────────────────────────────────────────────────
    def on_result(self, satellite_id: str, msg: dict[str, Any]) -> None:
        """A ``calibrate_result`` from a device's socket."""
        key = (str(msg.get("run_id")), msg.get("step"))
        w = self._waiting.get(key)  # type: ignore[arg-type]
        if w is None:
            return
        expect, got, done = w
        if satellite_id in expect and satellite_id not in got:
            got[satellite_id] = msg
            if expect <= set(got):
                done.set()

    # ── a run ──────────────────────────────────────────────────────────
    async def calibrate(self, room_id: str, *, reason: str = "admin",
                        names: dict[str, str | None] | None = None) -> dict[str, Any]:
        """Measure the room's speakers. Raises :class:`CalibrationRefused`
        when the run cannot start (one speaker, the room playing, a run
        already going); otherwise returns the outcome (also kept for the
        drawer in :attr:`last`)."""
        if room_id in self._running:
            raise CalibrationRefused(409, "The speakers in this room are being tuned already.")
        devices = self.participants(room_id)
        if len(devices) < 2:
            live = self.playout.speakers(room_id)
            msg = ("Tuning needs two speakers in the room that can take part; "
                   + ("this room has one." if len(devices) == 1 else "this room has none.")
                   + (" The others need newer software." if len(live) > len(devices) else ""))
            raise CalibrationRefused(409, msg)
        if self.room_busy(room_id):
            raise CalibrationRefused(409, "Something is playing in this room. Stop it, then tune the speakers.")
        self._running.add(room_id)
        try:
            outcome = await self._run(room_id, devices, reason, names or {})
        finally:
            self._running.discard(room_id)
        self.last[room_id] = outcome
        return outcome

    async def _step(self, run_id: str, step: int, player: str,
                    devices: list[str]) -> dict[str, dict[str, Any]]:
        at = int(self.clock() + self.lead_us)
        expect = set(devices)
        got: dict[str, dict[str, Any]] = {}
        done = asyncio.Event()
        self._waiting[(run_id, step)] = (expect, got, done)
        try:
            for sid in devices:
                self.playout.send_to(sid, {
                    "type": "calibrate", "run_id": run_id, "step": step, "at_us": at,
                    "play": sid == player, "listen_ms": self.listen_ms, "chirp": dict(CHIRP),
                })
            wait_s = (self.lead_us / 1e6) + self.listen_ms / 1000 + self.answer_grace_s
            try:
                await asyncio.wait_for(done.wait(), wait_s)
            except asyncio.TimeoutError:
                pass
        finally:
            self._waiting.pop((run_id, step), None)
        for r in got.values():
            r["_at_us"] = at
        return dict(got)

    async def _run(self, room_id: str, devices: list[str], reason: str,
                   names: dict[str, str | None]) -> dict[str, Any]:
        run_id = secrets.token_hex(4)
        log.info("calibration %s: room %s, devices %s (%s)", run_id, room_id, devices, reason)
        m: dict[tuple[str, str], float] = {}
        played: dict[str, bool] = {}
        heard_by: dict[str, int] = {d: 0 for d in devices}
        for step, player in enumerate(devices):
            answers = await self._step(run_id, step, player, devices)
            played[player] = bool(answers.get(player, {}).get("played"))
            for sid, r in answers.items():
                if r.get("heard") and isinstance(r.get("arrival_us"), (int, float)):
                    m[(player, sid)] = float(r["arrival_us"]) - r["_at_us"]
                    if sid != player:
                        heard_by[player] += 1
        sol = solve(devices, m)
        stored: dict[str, int] = {}
        if sol.latency_us and sol.consistent:
            stored = dict(sol.latency_us)
            try:
                await self.store(stored)
            except Exception:  # noqa: BLE001 — the run is lost, not the room
                log.exception("calibration %s: storing the values failed", run_id)
                stored = {}
            for sid, us in stored.items():
                self.playout.set_latency(sid, us)
        rows = []
        for sid in devices:
            if sid in stored:
                note = None
            elif not played.get(sid):
                note = "Its chirp did not play (the speaker was busy or did not answer)."
            elif heard_by[sid] == 0:
                note = "No other satellite heard its chirp: too far apart, or too noisy."
            else:
                note = "Not measured both ways, so it was left as it was."
            rows.append({"satellite_id": sid, "output_latency_us": stored.get(sid),
                         "tuned": sid in stored, "note": note})
        if sol.latency_us and not sol.consistent:
            status = "failed"
            message = ("The measurements disagreed with each other, so nothing was changed. "
                       "Try again when the room is quiet.")
        elif not stored:
            status = "failed"
            message = ("The satellites could not hear each other's chirps (too far apart, or "
                       "too noisy), so each keeps its previous setting.")
        elif len(stored) < len(devices):
            status = "partial"
            left = [names.get(r["satellite_id"]) or r["satellite_id"]
                    for r in rows if not r["tuned"]]
            message = (f"Tuned {len(stored)} of {len(devices)} speakers. "
                       f"{', '.join(left)} {_plural(len(left), 'keeps its', 'keep their')} "
                       "previous setting: the others could not hear "
                       f"{_plural(len(left), 'it', 'them')} clearly.")
        else:
            status = "done"
            message = f"Tuned {len(stored)} speakers."
        log.info("calibration %s: %s %s (rms %.0f us)", run_id, status, stored, sol.rms_us)
        return {"room_id": room_id, "status": status, "message": message, "reason": reason,
                "finished_at": _now_iso(), "satellites": rows}

    # ── the automatic run ──────────────────────────────────────────────
    def on_joined(self, satellite_id: str, room_id: str, calibrated: bool) -> None:
        """A speaker subscribed to ``room_id`` (connected, or moved in).
        With the setting on, an uncalibrated one in a shared room starts a
        run once things settle, if the room is quiet then."""
        if calibrated or not self.auto():
            return
        if room_id in self._auto_tasks and not self._auto_tasks[room_id].done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._auto_tasks[room_id] = loop.create_task(self._auto(room_id))

    async def _auto(self, room_id: str) -> None:
        await asyncio.sleep(self.auto_delay_s)
        if len(self.participants(room_id)) < 2 or self.room_busy(room_id):
            log.info("calibration: automatic run for %s skipped (alone or playing)", room_id)
            return
        try:
            await self.calibrate(room_id, reason="joined")
        except CalibrationRefused as e:
            log.info("calibration: automatic run for %s skipped (%s)", room_id, e.message)
        except Exception:  # noqa: BLE001
            log.exception("calibration: automatic run for %s failed", room_id)


def _auto_setting() -> bool:
    from domovoi.config import settings

    return bool(getattr(settings, "room_calibration_auto", False))


def get_calibration(app: Any) -> RoomCalibration:
    """The app's calibration service, on the app's playout."""
    from domovoi.room_playout.service import get_playout

    pl = get_playout(app)
    cal = getattr(pl, "calibration", None)
    if not isinstance(cal, RoomCalibration):
        cal = RoomCalibration(pl)
    return cal
