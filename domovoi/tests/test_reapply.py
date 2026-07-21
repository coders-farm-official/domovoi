"""Core reapply-hook registry (design §4.6) — the piece of the C6
lifespan rewrite that replaced the config endpoint's hardcoded if/elif.
Pure units (no DB / no app)."""

from __future__ import annotations

from domovoi import reapply


def _clear() -> None:
    reapply._HOOKS.clear()


def test_run_for_runs_registered_hooks_in_field_order() -> None:
    _clear()
    fired: list[str] = []
    reapply.on_reapply("tts_engine", lambda: fired.append("tts"), key="tts")
    reapply.on_reapply("log_level", lambda: fired.append("log"), key="log")

    ran = reapply.run_for(["tts_engine", "log_level"])

    assert fired == ["tts", "log"]
    assert ran == ["tts_engine:tts", "log_level:log"]


def test_shared_callback_dedupes_across_fields() -> None:
    # tts_engine + tts_speed share one reset callback — a batch touching
    # both must reset the TTS client exactly once.
    _clear()
    fired: list[str] = []

    def reset_tts_client() -> None:
        fired.append("reset")

    reapply.on_reapply("tts_engine", reset_tts_client)
    reapply.on_reapply("tts_speed", reset_tts_client)

    ran = reapply.run_for(["tts_engine", "tts_speed"])

    assert fired == ["reset"]
    assert ran == ["tts_engine:reset_tts_client"]


def test_keyed_reregistration_replaces_not_accumulates() -> None:
    # The lifespan registers core hooks on every entry (tests re-enter it
    # in one process) — same (field, key) must replace, so a write still
    # fires exactly one hook.
    _clear()
    fired: list[str] = []
    reapply.on_reapply("ollama_model", lambda: fired.append("old"), key="reset")
    reapply.on_reapply("ollama_model", lambda: fired.append("new"), key="reset")

    reapply.run_for(["ollama_model"])

    assert fired == ["new"]


def test_hook_failure_is_isolated_and_logged() -> None:
    # A raising hook must not abort the batch — the config write already
    # persisted; later hooks still run.
    _clear()
    fired: list[str] = []

    def boom() -> None:
        raise RuntimeError("subsystem poke failed")

    reapply.on_reapply("tts_engine", boom, key="boom")
    reapply.on_reapply("log_level", lambda: fired.append("log"), key="log")

    ran = reapply.run_for(["tts_engine", "log_level"])

    assert fired == ["log"]
    assert ran == ["log_level:log"]  # the failed hook isn't reported as ran


def test_unregistered_field_is_a_noop() -> None:
    _clear()
    assert reapply.run_for(["bot_name"]) == []
    assert reapply.registered_fields() == []
