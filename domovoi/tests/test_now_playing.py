"""Now-playing source registry (design §4.7): explicit registration,
one-stamp-per-room replace semantics, source-filtered clears, matcher
chain, owner teardown, snapshot shape."""

from __future__ import annotations

import pytest

from domovoi.now_playing import NOW_PLAYING, NowPlayingRegistry


def _registry() -> NowPlayingRegistry:
    reg = NowPlayingRegistry()
    reg.register_source("radio", owner="radio")
    reg.register_source("providerx", owner="providerx")
    return reg


def test_stamp_requires_registered_source() -> None:
    reg = NowPlayingRegistry()
    with pytest.raises(ValueError, match="not registered"):
        reg.stamp("kitchen", "radio", {"stream_url": "http://x"})


def test_stamp_replace_get_clear() -> None:
    reg = _registry()
    reg.stamp("kitchen", "radio", {"stream_url": "http://a", "title": "KEXP"})
    st = reg.get("kitchen")
    assert st is not None and st.source == "radio" and st.data["title"] == "KEXP"

    # One stamp per room — a new source replaces.
    reg.stamp("kitchen", "providerx", {"stream_url": "http://b"})
    st = reg.get("kitchen")
    assert st is not None and st.source == "providerx"

    assert reg.clear("kitchen") is True
    assert reg.get("kitchen") is None
    assert reg.clear("kitchen") is False


def test_source_filtered_clear_protects_successor() -> None:
    """A provider's own pruning (source=<its own>) must not evict a
    stamp another source placed afterwards."""
    reg = _registry()
    reg.stamp("kitchen", "providerx", {"stream_url": "http://b"})
    assert reg.clear("kitchen", source="radio") is False
    assert reg.get("kitchen") is not None
    assert reg.clear("kitchen", source="providerx") is True


def test_register_source_conflict_across_owners() -> None:
    reg = _registry()
    with pytest.raises(ValueError, match="already registered"):
        reg.register_source("radio", owner="someone_else")
    # Same owner re-registering is idempotent.
    reg.register_source("radio", owner="radio")


def test_matcher_chain_deterministic_order() -> None:
    reg = _registry()
    fn_a = lambda **kw: None  # noqa: E731
    fn_b = lambda **kw: None  # noqa: E731
    reg.register_matcher("providerx", fn_b, owner="providerx")
    reg.register_matcher("radio", fn_a, owner="radio")
    # Ascending source-slug order regardless of registration order.
    assert [slug for slug, _ in reg.matchers()] == ["providerx", "radio"]

    with pytest.raises(ValueError, match="not registered"):
        reg.register_matcher("nope", fn_a, owner="radio")


def test_unregister_owner_clears_sources_matchers_stamps() -> None:
    reg = _registry()
    reg.register_matcher("radio", lambda **kw: None, owner="radio")
    reg.stamp("kitchen", "radio", {"stream_url": "http://a"})
    reg.stamp("garage", "providerx", {"stream_url": "http://b"})

    reg.unregister_owner("radio")
    assert "radio" not in reg.sources()
    assert reg.get("kitchen") is None            # radio's stamp cleared
    assert reg.get("garage") is not None         # other owner untouched
    assert reg.matchers() == []


def test_snapshot_shape_no_elapsed_sec() -> None:
    reg = _registry()
    reg.stamp("kitchen", "radio", {"stream_url": "http://a", "title": "KEXP"})
    snap = reg.snapshot()
    assert set(snap.keys()) == {"kitchen"}
    entry = snap["kitchen"]
    assert set(entry.keys()) == {"source", "data", "stamped_at"}
    assert "elapsed_sec" not in entry["data"]    # dossier §7 inv. 8


def test_core_singleton_seeds() -> None:
    """Core-owned sources exist for core features to stamp."""
    for slug in ("library", "playlist", "spoken_audio"):
        assert slug in NOW_PLAYING.sources()
