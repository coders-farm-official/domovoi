"""Curated TTS voice catalog + idempotent registry seeding.

The core pre-populates the voices registry with a curated
set of known-good voices: Edge cloud voices (download on demand, no file) and
single-speaker Piper local voices (auto-downloaded by HF name on first
synth). Multi-speaker Piper voices (libritts/arctic/…) need a speaker id
the synth path doesn't take, so they're intentionally omitted.

Controlled by ``SEED_VOICE_CATALOG`` (default on). Seeding is **idempotent
by name + model_ref**, so it augments an existing registry on the next boot
and never disturbs the chosen default — flip the flag off once you've
curated the list (deleted rows would otherwise reappear on reboot).

Microsoft Edge voices send what Domovoi says to Microsoft, so they are an
informed opt-in. The configured Edge voice (``TTS_EDGE_VOICE``) is
registered only when the catalog is on or ``TTS_ENGINE=edge`` — a household
that never chose Edge gets no Edge voice in its registry — and no Edge
voice at all (configured or catalog) is registered under
``INTERNET_ACCESS=never``. Rows already in the registry are never removed.

Labels are the unique first-names you say ("switch to Jenny"); they're kept
distinct across both engines so the DB's UNIQUE(name) is always satisfied.
"""

from __future__ import annotations

import logging

from domovoi import egress
from domovoi.config import settings

log = logging.getLogger(__name__)


# (label, Edge voice id). Curated from README.md's Edge table.
EDGE_CATALOG: list[tuple[str, str]] = [
    ("Aria", "en-US-AriaNeural"),
    ("Jenny", "en-US-JennyNeural"),
    ("Michelle", "en-US-MichelleNeural"),
    ("Ana", "en-US-AnaNeural"),
    ("Emma", "en-US-EmmaNeural"),
    ("Ava", "en-US-AvaNeural"),
    ("Guy", "en-US-GuyNeural"),
    ("Christopher", "en-US-ChristopherNeural"),
    ("Eric", "en-US-EricNeural"),
    ("Brian", "en-US-BrianNeural"),
    ("Andrew", "en-US-AndrewNeural"),
    ("Roger", "en-US-RogerNeural"),
    ("Steffan", "en-US-SteffanNeural"),
    ("Sonia", "en-GB-SoniaNeural"),      # British female
    ("Natasha", "en-AU-NatashaNeural"),  # Australian female
    ("William", "en-AU-WilliamNeural"),  # Australian male
    ("Clara", "en-CA-ClaraNeural"),      # Canadian female
    ("Emily", "en-IE-EmilyNeural"),      # Irish female
    ("Neerja", "en-IN-NeerjaNeural"),    # Indian female
]

# (label, Piper HF model_ref). Single-speaker only. Curated from
# yt_pipeline.py / README.md's Piper table.
PIPER_CATALOG: list[tuple[str, str]] = [
    ("Kusal", "en_US-kusal-medium"),
    ("Lessac", "en_US-lessac-medium"),
    ("Amy", "en_US-amy-medium"),
    ("Ryan", "en_US-ryan-high"),
    ("Joe", "en_US-joe-medium"),
    ("Kathleen", "en_US-kathleen-low"),
    ("Alan", "en_GB-alan-medium"),       # British male
    ("Alba", "en_GB-alba-medium"),       # Scottish female
    ("Cori", "en_GB-cori-high"),         # British female
]


def edge_label(voice_id: str) -> str:
    """"en-US-AriaNeural" → "Aria" (friendly spoken name)."""
    tail = voice_id.split("-")[-1] if "-" in voice_id else voice_id
    return tail.replace("Neural", "").strip() or voice_id


def piper_label(model_ref: str) -> str:
    """"en_US-lessac-medium" → "Lessac"."""
    parts = model_ref.split("-")
    core = parts[1] if len(parts) > 1 else parts[0]
    return core.capitalize() or model_ref


def planned_voices(
    *,
    include_catalog: bool,
    edge_voice: str,
    piper_voice: str,
    include_edge_voice: bool,
    include_edge_catalog: bool = True,
) -> list[tuple[str, str, str]]:
    """The de-duplicated ``(engine, name, model_ref)`` list to ensure.

    Always includes the configured Piper voice, and the configured Edge
    voice when ``include_edge_voice`` (so a default can point at either even
    with the catalog disabled); prepends the full catalog when enabled — its
    Edge half only when ``include_edge_catalog``. De-duped by ``(engine,
    model_ref)`` keeping the first label, so a configured voice already in
    the catalog isn't doubled. Pure: :func:`seed_voices` decides the flags."""
    rows: list[tuple[str, str, str]] = []
    if include_catalog:
        if include_edge_catalog:
            rows += [("edge", n, r) for n, r in EDGE_CATALOG]
        rows += [("piper", n, r) for n, r in PIPER_CATALOG]
    if include_edge_voice:
        rows.append(("edge", edge_label(edge_voice), edge_voice))
    rows.append(("piper", piper_label(piper_voice), piper_voice))

    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, str]] = []
    for engine, name, ref in rows:
        if (engine, ref) in seen:
            continue
        seen.add((engine, ref))
        out.append((engine, name, ref))
    return out


async def seed_voices(
    repo,
    *,
    include_catalog: bool,
    edge_voice: str,
    piper_voice: str,
    default_is_piper: bool,
    include_edge_voice: bool | None = None,
) -> int:
    """Idempotently ensure the planned voices exist and exactly one default
    is set. Returns the number of rows created.

    ``include_edge_voice`` None (what the boot passes) derives it:
    ``(include_catalog or settings.tts_engine == "edge")`` and the internet
    answer isn't ``never``. Under ``never`` the catalog's Edge voices are
    left out too. A row is skipped if its name (case-insensitive) or
    ``(engine, model_ref)`` already exists, so re-running never duplicates
    and never raises on the UNIQUE(name) constraint. An existing default is
    preserved; only a registry with no default gets the configured engine's
    voice promoted (falling back to the first row)."""
    edge_allowed = not egress.internet_turned_off()
    if include_edge_voice is None:
        engine_is_edge = (settings.tts_engine or "").strip().lower() == "edge"
        include_edge_voice = include_catalog or engine_is_edge
    include_edge_voice = include_edge_voice and edge_allowed

    existing = await repo.all()
    names = {v["name"].lower() for v in existing}
    refs = {(v["engine"], v["model_ref"]) for v in existing}

    created = 0
    for engine, name, ref in planned_voices(
        include_catalog=include_catalog,
        edge_voice=edge_voice,
        piper_voice=piper_voice,
        include_edge_voice=include_edge_voice,
        include_edge_catalog=edge_allowed,
    ):
        if name.lower() in names or (engine, ref) in refs:
            continue
        await repo.create(name=name, engine=engine, model_ref=ref)
        names.add(name.lower())
        refs.add((engine, ref))
        created += 1

    if await repo.get_default() is None:
        target = ("piper", piper_voice) if default_is_piper else ("edge", edge_voice)
        rows = await repo.all()
        match = next((v for v in rows if (v["engine"], v["model_ref"]) == target), None)
        if match is None and rows:
            match = rows[0]
        if match is not None:
            await repo.set_default(match["id"])

    return created
