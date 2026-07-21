"""HomelabHandler — spoken status of the Domovoi server host (Domovoi).

Three sources, all queried in parallel and degraded individually if the
underlying tool isn't available:

* ``nvidia-smi`` for GPU load + VRAM use + temperature. Native binary
  on Domovoi's Windows install — gracefully no-ops on a CPU-only host or
  if nvidia-smi isn't on PATH.
* Ollama's ``GET /api/ps`` for currently-loaded models (RAM-resident,
  i.e. actually using VRAM right now — different from ``/api/tags``
  which just lists what's installed on disk).
* The core's in-memory ``app.state.active_sessions`` for which
  rooms have a Pi connected.

The spoken summary is short on purpose — long status reads are tedious
through TTS. Detail is logged at INFO for the curious.

Fully local (``requires_network="no"``); Ollama runs on the same host.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.config import settings
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


# `(?:(?:my|the) )?` covers the natural possessive/article — Whisper
# happily transcribes "what's my domovoi doing" / "what's the server
# doing", both of which the original (no-modifier) pattern missed.
# "(?:doing|up to)" picks up "what's domovoi up to". The "how's domovoi"
# arm catches the conversational variant that doesn't start with "what".
_STATUS_RE = re.compile(
    r"^(?:"
    r"what(?:'s| is) (?:(?:my|the) )?(?:domovoi|server|homelab|host) (?:doing|up to)"
    r"|how(?:'s| is) (?:(?:my|the) )?domovoi(?: doing)?"
    r"|is (?:(?:my|the) )?domovoi busy"
    r"|(?:(?:my|the) )?domovoi status"
    r"|system status"
    r"|homelab status"
    r"|gpu status"
    r")$"
)


@dataclass
class _GpuSnapshot:
    name: str
    util_pct: int
    mem_used_mb: int
    mem_total_mb: int
    temp_c: int


async def _query_gpus() -> list[_GpuSnapshot]:
    """Run ``nvidia-smi`` in CSV mode. Empty list on any failure.

    Output format (no header, no units):
      ``<name>, <gpu_util>, <mem_used>, <mem_total>, <temp>``
    """
    if shutil.which("nvidia-smi") is None:
        return []
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except (OSError, asyncio.TimeoutError) as e:
        log.warning("nvidia-smi query failed: %s", e)
        return []
    snapshots: list[_GpuSnapshot] = []
    for line in stdout.decode(errors="replace").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 5:
            continue
        try:
            snapshots.append(
                _GpuSnapshot(
                    name=parts[0],
                    util_pct=int(parts[1]),
                    mem_used_mb=int(parts[2]),
                    mem_total_mb=int(parts[3]),
                    temp_c=int(parts[4]),
                )
            )
        except ValueError:
            continue
    return snapshots


async def _query_loaded_models() -> list[str]:
    """Currently-loaded Ollama models (i.e. using VRAM right now).

    Empty list if Ollama is unreachable, the endpoint shape changed, or
    no models are loaded — all surface as "no models loaded" in the
    spoken summary, which is the right user-facing answer either way.
    """
    url = f"{settings.ollama_url.rstrip('/')}/api/ps"
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            resp = await c.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        log.warning("ollama /api/ps failed: %s", e)
        return []
    models = payload.get("models") or []
    return [m.get("name") or m.get("model") or "unknown" for m in models]


def _format_gpu_summary(gpus: list[_GpuSnapshot]) -> str:
    """One-line GPU summary suitable for TTS.

    Per-GPU detail is too noisy aloud, so we report:
      * how many GPUs are visible
      * average utilization
      * combined VRAM used / total
      * hottest GPU's temp (the one most likely to be a problem)
    """
    if not gpus:
        return "No GPUs detected"
    avg_util = round(sum(g.util_pct for g in gpus) / len(gpus))
    used = sum(g.mem_used_mb for g in gpus)
    total = sum(g.mem_total_mb for g in gpus)
    hottest = max(g.temp_c for g in gpus)
    used_gb = used / 1024
    total_gb = total / 1024
    if len(gpus) == 1:
        prefix = "GPU"
    else:
        prefix = f"{len(gpus)} GPUs averaging"
    return (
        f"{prefix} {avg_util}% load, {used_gb:.1f} of {total_gb:.1f} gigabytes "
        f"VRAM used, hottest at {hottest} degrees"
    )


def _format_models_summary(model_names: list[str]) -> str:
    if not model_names:
        return "no models loaded"
    if len(model_names) == 1:
        return f"{model_names[0]} loaded"
    if len(model_names) == 2:
        return f"{model_names[0]} and {model_names[1]} loaded"
    return f"{len(model_names)} models loaded"


def _format_rooms_summary(active_rooms: list[str]) -> str:
    if not active_rooms:
        return "no rooms connected"
    if len(active_rooms) == 1:
        return f"the {active_rooms[0]} satellite is connected"
    return f"{len(active_rooms)} satellites connected"


class HomelabHandler(Handler):
    name = "homelab"
    # band rationale: utility; anchored status vocabulary, no known collisions.
    priority_band = 250
    display = HandlerDisplay(label="Homelab", tone="device")
    requires_network = "no"

    tool_schema = {
        "name": "homelab",
        "description": (
            "Report the Domovoi server's current status: GPU load + "
            "VRAM use, currently-loaded Ollama models, and connected Pi "
            "satellites. No arguments — always returns a spoken summary."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    }

    def __init__(self) -> None:
        self.fast_paths = [FastPath(_STATUS_RE, HomelabHandler._status_from_match)]

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._status(ctx)

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._status(ctx)

    async def _status_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._status(ctx)

    async def _status(self, ctx: Context) -> Response:
        gpus, model_names = await asyncio.gather(
            _query_gpus(), _query_loaded_models()
        )
        active_rooms = sorted(self._active_rooms(ctx))

        gpu_summary = _format_gpu_summary(gpus)
        model_summary = _format_models_summary(model_names)
        room_summary = _format_rooms_summary(active_rooms)

        # Detail at INFO so the user can scroll the terminal log if they
        # want the raw numbers; the spoken response stays compact.
        log.info(
            "homelab status: gpus=%s models=%s rooms=%s",
            [(g.name, g.util_pct, g.mem_used_mb, g.mem_total_mb, g.temp_c) for g in gpus],
            model_names,
            active_rooms,
        )

        return Response(
            text=f"{gpu_summary}. {model_summary.capitalize()}. {room_summary.capitalize()}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={
                "gpus": [
                    {
                        "name": g.name,
                        "util_pct": g.util_pct,
                        "mem_used_mb": g.mem_used_mb,
                        "mem_total_mb": g.mem_total_mb,
                        "temp_c": g.temp_c,
                    }
                    for g in gpus
                ],
                "loaded_models": model_names,
                "active_rooms": active_rooms,
            },
        )

    @staticmethod
    def _active_rooms(ctx: Context) -> list[str]:
        """Best-effort lookup of room_ids the core knows about.

        Reads the module-level ``_room_ports`` from ``clients.mpd``, which
        is populated by ``mpd_provisioner.warm_known_rooms`` on startup
        and ``ensure_room`` on each first-connect. This is the
        provisioned set, not the currently-connected set —
        ``app.state.active_sessions`` would be more precise but isn't
        reachable through the handler signature, and in steady-state a
        2–4 satellite home both sets agree.
        """
        # Module-import (not `from ... import _room_ports`) so test
        # rebinds on `mpd._room_ports = ...` are visible here. See
        # IntercomHandler for the same pattern + reasoning.
        from domovoi.clients import mpd

        return list(mpd._room_ports.keys())
