"""``plugins_changed`` must fire only when something actually changed.

That NOTIFY is user-visible, not merely cache invalidation: the dashboard
turns it into a "plugins changed — reload to get the new pages" toast.
Boot-time plugin discovery re-writes each plugin's status (``loaded`` over
an already-``loaded`` row), so an unconditional NOTIFY meant EVERY core
restart told every open dashboard that its plugins had changed — observed
2026-09-01 with only the bundled radio plugin installed and nothing touched.

A toast that cries wolf on every restart is worse than no toast: people
learn to dismiss it, and then miss the one that matters.
"""

from __future__ import annotations

import pytest

from domovoi.plugins_runtime import registry as reg
from domovoi.tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.asyncio]

SLUG = "notifytest"


@pytest.fixture
async def plugin_row(db_session):
    """A plugin row in a known state, removed afterwards."""
    await reg.delete_plugin(SLUG)
    await reg.insert_plugin(
        slug=SLUG,
        name="Notify Test",
        version="1.0.0",
        publisher="tests",
        license="MIT",
        domovoi_api="1.0",
        enabled=True,
        bundled=False,
        install_source="zip",
        source_ref=None,
        install_dir="/tmp/notifytest",
        manifest={"slug": SLUG},
        status="loaded",
    )
    yield
    await reg.delete_plugin(SLUG)


@pytest.fixture
def notifies(monkeypatch):
    """Record every NOTIFY the registry emits."""
    seen: list[str] = []

    async def fake_notify(_s, slug):
        seen.append(slug)

    monkeypatch.setattr(reg, "_notify", fake_notify)
    return seen


async def test_no_op_update_does_not_notify(plugin_row, notifies):
    """Re-writing the SAME status — what boot-time discovery does — must be
    silent."""
    await reg.set_status(SLUG, "loaded", None)
    assert notifies == [], "a no-op write must not announce a change"


async def test_real_change_still_notifies(plugin_row, notifies):
    await reg.set_status(SLUG, "load_error", "boom")
    assert notifies == [SLUG]


async def test_null_transitions_are_detected(plugin_row, notifies):
    """last_error NULL -> value -> NULL. `IS DISTINCT FROM` is required here;
    a plain `!=` comparison is NULL-blind and would miss both edges."""
    await reg.update_plugin(SLUG, last_error="something")
    assert notifies == [SLUG]

    notifies.clear()
    await reg.update_plugin(SLUG, last_error="something")
    assert notifies == [], "same value again is not a change"

    notifies.clear()
    await reg.update_plugin(SLUG, last_error=None)
    assert notifies == [SLUG], "value -> NULL is a change"


async def test_partial_change_in_a_multi_field_update_notifies(plugin_row, notifies):
    """One differing field is enough, even when the others match."""
    await reg.update_plugin(SLUG, status="loaded", last_error="new")
    assert notifies == [SLUG]


async def test_enabled_toggle_notifies(plugin_row, notifies):
    await reg.set_enabled(SLUG, False)
    assert notifies == [SLUG]

    notifies.clear()
    await reg.set_enabled(SLUG, False)
    assert notifies == [], "disabling an already-disabled plugin changes nothing"


async def test_manifest_jsonb_comparison(plugin_row, notifies):
    """JSONB needs the CAST on both sides of the comparison or every write
    looks like a change."""
    await reg.update_plugin(SLUG, manifest={"slug": SLUG})
    assert notifies == [], "identical manifest is not a change"

    notifies.clear()
    await reg.update_plugin(SLUG, manifest={"slug": SLUG, "extra": 1})
    assert notifies == [SLUG]


async def test_unknown_slug_notifies_nothing(notifies):
    await reg.update_plugin("does-not-exist", status="loaded")
    assert notifies == []
