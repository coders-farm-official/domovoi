"""``ltl_remote`` settings — env prefix ``LTL_``.

Values persist to ``~/.domovoi/plugins/ltl_remote.env`` through the core
config bridge and render on the dashboard's Settings page from the
:data:`LTL_FIELDSPECS` rows below. OS environment variables shadow the
file, which is the documented core-wide behavior.

Two settings deserve more than a one-line help string, so they get one
here:

``read_only``
    Drops every non-GET request at the home server. Off by default,
    because a dashboard you cannot press buttons on is not much of a
    dashboard — but a household that only ever wants to *look* from
    outside should turn it on, and it is enforced before any local
    socket is opened rather than in the UI.

``allow_core_admin``
    Gates the ``/v1/**`` prefix, which reaches Domovoi's admin tier and
    therefore plugin installation, which is code execution. Turning it
    off leaves the dashboard fully usable and removes remote code
    execution from the attack surface entirely.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from domovoi.sdk import FieldSpec

# Public LTL endpoints. Overridable so a self-hosted or staging relay is
# a settings change rather than a fork.
DEFAULT_RELAY_URL = "wss://relay.lazythumblabs.com/relay/v1/agent"
DEFAULT_API_BASE = "https://api.lazythumblabs.com"


class LtlRemoteSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LTL_", env_file_encoding="utf-8", extra="ignore"
    )

    # ── Link ────────────────────────────────────────────────────────────
    enabled: bool = True
    relay_url: str = DEFAULT_RELAY_URL
    api_base: str = DEFAULT_API_BASE
    # Written by the claim flow, not by hand. Rotatable from the
    # dashboard; useless to a thief without the household signing key.
    relay_token: str = ""
    household_id: str = ""

    # ── What the tunnel may reach ───────────────────────────────────────
    # Both origins are validated to be loopback or private-range at
    # registration time (see local_proxy.validate_origin) so a typo
    # cannot turn this household's Domovoi server into an open proxy.
    dashboard_origin: str = "http://127.0.0.1:6369"
    core_origin: str = "http://127.0.0.1:6370"
    read_only: bool = False
    allow_core_admin: bool = True
    allow_media_streaming: bool = True

    # ── Limits ──────────────────────────────────────────────────────────
    max_concurrent_streams: int = 32
    max_request_body_mb: int = 32
    request_timeout_sec: float = 30.0
    stream_idle_timeout_sec: float = 300.0

    # ── Connection behavior ─────────────────────────────────────────────
    heartbeat_sec: float = 30.0
    reconnect_initial_backoff_sec: float = 2.0
    reconnect_max_backoff_sec: float = 300.0

    # ── Retention ───────────────────────────────────────────────────────
    # 0 keeps the access log forever. The reaper also expires stale
    # pending device registrations.
    access_log_retention_days: int = 30
    pending_device_ttl_hours: int = 72
    reaper_interval_sec: float = 3600.0


LTL_FIELDSPECS: list[FieldSpec] = [
    FieldSpec(
        name="enabled", label="Remote access",
        help="Hold an outbound link to the LTL relay so approved devices can reach this house.",
        group="Link", kind="bool", tier="restart",
    ),
    FieldSpec(
        name="relay_url", label="Relay URL",
        help="The relay this server dials. Change only for a staging or self-hosted relay.",
        group="Link", kind="text", tier="restart",
    ),
    FieldSpec(
        name="api_base", label="LTL API base URL",
        help="Where pairing and enrollment requests go. Must match the relay's deployment.",
        group="Link", kind="text", tier="restart",
    ),
    FieldSpec(
        name="relay_token", label="Relay token",
        help="Written by pairing. Rotate it from the Remote Access page if you think it leaked.",
        group="Link", kind="secret", tier="restart",
    ),
    FieldSpec(
        name="household_id", label="Household ID",
        help="Assigned by LTL when you claimed this server with a pairing code.",
        group="Link", kind="text", tier="restart",
    ),
    FieldSpec(
        name="read_only", label="Read-only remote access",
        help="Refuse every remote request that would change something. Look, don't touch.",
        group="Access", kind="bool",
    ),
    FieldSpec(
        name="allow_core_admin", label="Allow core API remotely",
        help="Let approved devices reach the core voice API, including admin actions "
             "such as installing plugins. Turn off to keep remote access to the dashboard only.",
        group="Access", kind="bool",
    ),
    FieldSpec(
        name="allow_media_streaming", label="Allow media streaming",
        help="Stream library audio to remote devices. This is what uses your monthly data allowance.",
        group="Access", kind="bool",
    ),
    FieldSpec(
        name="dashboard_origin", label="Dashboard origin",
        help="Local address of the Domovoi dashboard the tunnel forwards to. Must be a private address.",
        group="Access", kind="text", tier="restart",
    ),
    FieldSpec(
        name="core_origin", label="Core origin",
        help="Local address of the Domovoi core service the tunnel forwards to. Must be a private address.",
        group="Access", kind="text", tier="restart",
    ),
    FieldSpec(
        name="max_concurrent_streams", label="Max concurrent streams",
        help="Requests and tunneled sockets allowed at once across all remote devices.",
        group="Limits", kind="int",
    ),
    FieldSpec(
        name="max_request_body_mb", label="Max request body (MB)",
        help="Largest upload a remote device may send. Larger requests are refused unread.",
        group="Limits", kind="int",
    ),
    FieldSpec(
        name="request_timeout_sec", label="Request timeout (seconds)",
        help="How long to wait on the local dashboard or core before giving up on a remote request.",
        group="Limits", kind="float",
    ),
    FieldSpec(
        name="access_log_retention_days", label="Access log retention (days)",
        help="Delete remote access log rows older than this. 0 keeps them forever.",
        group="Retention", kind="int",
    ),
    FieldSpec(
        name="pending_device_ttl_hours", label="Pending device expiry (hours)",
        help="Drop device registrations nobody approved after this long.",
        group="Retention", kind="int",
    ),
]
