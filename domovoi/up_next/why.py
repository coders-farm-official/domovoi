""""Why this song?": a pick's stored signals said in plain words.

The signals are what the picker really used (:func:`~.pick.why_signals`,
kept in ``up_next_picks.why``): first what made the song a candidate,
then the scoring factor that lifted it most, if one did. This module only
words them; it never adds a reason of its own. Lowercase chrome like the
reason chips, and never a person's name (the room queue is
household-visible).

    same artist as the last song · not played in 4 months
    from the 90s, like the song you started with · you play this often
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

_SEED = {
    "last": "the last song",
    "start": "the song you started with",
    "recent": "a song you just heard",
    "list": "this list",
    "": "this one",
}


def _seed(role: str) -> str:
    return _SEED.get(role, _SEED["recent"])


def _split(value: str) -> tuple[str, str]:
    label, _, role = (value or "").rpartition("|")
    return (label, role) if label else (value or "", "")


def _ago(days: str) -> str:
    if not days:
        return "not played in a long time"
    d = int(days)
    if d >= 60:
        return f"not played in {round(d / 30)} months"
    if d >= 28:
        return "not played in a month"
    if d >= 14:
        return f"not played in {d // 7} weeks"
    return f"not played in {d} days"


def _first(code: str, value: str) -> str | None:
    if code == "same_artist":
        return f"same artist as {_seed(value)}"
    if code == "same_era":
        label, role = _split(value)
        if label.startswith("from ") and label.endswith("-ish"):
            year = label[5:-4]
            return f"from around {year}, like {_seed(role)}"
        return f"from the {label}, like {_seed(role)}" if label else None
    if code == "same_genre":
        label, role = _split(value)
        return f"{label}, like {_seed(role)}" if label else None
    if code == "played_together":
        label, role = _split(value)
        if label.startswith("from "):
            return f"on {label[5:]} with {_seed(role)}"
        return f"played together with {_seed(role)}"
    if code == "similar_artist":
        return value.replace("similar to ", "by an artist like ", 1) if value else "by a similar artist"
    if code == "wildcard":
        return "a wildcard from your library"
    if code == "from_list":
        return value if value else "from this list"
    return None


def _factor(code: str, value: str) -> str | None:
    if code == "favorite":
        return "a favourite"
    if code == "plays_often":
        return "you play this often"
    if code == "not_played":
        return _ago(value)
    if code == "well_known":
        return "one of the artist's best-known songs"
    return None


def why_text(why: Iterable[Sequence[Any]] | None) -> str | None:
    """The plain sentence, or None when the pick has no stored signals
    (a pick made before V056)."""
    parts: list[str] = []
    for i, item in enumerate(why or ()):
        try:
            code, value = str(item[0]), str(item[1] if len(item) > 1 else "")
        except (TypeError, IndexError):
            continue
        text = _first(code, value) if i == 0 else _factor(code, value)
        if text:
            parts.append(text)
    return " · ".join(parts) or None


def factor_text(why: Iterable[Sequence[Any]] | None) -> str | None:
    """Just the lifting factor (the second signal), for a spoken answer
    that words the first one itself; None when there is none."""
    items = list(why or ())
    if len(items) < 2:
        return None
    try:
        return _factor(str(items[1][0]), str(items[1][1] if len(items[1]) > 1 else ""))
    except (TypeError, IndexError):
        return None


__all__ = ["factor_text", "why_text"]
