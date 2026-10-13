"""The voice registry's seeding: the three public-domain Piper voices.

The core pre-populates the voices registry with the voices Domovoi speaks
with locally (``SHIPPED_VOICES`` in domovoi/clients/tts.py, auto-downloaded
on first synth, or fetched ahead by the Linux provisioning), and the
configured Piper voice (``TTS_PIPER_VOICE``).

Taken OUT of the registry at boot:

* the Piper voices Domovoi used to offer (``RETIRED_PIPER_VOICES``: lessac
  and the voices fine-tuned from it or from ryan, whose licences aren't
  permissive);
* every Microsoft Edge voice (engine ``edge``): the online voices were
  dropped on 2026-10-12 (the owner's decision: Microsoft's service had
  started refusing edge-tts, every reply went to Microsoft, and edge-tts is
  LGPL), so a registered cloud voice is removed on the update.

A removed row that was the default moves the default to the configured
Piper voice; a room set to a removed voice speaks in the default (the
resolver falls back to it, domovoi/streaming.py ``resolve_voice``), and
its clips are pruned with the row.

Controlled by ``SEED_VOICE_CATALOG`` (default on). Seeding is **idempotent
by name + model_ref**, so it augments an existing registry on the next boot
and never disturbs the chosen default — flip the flag off once you've
curated the list (deleted rows would otherwise reappear on reboot). The
configured Piper voice is registered either way. Rows already in the
registry are never removed, the retired and Edge voices excepted.

Labels are the unique first-names you say ("switch to Kristin"); the DB's
UNIQUE(name) is always satisfied.
"""

from __future__ import annotations

import logging

from domovoi.clients.tts import RETIRED_PIPER_VOICES, SHIPPED_VOICES

log = logging.getLogger(__name__)


# (label, Piper HF model_ref): the three public-domain voices.
PIPER_CATALOG: list[tuple[str, str]] = [(v.name, v.model_ref) for v in SHIPPED_VOICES]

# Engines the registry no longer speaks: their rows are removed at boot.
DROPPED_ENGINES: frozenset[str] = frozenset({"edge"})


def piper_label(model_ref: str) -> str:
    """"en_US-kristin-medium" → "Kristin"; a shipped voice's own name
    ("en_US-ljspeech-medium" → "Linda")."""
    for v in SHIPPED_VOICES:
        if v.model_ref == model_ref:
            return v.name
    parts = model_ref.split("-")
    core = parts[1] if len(parts) > 1 else parts[0]
    return core.capitalize() or model_ref


def planned_voices(*, include_catalog: bool, piper_voice: str) -> list[tuple[str, str, str]]:
    """The de-duplicated ``(engine, name, model_ref)`` list to ensure: the
    catalog's three voices when enabled, and always the configured Piper
    voice. De-duped by ``(engine, model_ref)`` keeping the first label."""
    rows: list[tuple[str, str, str]] = []
    if include_catalog:
        rows += [("piper", n, r) for n, r in PIPER_CATALOG]
    rows.append(("piper", piper_label(piper_voice), piper_voice))

    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, str]] = []
    for engine, name, ref in rows:
        if (engine, ref) in seen:
            continue
        seen.add((engine, ref))
        out.append((engine, name, ref))
    return out


def _removed_on_boot(row: dict) -> str | None:
    """Why a registry row goes at boot, or None when it stays."""
    engine = (row["engine"] or "").strip().lower()
    if engine in DROPPED_ENGINES:
        return "a Microsoft Edge voice (the online voices were dropped)"
    if engine == "piper" and row["model_ref"] in RETIRED_PIPER_VOICES:
        return "a voice Domovoi no longer offers"
    return None


async def seed_voices(repo, *, include_catalog: bool, piper_voice: str) -> int:
    """Remove the retired and Edge voices, idempotently ensure the planned
    voices exist, and make sure exactly one default is set. Returns the
    number of rows created.

    A row is skipped if its name (case-insensitive) or ``(engine,
    model_ref)`` already exists, so re-running never duplicates and never
    raises on the UNIQUE(name) constraint. An existing default is
    preserved; a registry with no default (or whose default was removed)
    gets the configured Piper voice promoted (falling back to the first
    row)."""
    existing = await repo.all()
    removed = 0
    for v in existing:
        why = _removed_on_boot(v)
        if why is None:
            continue
        if v["is_default"]:
            await repo.clear_default()
        await repo.delete(v["id"])
        removed += 1
        log.info("voice registry: removed %s (%s), %s", v["name"], v["model_ref"], why)
    if removed:
        existing = await repo.all()
    names = {v["name"].lower() for v in existing}
    refs = {(v["engine"], v["model_ref"]) for v in existing}

    created = 0
    for engine, name, ref in planned_voices(include_catalog=include_catalog, piper_voice=piper_voice):
        if name.lower() in names or (engine, ref) in refs:
            continue
        await repo.create(name=name, engine=engine, model_ref=ref)
        names.add(name.lower())
        refs.add((engine, ref))
        created += 1

    if await repo.get_default() is None:
        rows = await repo.all()
        match = next((v for v in rows if (v["engine"], v["model_ref"]) == ("piper", piper_voice)), None)
        if match is None and rows:
            match = rows[0]
        if match is not None:
            await repo.set_default(match["id"])

    return created
