"""The spoken-name resolver's index and scorer: pure, no DB, no settings.

:mod:`domovoi.handlers.shared.library_match` is the public face (types,
the process-wide index, the DB reads); this module is the part that turns
library rows into an index and scores a request against it. It is kept
apart so the heavy dependencies (rapidfuzz, anyascii, Metaphone, through
:mod:`spoken_names`) load only when the resolver is first used: the web
process and the handler registry import ``library_match`` at start-up,
and a missing wheel must cost the resolver, not the core.

The ranker is the 2026-10-02 audit's untuned one (``mymatch.py``), with
its constants fixed in advance and never moved to fit an evaluation set:

* every library name is indexed under all of its spoken forms; a request
  is matched exactly on any form (score 1.0), else
  ``0.6 × spelling + 0.4 × sound`` (rapidfuzz ratio on the space-less
  form, 0.95 × token-sort ratio on the spaced one; Double Metaphone keys
  compared with ratio);
* an entity that does not account for every content word of the request
  is never played outright (its score stays just under the play bar);
* "<title> by <artist>" weighs the title 0.6 and the artist 0.4, and the
  whole phrase is scored as well, so "stand by me" stays a title;
* names of three letters or fewer match exactly only.

Entities are what a request can name: an ``artist`` (every spelling of
the name merged by ``alias_key``), a whole multi-artist ``credit``, a
``title`` (per primary artist), an ``album`` (per folder) and a ``track``
(one row, only ever the target of a song alias). Household and
MusicBrainz aliases (``library_aliases``) are extra spoken forms of their
target, in a small layer rebuilt on its own when only aliases change.

Hooks for later phases are single functions on purpose:
:func:`_candidate_pool` (candidate narrowing), :func:`_score` (a
household-plays prior) and :func:`resolve`'s query (N-best transcripts).
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePath
from typing import Any, Iterable, Mapping, Sequence

from rapidfuzz import fuzz, process

from domovoi.handlers.shared.library_match import (
    ENTITY_TYPES,
    Candidate,
    EntityRef,
    Resolution,
)
from domovoi.handlers.shared.spoken_names import (
    alias_key,
    clean_title,
    compact,
    entity_names,
    phonetic_key,
    sounds_like,
    speakable,
    spoken_forms,
)

# ─── Fixed constants (chosen a priori; never fitted to an evaluation set) ──

#: Query forms scored fuzzily (the primary reading first).
FUZZY_FORMS = 4
#: Best matches kept per searched list.
EXTRACT_LIMIT = 30
#: Spelling scores below this (rapidfuzz 0-100) count as no match.
FUZZY_CUTOFF = 60
#: Sound (Double Metaphone) scores below this count as no match.
PHONETIC_CUTOFF = 70
W_FUZZY, W_PHONETIC = 0.6, 0.4
#: Token-sort ratio counts slightly less than a straight spelling match.
TOKEN_SORT_WEIGHT = 0.95
#: "<title> by <artist>": the title's weight, the artist's weight.
W_TITLE, W_ARTIST = 0.6, 0.4
#: The by-split reading wins unless the whole phrase scores this much more.
BY_SPLIT_MARGIN = 0.05
#: A word is accounted for by a name it partially matches this well.
COVERAGE_PARTIAL = 80
#: Forms this short (space-less) are matched exactly only (TI, MIA, U2).
SHORT_KEY = 3
#: Most entities offered by one "did you mean".
MAX_ASK = 3

STOP_WORDS = frozenset({"the", "a", "an", "and", "of", "to", "by"})

#: Where a matching form came from; higher wins an exact tie.
ORIGIN_MB, ORIGIN_LIBRARY, ORIGIN_HOUSEHOLD = 0, 1, 2

ARTIST_KINDS = frozenset({"artist", "credit"})
TITLE_KINDS = frozenset({"title", "track"})
ALBUM_KINDS = frozenset({"album"})

#: One leading carrier phrase is stripped (and the request ALSO scored
#: without stripping it); the stripped reading is limited to these kinds.
CARRIERS: tuple[tuple[str, frozenset[str]], ...] = tuple(
    sorted(
        (
            *((c, ARTIST_KINDS) for c in (
                "some ", "songs by ", "song by ", "music by ", "something by ",
                "anything by ", "stuff by ", "tracks by ", "the band ",
                "the group ", "the artist ",
            )),
            *((c, ALBUM_KINDS) for c in ("the album ", "album ", "the record ")),
            *((c, TITLE_KINDS) for c in ("the song ", "song ", "the track ", "track ")),
        ),
        key=lambda ck: -len(ck[0]),
    )
)

#: "play <X> by <artist>" where X only says "some of their music".
CARRIER_TITLES = frozenset({
    "songs", "some songs", "music", "something", "anything", "stuff",
    "tracks", "some",
})

# Owner of a form: (entity index, origin, source label). The label is
# "library", "alias", "musicbrainz" or "phrase".
Owner = tuple[int, int, str]


# ─── Index structures ──────────────────────────────────────────────────────


@dataclass(slots=True, eq=False)
class Entity:
    type: str
    key: str
    label: str
    artist_label: str | None
    track_ids: tuple[int, ...]
    #: The part of ``key`` that is the entity's own name (title / album:
    #: before the "|"), for readback and alias targeting.
    name_key: str
    #: title / album / track: the artist and credit entities credited on
    #: its rows (the "by <artist>" half of a request is scored on these).
    credited: frozenset[int] = frozenset()
    #: album / title / track: the primary-artist keys of its rows.
    artist_keys: frozenset[str] = frozenset()
    #: Its library forms: (spaced form, space-less form, Double Metaphone).
    forms: tuple[tuple[str, str, str], ...] = ()
    #: title: its "<title> <artist>" phrase form, space-less ("" for none).
    phrase: str = ""

    def ref(self) -> EntityRef:
        return EntityRef(
            type=self.type,  # type: ignore[arg-type]
            key=self.key,
            label=self.label,
            artist_label=self.artist_label,
        )


@dataclass(slots=True, eq=False)
class RowInfo:
    """What a ``track`` entity (a song alias's target) needs of its row."""

    title: str
    primary: str
    primary_key: str
    credited: frozenset[int]


class Pool:
    """One searchable set of forms, de-duplicated: every distinct string
    once, with the entities that own it. Forms of :data:`SHORT_KEY` chars
    or fewer are never added (they match exactly only)."""

    __slots__ = (
        "compacts", "compact_owners", "forms", "form_owners", "phons", "phon_owners",
    )

    def __init__(self, entries: Iterable[tuple[str, str, str, Owner]]) -> None:
        by_compact: dict[str, dict[Owner, None]] = {}
        by_form: dict[str, dict[Owner, None]] = {}
        by_phon: dict[str, dict[Owner, None]] = {}
        for form, comp, phon, owner in entries:
            if len(comp) <= SHORT_KEY:
                continue
            by_compact.setdefault(comp, {})[owner] = None
            by_form.setdefault(form, {})[owner] = None
            if phon:
                by_phon.setdefault(phon, {})[owner] = None
        self.compacts = list(by_compact)
        self.compact_owners = [tuple(v) for v in by_compact.values()]
        self.forms = list(by_form)
        self.form_owners = [tuple(v) for v in by_form.values()]
        self.phons = list(by_phon)
        self.phon_owners = [tuple(v) for v in by_phon.values()]

    def __len__(self) -> int:
        return len(self.forms)


@dataclass(slots=True, eq=False)
class AliasLayer:
    """The household + MusicBrainz aliases, attached to their targets."""

    extra: list[Entity]
    exact: dict[str, tuple[Owner, ...]]
    pools: dict[str, Pool]
    #: entity index → its alias forms (spaced, compact, phonetic, origin, label).
    forms: dict[int, tuple[tuple[str, str, str, int, str], ...]]
    #: entity index → household aliases, newest first.
    household: dict[int, tuple[str, ...]]
    #: entity index → MusicBrainz aliases.
    musicbrainz: dict[int, tuple[str, ...]]
    attached: int
    inert: int


class Index:
    """Base layer (the library) + alias layer. Immutable once built;
    :func:`with_aliases` makes a new one sharing the base."""

    def __init__(self) -> None:
        self.base_entities: list[Entity] = []
        self.by_ref: dict[tuple[str, str], int] = {}
        self.exact: dict[str, tuple[Owner, ...]] = {}
        self.phrase_exact: dict[str, tuple[Owner, ...]] = {}
        self.pools: dict[str, Pool] = {}
        self.phrase_pool: Pool = Pool(())
        self.rows: dict[int, RowInfo] = {}
        self.albums_by_name: dict[str, tuple[int, ...]] = {}
        self.n_tracks = 0
        self.n_forms = 0
        self.base_ms = 0.0
        self.aliases = AliasLayer([], {}, {}, {}, {}, {}, 0, 0)
        self.entities: list[Entity] = []
        self.alias_ms = 0.0
        self.built_at: datetime = datetime.now(timezone.utc)
        self._speak: dict[int, str] = {}

    # ── lookups ──

    def entity(self, i: int) -> Entity:
        return self.entities[i]

    def find(self, kind: str, key: str) -> int | None:
        i = self.by_ref.get((kind, key))
        if i is not None:
            return i
        if kind == "track":
            for j, e in enumerate(self.aliases.extra, start=len(self.base_entities)):
                if e.key == key:
                    return j
        return None

    def exact_owners(self, comp: str, *, phrase: bool) -> Iterable[Owner]:
        yield from self.exact.get(comp, ())
        yield from self.aliases.exact.get(comp, ())
        if phrase:
            yield from self.phrase_exact.get(comp, ())

    def coverage_forms(self, i: int, *, phrase: bool) -> list[str]:
        e = self.entities[i]
        out = [c for _, c, _ in e.forms]
        out.extend(c for _, c, _, _, _ in self.aliases.forms.get(i, ()))
        if phrase and e.phrase:
            out.append(e.phrase)
        return out

    def all_forms(self, i: int, *, phrase: bool) -> list[tuple[str, str, str, int, str]]:
        """Every form of entity ``i`` with its origin and source label."""
        e = self.entities[i]
        out = [(f, c, p, ORIGIN_LIBRARY, "library") for f, c, p in e.forms]
        out.extend(self.aliases.forms.get(i, ()))
        if phrase and e.phrase:
            out.append((e.phrase, e.phrase, "", ORIGIN_LIBRARY, "phrase"))
        return out

    def speak(self, i: int) -> str:
        """How to SAY entity ``i``: the newest household alias that sounds
        like its name, else a MusicBrainz one that does, else the name made
        speakable."""
        hit = self._speak.get(i)
        if hit is not None:
            return hit
        e = self.entities[i]
        out = None
        for alias in self.aliases.household.get(i, ()):
            if sounds_like(alias, e.label):
                out = alias
                break
        if out is None:
            for alias in self.aliases.musicbrainz.get(i, ()):
                if sounds_like(alias, e.label):
                    out = alias
                    break
        if out is None:
            out = speakable(e.label)
        self._speak[i] = out
        return out

    def candidate(self, i: int, score: float, via: str) -> Candidate:
        e = self.entities[i]
        return Candidate(
            ref=e.ref(), score=float(score), via=via, speak=self.speak(i),
            track_ids=e.track_ids,
        )

    def stats(self) -> dict[str, Any]:
        return {
            "tracks": self.n_tracks,
            "entities": len(self.entities),
            "forms": self.n_forms,
            "aliases": self.aliases.attached,
            "aliases_inert": self.aliases.inert,
            "build_ms": round(self.base_ms + self.alias_ms, 1),
            "built_at": self.built_at.isoformat(),
        }


# ─── Building ──────────────────────────────────────────────────────────────


class _Acc:
    __slots__ = ("spell", "ids", "primary", "files", "credit_keys", "part_keys")

    def __init__(self) -> None:
        self.spell: Counter[str] = Counter()
        self.ids: dict[int, None] = {}
        self.primary: Counter[str] = Counter()
        self.files: dict[int, str] = {}
        self.part_keys: dict[str, None] = {}
        self.credit_keys: dict[str, None] = {}


def _row_parts(row: Mapping[str, Any]) -> tuple[str, list[str], str, str]:
    """(credit, performer parts, title, album) of a row as
    :func:`spoken_names.entity_names` reads it (an artist-less "Artist -
    Title" row gives both)."""
    names = entity_names(row.get("title"), row.get("artist"), row.get("album"))
    parts = [n for k, n in names if k == "artist"]
    credit = next((n for k, n in names if k == "credit"), parts[0] if parts else "")
    title = next((n for k, n in names if k == "title"), "")
    album = next((n for k, n in names if k == "album"), "")
    return credit, parts, title, album


def _forms_of(names: Iterable[str], *, cleaned: bool) -> tuple[tuple[str, str, str], ...]:
    out: dict[str, None] = {}
    for name in names:
        for f in spoken_forms(name):
            out.setdefault(f, None)
        if cleaned:
            for f in spoken_forms(clean_title(name)):
                out.setdefault(f, None)
    return tuple(
        (f, compact(f), phonetic_key(f) if len(compact(f)) > SHORT_KEY else "")
        for f in out
    )


def _folder_key(file_path: str) -> str:
    if not file_path:
        return ""
    return PurePath(file_path).parent.as_posix().lower()


def build(tracks: Sequence[Mapping[str, Any]], aliases: Sequence[Mapping[str, Any]] = ()) -> Index:
    """The base layer over ``tracks`` plus the alias layer over ``aliases``."""
    t0 = time.perf_counter()
    rows = sorted(
        (r for r in tracks if r.get("id") is not None), key=lambda r: int(r["id"])
    )
    artists: dict[str, _Acc] = {}
    credits: dict[str, _Acc] = {}
    titles: dict[str, _Acc] = {}
    albums: dict[str, _Acc] = {}
    row_keys: dict[int, tuple[str, str, list[str], str, str]] = {}

    for r in rows:
        tid = int(r["id"])
        credit, parts, title, album = _row_parts(r)
        primary = parts[0] if parts else ""
        pkey = alias_key(primary) if primary else ""
        part_keys: list[str] = []
        for p in parts:
            k = alias_key(p)
            if not k:
                continue
            acc = artists.setdefault(k, _Acc())
            acc.spell[p] += 1
            acc.ids[tid] = None
            part_keys.append(k)
        ckey = ""
        if len(parts) > 1:
            ckey = alias_key(credit)
            if ckey:
                acc = credits.setdefault(ckey, _Acc())
                acc.spell[credit] += 1
                acc.ids[tid] = None
        if title:
            tkey = alias_key(clean_title(title))
            if tkey:
                acc = titles.setdefault(f"{tkey}|{pkey}", _Acc())
                acc.spell[title] += 1
                acc.ids[tid] = None
                if primary:
                    acc.primary[primary] += 1
                acc.part_keys.update(dict.fromkeys(part_keys))
                if ckey:
                    acc.credit_keys[ckey] = None
        if album:
            akey = alias_key(album)
            if akey:
                fp = str(r.get("file_path") or "")
                acc = albums.setdefault(f"{akey}|{_folder_key(fp)}", _Acc())
                acc.spell[album] += 1
                acc.ids[tid] = None
                acc.files[tid] = fp
                acc.primary[pkey] += 1
                acc.part_keys.update(dict.fromkeys(part_keys))
                if ckey:
                    acc.credit_keys[ckey] = None
        row_keys[tid] = (title, primary, part_keys, ckey, pkey)

    idx = Index()
    ents = idx.base_entities

    def add(e: Entity) -> int:
        ents.append(e)
        idx.by_ref[(e.type, e.key)] = len(ents) - 1
        return len(ents) - 1

    for key, acc in artists.items():
        add(Entity(
            type="artist", key=key, label=acc.spell.most_common(1)[0][0],
            artist_label=None, track_ids=tuple(acc.ids), name_key=key,
            forms=_forms_of(acc.spell, cleaned=False),
        ))
    for key, acc in credits.items():
        add(Entity(
            type="credit", key=key, label=acc.spell.most_common(1)[0][0],
            artist_label=None, track_ids=tuple(acc.ids), name_key=key,
            forms=_forms_of(acc.spell, cleaned=False),
        ))

    def credited(acc: _Acc) -> frozenset[int]:
        out = {idx.by_ref[("artist", k)] for k in acc.part_keys if ("artist", k) in idx.by_ref}
        out.update(idx.by_ref[("credit", k)] for k in acc.credit_keys if ("credit", k) in idx.by_ref)
        return frozenset(out)

    for key, acc in titles.items():
        tkey, _, pkey = key.partition("|")
        label = next(iter(acc.spell))  # the lowest id's spelling
        primary = acc.primary.most_common(1)[0][0] if acc.primary else None
        add(Entity(
            type="title", key=key, label=label, artist_label=primary,
            track_ids=tuple(sorted(acc.ids)), name_key=tkey,
            credited=credited(acc), artist_keys=frozenset({pkey} if pkey else ()),
            forms=_forms_of(acc.spell, cleaned=True),
        ))
    albums_by_name: dict[str, list[int]] = {}
    for key, acc in albums.items():
        akey = key.partition("|")[0]
        n = len(acc.ids)
        top_key, top_n = acc.primary.most_common(1)[0]
        artist_label = None
        if top_key and top_n * 2 >= n and ("artist", top_key) in idx.by_ref:
            artist_label = ents[idx.by_ref[("artist", top_key)]].label
        i = add(Entity(
            type="album", key=key, label=acc.spell.most_common(1)[0][0],
            artist_label=artist_label,
            track_ids=tuple(sorted(acc.ids, key=lambda t: (acc.files.get(t, ""), t))),
            name_key=akey, credited=credited(acc),
            artist_keys=frozenset(k for k in acc.primary if k),
            forms=_forms_of(acc.spell, cleaned=True),
        ))
        albums_by_name.setdefault(akey, []).append(i)
    idx.albums_by_name = {k: tuple(v) for k, v in albums_by_name.items()}

    for tid, (title, primary, part_keys, ckey, pkey) in row_keys.items():
        cred = {idx.by_ref[("artist", k)] for k in part_keys if ("artist", k) in idx.by_ref}
        if ckey and ("credit", ckey) in idx.by_ref:
            cred.add(idx.by_ref[("credit", ckey)])
        idx.rows[tid] = RowInfo(title=title, primary=primary, primary_key=pkey, credited=frozenset(cred))

    # Forms → the exact table and one search pool per entity type.
    exact: dict[str, dict[Owner, None]] = {}
    per_type: dict[str, list[tuple[str, str, str, Owner]]] = {t: [] for t in ENTITY_TYPES}
    phrase_exact: dict[str, dict[Owner, None]] = {}
    phrase_entries: list[tuple[str, str, str, Owner]] = []
    n_forms = 0
    for i, e in enumerate(ents):
        owner: Owner = (i, ORIGIN_LIBRARY, "library")
        for f, c, p in e.forms:
            exact.setdefault(c, {})[owner] = None
            per_type[e.type].append((f, c, p, owner))
            n_forms += 1
        if e.type == "title" and e.artist_label:
            tf = spoken_forms(clean_title(e.label)) or spoken_forms(e.label)
            af = spoken_forms(e.artist_label)
            if tf and af:
                phrase = f"{tf[0]} {af[0]}"
                c = compact(phrase)
                e.phrase = c
                powner: Owner = (i, ORIGIN_LIBRARY, "phrase")
                phrase_exact.setdefault(c, {})[powner] = None
                phrase_entries.append((phrase, c, phonetic_key(phrase), powner))
    idx.exact = {k: tuple(v) for k, v in exact.items()}
    idx.phrase_exact = {k: tuple(v) for k, v in phrase_exact.items()}
    idx.pools = {t: Pool(v) for t, v in per_type.items() if v}
    idx.phrase_pool = Pool(phrase_entries)
    idx.n_tracks = len(rows)
    idx.n_forms = n_forms
    idx.entities = list(ents)
    idx.base_ms = (time.perf_counter() - t0) * 1000.0
    return with_aliases(idx, aliases)


def _stamp(row: Mapping[str, Any]) -> tuple[float, int]:
    ts = row.get("updated_at") or row.get("created_at")
    t = ts.timestamp() if isinstance(ts, datetime) else 0.0
    try:
        rid = int(row.get("id") or 0)
    except (TypeError, ValueError):
        rid = 0
    return (t, rid)


def with_aliases(base: Index, aliases: Sequence[Mapping[str, Any]]) -> Index:
    """A new index: ``base``'s library layer + an alias layer over
    ``aliases`` (library_aliases rows; suppressed ones are skipped). An
    alias whose target is not in the library is inert."""
    t0 = time.perf_counter()
    idx = Index()
    for name in (
        "base_entities", "by_ref", "exact", "phrase_exact", "pools", "phrase_pool",
        "rows", "albums_by_name", "n_tracks", "n_forms", "base_ms", "built_at",
    ):
        setattr(idx, name, getattr(base, name))
    n_base = len(base.base_entities)
    extra: list[Entity] = []
    track_entity: dict[int, int] = {}
    exact: dict[str, dict[Owner, None]] = {}
    entries: dict[str, list[tuple[str, str, str, Owner]]] = {}
    forms: dict[int, dict[tuple[str, str, str, int, str], None]] = {}
    household: dict[int, list[tuple[tuple[float, int], str]]] = {}
    mb: dict[int, list[tuple[tuple[float, int], str]]] = {}
    attached = inert = 0

    for a in sorted(aliases, key=_stamp):
        if a.get("suppressed"):
            continue
        text = " ".join(str(a.get("alias") or "").split())
        alias_forms = spoken_forms(text)
        if not alias_forms:
            continue
        kind = a.get("target_type")
        targets: list[int] = []
        if kind == "artist":
            key = str(a.get("target_key") or "")
            i = base.by_ref.get(("artist", key))
            if i is None:
                i = base.by_ref.get(("credit", key))
            if i is not None:
                targets = [i]
        elif kind == "album":
            key = str(a.get("target_key") or "")
            artist_key = a.get("target_artist_key")
            targets = [
                i for i in base.albums_by_name.get(key, ())
                if not artist_key or artist_key in base.base_entities[i].artist_keys
            ]
        elif kind == "track":
            try:
                tid = int(a.get("target_track_id"))
            except (TypeError, ValueError):
                tid = None
            row = base.rows.get(tid) if tid is not None else None
            if row is not None:
                i = track_entity.get(tid)
                if i is None:
                    i = n_base + len(extra)
                    track_entity[tid] = i
                    extra.append(Entity(
                        type="track", key=str(tid), label=row.title,
                        artist_label=row.primary or None, track_ids=(tid,),
                        name_key=str(tid), credited=row.credited,
                        artist_keys=frozenset({row.primary_key} if row.primary_key else ()),
                    ))
                targets = [i]
        if not targets:
            inert += 1
            continue
        attached += 1
        is_mb = a.get("source") == "musicbrainz"
        origin = ORIGIN_MB if is_mb else ORIGIN_HOUSEHOLD
        label = "musicbrainz" if is_mb else "alias"
        stamp = _stamp(a)
        for i in targets:
            etype = base.base_entities[i].type if i < n_base else "track"
            owner: Owner = (i, origin, label)
            for f in alias_forms:
                c = compact(f)
                p = phonetic_key(f) if len(c) > SHORT_KEY else ""
                exact.setdefault(c, {})[owner] = None
                entries.setdefault(etype, []).append((f, c, p, owner))
                forms.setdefault(i, {})[(f, c, p, origin, label)] = None
            (mb if is_mb else household).setdefault(i, []).append((stamp, text))

    def newest_first(d: dict[int, list[tuple[tuple[float, int], str]]]) -> dict[int, tuple[str, ...]]:
        return {
            i: tuple(dict.fromkeys(t for _, t in sorted(v, key=lambda x: x[0], reverse=True)))
            for i, v in d.items()
        }

    idx.aliases = AliasLayer(
        extra=extra,
        exact={k: tuple(v) for k, v in exact.items()},
        pools={t: Pool(v) for t, v in entries.items()},
        forms={i: tuple(v) for i, v in forms.items()},
        household=newest_first(household),
        musicbrainz=newest_first(mb),
        attached=attached,
        inert=inert,
    )
    idx.entities = base.base_entities + extra if extra else base.base_entities
    idx.alias_ms = (time.perf_counter() - t0) * 1000.0
    return idx


# ─── Scoring ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Hit:
    score: float
    origin: int
    via: str


def _candidate_pool(
    index: Index, forms: Sequence[str], kinds: frozenset[str], *, phrase: bool
) -> list[Pool]:
    """The lists a request's fuzzy and sound-alike ``forms`` are searched
    in. THE hook for candidate narrowing (Phase 5, e.g. an n-gram or
    phonetic-key block built from ``forms``): today every form of every
    allowed entity type, whatever was said."""
    pools = [index.pools[t] for t in sorted(kinds) if t in index.pools]
    pools.extend(index.aliases.pools[t] for t in sorted(kinds) if t in index.aliases.pools)
    if phrase and "title" in kinds and len(index.phrase_pool):
        pools.append(index.phrase_pool)
    return pools


def _bump(best: dict[int, tuple[float, int, str]], owners: Sequence[Owner], s: float) -> None:
    for i, origin, label in owners:
        prev = best.get(i)
        if prev is None or s > prev[0] or (s == prev[0] and origin > prev[1]):
            best[i] = (s, origin, label)


def _covers(index: Index, i: int, words: Sequence[str], *, phrase: bool) -> bool:
    """Every content word of the request appears in, or nearly in, one of
    the entity's own forms."""
    forms = index.coverage_forms(i, phrase=phrase)
    if not forms:
        return False
    for w in words:
        if any(w in f for f in forms):
            continue
        if max(fuzz.partial_ratio(w, f) for f in forms) >= COVERAGE_PARTIAL:
            continue
        return False
    return True


def _via(label: str, fuzzy: float, sound: float) -> str:
    if label == "library":
        return "fuzzy" if fuzzy >= sound else "phonetic"
    return label


def _score(
    index: Index,
    i: int,
    fuzzy: tuple[float, int, str] | None,
    sound: tuple[float, int, str] | None,
    words: Sequence[str],
    *,
    cap: float,
    phrase: bool,
) -> Hit:
    """An entity's non-exact score from its best spelling and sound
    matches: 0.6 × spelling + 0.4 × sound, kept under the play bar
    (``cap``) unless it accounts for every content word. THE hook for a
    household-plays prior (Phase 4)."""
    fs = fuzzy[0] if fuzzy else 0.0
    ps = sound[0] if sound else 0.0
    s = W_FUZZY * fs + W_PHONETIC * ps
    src = fuzzy if fuzzy is not None else sound
    assert src is not None
    if s > cap and words and not _covers(index, i, words, phrase=phrase):
        s = cap
    return Hit(s, src[1], _via(src[2], fs, ps))


def _content_words(qs: Sequence[str]) -> list[str]:
    return [w for w in min(qs, key=len).split() if w not in STOP_WORDS]


def score_reading(
    index: Index, text: str, kinds: frozenset[str], *, phrase: bool, cap: float
) -> dict[int, Hit]:
    """Score one reading of a request against every entity of ``kinds``."""
    qs = spoken_forms(text)
    if not qs or not kinds:
        return {}
    ents = index.entities
    hits: dict[int, Hit] = {}
    for q in qs:
        for i, origin, label in index.exact_owners(compact(q), phrase=phrase):
            if ents[i].type not in kinds:
                continue
            prev = hits.get(i)
            if prev is None or origin > prev.origin:
                hits[i] = Hit(1.0, origin, "exact" if label == "library" else label)
    fz: dict[int, tuple[float, int, str]] = {}
    ph: dict[int, tuple[float, int, str]] = {}
    pools = _candidate_pool(index, qs[:FUZZY_FORMS], kinds, phrase=phrase)
    for q in qs[:FUZZY_FORMS]:
        c = compact(q)
        if len(c) <= SHORT_KEY:
            continue
        p = phonetic_key(q)
        for pool in pools:
            for _, sc, j in process.extract(
                c, pool.compacts, scorer=fuzz.ratio, limit=EXTRACT_LIMIT,
                score_cutoff=FUZZY_CUTOFF,
            ):
                _bump(fz, pool.compact_owners[j], sc / 100.0)
            for _, sc, j in process.extract(
                q, pool.forms, scorer=fuzz.token_sort_ratio, limit=EXTRACT_LIMIT,
                score_cutoff=FUZZY_CUTOFF,
            ):
                _bump(fz, pool.form_owners[j], TOKEN_SORT_WEIGHT * sc / 100.0)
            if p and pool.phons:
                for _, sc, j in process.extract(
                    p, pool.phons, scorer=fuzz.ratio, limit=EXTRACT_LIMIT,
                    score_cutoff=PHONETIC_CUTOFF,
                ):
                    _bump(ph, pool.phon_owners[j], sc / 100.0)
    words = _content_words(qs)
    for i in fz.keys() | ph.keys():
        if i in hits:
            continue
        hits[i] = _score(index, i, fz.get(i), ph.get(i), words, cap=cap, phrase=phrase)
    return hits


def score_entity(index: Index, i: int, text: str, *, cap: float) -> Hit | None:
    """The same score as :func:`score_reading`, for one known entity,
    compared directly with all of its forms (a "did you mean" reply)."""
    qs = spoken_forms(text)
    if not qs:
        return None
    forms = index.all_forms(i, phrase=True)
    qc = {compact(q) for q in qs}
    exact = [(o, lbl) for _, c, _, o, lbl in forms if c in qc]
    if exact:
        origin, label = max(exact)
        return Hit(1.0, origin, "exact" if label == "library" else label)
    fz: dict[int, tuple[float, int, str]] = {}
    ph: dict[int, tuple[float, int, str]] = {}
    for q in qs[:FUZZY_FORMS]:
        c = compact(q)
        if len(c) <= SHORT_KEY:
            continue
        p = phonetic_key(q)
        for f, fc, fp, origin, label in forms:
            if len(fc) <= SHORT_KEY:
                continue
            owner = ((i, origin, label),)
            r = fuzz.ratio(c, fc, score_cutoff=FUZZY_CUTOFF)
            if r:
                _bump(fz, owner, r / 100.0)
            t = fuzz.token_sort_ratio(q, f, score_cutoff=FUZZY_CUTOFF)
            if t:
                _bump(fz, owner, TOKEN_SORT_WEIGHT * t / 100.0)
            if p and fp:
                s = fuzz.ratio(p, fp, score_cutoff=PHONETIC_CUTOFF)
                if s:
                    _bump(ph, owner, s / 100.0)
    if i not in fz and i not in ph:
        return None
    return _score(index, i, fz.get(i), ph.get(i), _content_words(qs), cap=cap, phrase=True)


def rank(index: Index, hits: Mapping[int, Hit]) -> list[tuple[int, Hit]]:
    """Best first: score, then origin (household alias > library name >
    MusicBrainz alias), then the entity with more tracks."""
    ents = index.entities
    return sorted(
        hits.items(),
        key=lambda kv: (-kv[1].score, -kv[1].origin, -len(ents[kv[0]].track_ids), kv[0]),
    )


# ─── Reading a request ────────────────────────────────────────────────────


def norm(text: str | None) -> str:
    return " ".join((text or "").lower().split()).strip().rstrip(".,!?").strip()


def leading_carrier(text: str) -> tuple[str, frozenset[str]] | None:
    """(the rest, the kinds the carrier implies) for a request led by one
    carrier phrase ("songs by …", "the album …"), else None."""
    for carrier, kinds in CARRIERS:
        if text.startswith(carrier):
            rest = text[len(carrier):].strip()
            return (rest, kinds) if rest else None
    return None


Ranked = list[tuple[int, Hit]]


def resolve_any(index: Index, text: str, kinds: frozenset[str], *, cap: float) -> tuple[Ranked, str]:
    """A free-text request: scored as said AND, when it starts with a
    carrier phrase, without it (limited to the kinds the carrier names);
    the better reading wins ("some 41" keeps its "some")."""
    t = norm(text)
    readings: list[tuple[str, frozenset[str]]] = []
    carried = leading_carrier(t)
    if carried is not None:
        rest, hint = carried
        if hint & kinds:
            readings.append((rest, hint & kinds))
    readings.append((t, kinds))
    best: tuple[Ranked, str] = ([], t)
    best_top = -1.0
    for rt, rk in readings:
        ranked = rank(index, score_reading(index, rt, rk, phrase="title" in rk, cap=cap))
        top = ranked[0][1].score if ranked else -1.0
        if top > best_top:
            best, best_top = (ranked, rt), top
    return best


def resolve_artist(
    index: Index, text: str, kinds: frozenset[str], *, play: float, ask: float, cap: float
) -> tuple[Ranked, str]:
    """An artist-only request (the tool router's ``artist``): artists and
    credits first; anything else only when that finds nothing."""
    ak = ARTIST_KINDS & kinds
    if ak:
        ranked, heard = resolve_any(index, text, ak, cap=cap)
        if ranked and ranked[0][1].score >= min(ask, play):
            return ranked, heard
    return resolve_any(index, text, kinds, cap=cap)


def resolve_by(
    index: Index, title: str, artist: str, kinds: frozenset[str], *,
    play: float, ask: float, cap: float,
) -> tuple[Ranked, str]:
    """"<X> by <Y>": X against titles (albums, when X says "the album …"),
    Y against the artists credited on each, 0.6 / 0.4; the whole phrase is
    scored too, and wins when it beats the split by more than
    :data:`BY_SPLIT_MARGIN` ("stand by me")."""
    t, a = norm(title), norm(artist)
    if t in CARRIER_TITLES:
        return resolve_artist(index, a, kinds, play=play, ask=ask, cap=cap)
    side, side_kinds = t, TITLE_KINDS
    carried = leading_carrier(t)
    if carried is not None and carried[1] in (ALBUM_KINDS, TITLE_KINDS):
        side, side_kinds = carried
    side_kinds = side_kinds & kinds
    combos: dict[int, Hit] = {}
    if side_kinds and a:
        sides = score_reading(index, side, side_kinds, phrase=False, cap=cap)
        artists = score_reading(index, a, ARTIST_KINDS, phrase=False, cap=cap)
        ents = index.entities
        for i, h in sides.items():
            if h.score < ask:
                continue
            sa = max((artists[c].score for c in ents[i].credited if c in artists), default=0.0)
            combos[i] = Hit(W_TITLE * h.score + W_ARTIST * sa, h.origin, "by_split")
    by_ranked = rank(index, combos)
    whole: tuple[Ranked, str] = ([], f"{t} by {a}")
    whole_top = -1.0
    for phrase_text in (f"{t} by {a}", f"{t} {a}"):
        ranked, heard = resolve_any(index, phrase_text, kinds, cap=cap)
        top = ranked[0][1].score if ranked else -1.0
        if top > whole_top:
            whole, whole_top = (ranked, heard), top
    if by_ranked and by_ranked[0][1].score >= whole_top - BY_SPLIT_MARGIN:
        return by_ranked, f"{side} by {a}"
    return whole


def decide(
    index: Index, ranked: Ranked, heard: str, *, play: float, ask: float
) -> Resolution:
    """play ≥ ``play``; ask in [``ask``, ``play``) with up to
    :data:`MAX_ASK` entities; else none. Two entities of one type tied at
    the top (the same title by two artists) are asked about, not picked.
    An ask bar at or above the play bar never asks."""
    if not ranked:
        return Resolution.none("no_match", heard)
    ents = index.entities
    can_ask = ask < play
    i0, h0 = ranked[0]
    if h0.score >= play:
        if can_ask:
            tied = [
                (i, h) for i, h in ranked[1:]
                if h.score == h0.score and h.origin == h0.origin
                and ents[i].type == ents[i0].type
            ]
            if tied:
                cands = [(i0, h0), *tied][:MAX_ASK]
                return Resolution(
                    decision="ask",
                    candidates=tuple(index.candidate(i, h.score, h.via) for i, h in cands),
                    heard=heard, reason="tie",
                )
        runners = [(i, h) for i, h in ranked[1:MAX_ASK] if h.score >= min(ask, play)]
        return Resolution(
            decision="play",
            candidates=tuple(
                index.candidate(i, h.score, h.via) for i, h in [(i0, h0), *runners]
            ),
            heard=heard, reason=h0.via,
        )
    if can_ask and h0.score >= ask:
        cands = [(i, h) for i, h in ranked if h.score >= ask][:MAX_ASK]
        return Resolution(
            decision="ask",
            candidates=tuple(index.candidate(i, h.score, h.via) for i, h in cands),
            heard=heard, reason=h0.via,
        )
    return Resolution.none("below_ask", heard)


def resolve(
    index: Index,
    query: Mapping[str, str],
    *,
    kinds: frozenset[str] | None,
    play_threshold: float,
    ask_threshold: float,
) -> Resolution:
    """One MusicHandler query dict → a :class:`Resolution`. The query is
    THE hook for N-best transcripts (Phase 4)."""
    allowed = ENTITY_TYPES if kinds is None else frozenset(kinds) & ENTITY_TYPES
    title = norm(query.get("title"))
    artist = norm(query.get("artist"))
    free = norm(query.get("any"))
    if not allowed:
        return Resolution.none("no_kinds", free or title or artist)
    play, ask = float(play_threshold), float(ask_threshold)
    cap = play - 0.01
    if title and artist:
        ranked, heard = resolve_by(index, title, artist, allowed, play=play, ask=ask, cap=cap)
    elif free or title:
        ranked, heard = resolve_any(index, free or title, allowed, cap=cap)
    elif artist:
        ranked, heard = resolve_artist(index, artist, allowed, play=play, ask=ask, cap=cap)
    else:
        return Resolution.none("empty_query")
    return decide(index, ranked, heard, play=play, ask=ask)


def _reply_score(index: Index, i: int, text: str, *, cap: float) -> tuple[float, float]:
    """(score, own-name score) of a reply for entity ``i``. A title, album
    or track is also named by its artist ("the Brass Lantern one" when two
    albums share a name) and by "<name> by <artist>", weighed as the
    by-split weighs them; its own name breaks a tie."""
    ents = index.entities
    own = score_entity(index, i, text, cap=cap)
    s_own = own.score if own else 0.0
    best = s_own
    e = ents[i]
    if e.type in ("title", "album", "track") and e.credited:
        s_artist = max(
            ((h.score if h else 0.0) for h in (score_entity(index, c, text, cap=cap) for c in e.credited)),
            default=0.0,
        )
        best = max(best, s_artist)
        if " by " in text:
            said, _, artist = text.rpartition(" by ")
            name = score_entity(index, i, said, cap=cap)
            if name is not None:
                sa = max(
                    ((h.score if h else 0.0) for h in (score_entity(index, c, artist, cap=cap) for c in e.credited)),
                    default=0.0,
                )
                best = max(best, W_TITLE * name.score + W_ARTIST * sa)
    return best, s_own


def match_reply(
    index: Index, text: str, idxs: Sequence[int | None], *, play: float, ask: float
) -> tuple[int, float] | None:
    """Which of the entities ``idxs`` (None = gone) a bare reply names:
    (position, score) of the best one scoring ≥ ``ask``, else None."""
    t = norm(text)
    carried = leading_carrier(t)
    readings = [t] + ([carried[0]] if carried else [])
    best: tuple[int, float, float] | None = None
    for pos, i in enumerate(idxs):
        if i is None:
            continue
        for rt in readings:
            s, own = _reply_score(index, i, rt, cap=play - 0.01)
            if s >= ask and (best is None or (s, own) > (best[1], best[2])):
                best = (pos, s, own)
    return (best[0], best[1]) if best else None


__all__ = [
    "ALBUM_KINDS",
    "ARTIST_KINDS",
    "CARRIERS",
    "CARRIER_TITLES",
    "Entity",
    "Hit",
    "Index",
    "TITLE_KINDS",
    "build",
    "decide",
    "match_reply",
    "norm",
    "rank",
    "resolve",
    "score_entity",
    "score_reading",
    "with_aliases",
]
