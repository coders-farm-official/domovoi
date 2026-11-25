"""What the core's routes call (blocking: run off the event loop).

``status(body)``: the card's whole view for a selection: the release, the
boxes and their sizes, the fit check, the first refusal, the warning when
the server is behind the latest release, the job and the finished kit.
``start(body)``: refused with ``{reason, message}`` or started.
"""

from __future__ import annotations

from typing import Any

from domovoi.config import settings

from . import jobs, plan


def _behind_warning(state: plan.ReleaseState) -> dict[str, str] | None:
    if not state.behind or not state.release:
        return None
    return {"reason": "behind", "message": (
        f"Domovoi {state.latest} is out. Update this server first to put {state.latest} on the stick.")}


def _view(body: dict[str, Any] | None) -> tuple[plan.ReleaseState, plan.Catalogue | None, dict[str, Any]]:
    jobs.prune()
    state = plan.release_state(settings.repo_dir)
    out: dict[str, Any] = {"job": jobs.job_state(),
                           "builds": [jobs.public_build(b) for b in jobs.list_builds()],
                           "keep_days": jobs.KEEP_SEC // 86400}
    if state.release is None:
        out.update(refusal=state.refusal, plan=None, warning=None)
        return state, None, out
    cat = plan.catalogue(state.release, settings.repo_dir, mpd_tag=settings.mpd_image_tag or None)
    sel = plan.Selection.from_body(body)
    p = plan.plan(cat, sel, freeable=jobs.builds_bytes(), busy=jobs.busy())
    out.update(plan=p, refusal=p["refusal"], warning=_behind_warning(state),
               latest_release=state.latest)
    return state, cat, out


def status(body: dict[str, Any] | None = None) -> dict[str, Any]:
    return _view(body)[2]


def start(body: dict[str, Any] | None, core: str = "") -> tuple[bool, dict[str, Any]]:
    """(started, view). Not started: ``view["refusal"]`` says why. A
    behind-the-latest warning needs ``force: true`` (the card's "Make it
    anyway")."""
    state, cat, view = _view(body)
    if view.get("refusal"):
        return False, view
    if view.get("warning") and not (body or {}).get("force"):
        return False, {**view, "refusal": view["warning"]}
    assert cat is not None and state.release is not None
    sel = plan.Selection.from_body(body)
    ps = plan.selected(cat, sel)
    if not jobs.start(state.release, sel, ps, core=core, on_done=lambda doc: plan.invalidate()):
        return False, {**view, "refusal": {"reason": "busy", "message": "A kit is already being made."}}
    return True, {**view, "job": jobs.job_state()}
