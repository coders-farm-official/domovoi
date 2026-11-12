"""How voice identification scored a turn, as the dashboard reads it (V049).

A satellite turn's ``conversation_log`` row keeps the identification
outcome and the closest two people with their scores
(``domovoi/voice_identifier.py``, ``VoiceMatch``). The room's and the
person's conversation lists show it as one quiet line ("voice: Alex 0.82
(next: Priya 0.61)"), and the People page lists a person's recent scores.

**Admins only.** A turn's text and who it was attributed to are read on the
device tier, but how close somebody's voice came to somebody ELSE's is
voice-biometric detail of the kind the People page already keeps for an
admin (a voice sample's claim details, delta review N4): every other caller
gets ``voice_match: null``. Before setup everything is open to the LAN, as
on the rest of the People surface.

The names are read at request time, so a rename shows at once and a person
who was forgotten simply isn't there (the columns are ON DELETE SET NULL).
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy import text

from domovoi.admin_auth import check_admin_request
from web.backend.schemas import VoiceMatchInfo

#: The SELECT tail of the V049 columns, for a query over
#: ``conversation_log`` (unaliased): 7 columns, read by :func:`from_row`.
SELECT_TAIL = (
    ", voice_outcome,"
    " voice_best_person_id,"
    " (SELECT p.name FROM people p WHERE p.id = conversation_log.voice_best_person_id),"
    " voice_best_score,"
    " voice_runner_up_person_id,"
    " (SELECT p.name FROM people p WHERE p.id = conversation_log.voice_runner_up_person_id),"
    " voice_runner_up_score"
)
SELECT_TAIL_WIDTH = 7

# Whether conversation_log has V049's columns; probed once per process (a
# dashboard started before Flyway ran keeps showing the history).
_HAS_COLUMNS: bool | None = None


async def has_columns(s: Any) -> bool:
    global _HAS_COLUMNS
    if _HAS_COLUMNS is None:
        row = (await s.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'conversation_log' AND column_name = 'voice_outcome'"
        ))).first()
        _HAS_COLUMNS = row is not None
    return _HAS_COLUMNS


def forget_probe() -> None:
    """Probe again on next use (tests)."""
    global _HAS_COLUMNS
    _HAS_COLUMNS = None


async def caller_sees_scores(request: Request | None) -> bool:
    """Whether this caller is shown voice-match details: an admin (a
    Bearer, the dashboard cookie) or anyone before setup."""
    if request is None:
        return False
    return await check_admin_request(request) in ("ok", "cookie-only", "pre-setup")


def _score(value: Any) -> float | None:
    return None if value is None else round(float(value), 3)


def from_row(tail: Any) -> VoiceMatchInfo | None:
    """The :data:`SELECT_TAIL` columns of a row as the API's object, or
    None for a row with no outcome (before V049, not a satellite turn)."""
    outcome, best_id, best_name, best_score, next_id, next_name, next_score = tail
    if not outcome:
        return None
    return VoiceMatchInfo(
        outcome=str(outcome),
        best_person_id=int(best_id) if best_id is not None else None,
        best_name=best_name,
        best_score=_score(best_score),
        runner_up_person_id=int(next_id) if next_id is not None else None,
        runner_up_name=next_name,
        runner_up_score=_score(next_score),
    )
