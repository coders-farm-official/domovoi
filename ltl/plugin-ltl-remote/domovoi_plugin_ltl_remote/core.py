"""Core-process entry point: ``register(ctx)``.

Everything the plugin contributes to the voice process is wired here
against the injected ``PluginSDK``:

* config (``LTL_*`` FieldSpecs) — first, because the workers and the
  handler read ``sdk.config``;
* the household identity, loaded (or generated) from disk into
  ``sdk.state`` so the link worker and the HTTP router share one object
  rather than each reading the key files;
* the RemoteAccessHandler at band 265;
* the relay link (long-run) and the retention reaper (poll);
* the plugin's core HTTP router at ``/v1/plugins/ltl_remote/...``, whose
  mutations are admin-gated by default — pairing, device approval, and
  unlinking are exactly the operations that should need the admin
  password;
* a clean ``on_disable`` teardown that closes the socket instead of
  leaving the relay waiting on a timeout.

Import-time discipline: nothing here opens a socket or touches the
database at import. Key generation happens in a startup hook, so a
missing data directory fails a hook rather than the whole plugin load.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from domovoi.sdk import PluginSDK

from . import crypto, pairing, store
from .handlers import RemoteAccessHandler
from .link import RelayLink
from .local_proxy import OriginError, validate_origin
from .ltl_api import LtlApiClient, LtlApiError
from .settings import LTL_FIELDSPECS, LtlRemoteSettings
from .workers import RemoteRetentionReaper

log = logging.getLogger(__name__)


def register(ctx: Any) -> None:
    sdk: PluginSDK = ctx.sdk

    # ── Config first — everything below reads sdk.config. ───────────────
    ctx.add_config(LtlRemoteSettings, LTL_FIELDSPECS)

    # Refuse to load with an origin that would make this an open proxy.
    # A hard load failure is the right severity: a plugin that can't
    # safely decide where to forward must not be half-enabled.
    for label, value in (
        ("dashboard_origin", sdk.config.dashboard_origin),
        ("core_origin", sdk.config.core_origin),
    ):
        try:
            validate_origin(value, label=label)
        except OriginError as e:
            raise ValueError(f"ltl_remote: {e}") from None

    # ── Identity. Generated on first run; the private halves never leave
    #    ~/.domovoi/plugins/data/ltl_remote/keys/. ───────────────────────
    identity = crypto.load_or_create_identity(sdk.ensure_data_dir() / "keys")
    sdk.state["identity"] = identity
    log.info("ltl_remote: household fingerprint %s", identity.fingerprint)

    # ── Voice plane. ────────────────────────────────────────────────────
    ctx.add_handler(RemoteAccessHandler(sdk))

    # ── Background work. ────────────────────────────────────────────────
    relay = RelayLink(sdk)
    sdk.state["relay"] = relay
    ctx.add_worker(relay)
    ctx.add_worker(RemoteRetentionReaper(sdk))

    async def _publish_identity() -> None:
        """Mirror the public half of the identity into the plugin schema
        so the dashboard can render the fingerprint without reading key
        files. Runs after the DB is up; the files on disk stay
        authoritative."""
        await store.save_identity(
            sdk,
            fingerprint=identity.fingerprint,
            dh_pub=identity.dh.public_raw,
            sig_pub=identity.sig.public_raw,
        )

    ctx.add_startup_hook(
        _publish_identity, name="publish_identity", after="core.db_ready"
    )

    # ── HTTP. ───────────────────────────────────────────────────────────
    ctx.add_router(_build_core_router(sdk))

    # ── Teardown: close the link rather than let it time out. ───────────
    async def _on_disable() -> None:
        worker = sdk.state.get("relay")
        if worker is not None:
            try:
                await worker.shutdown_links()
            except Exception as e:  # noqa: BLE001 — teardown isolation
                log.warning("ltl_remote: closing the relay link failed: %s", e)

    ctx.on_disable(_on_disable)

    log.info("ltl_remote plugin registered (band 265, 2 workers, 1 hook)")


# ─── the plugin's core router (/v1/plugins/ltl_remote/...) ───────────────
#
# Every mutation below stays admin-gated (the runtime's default-DENY for
# non-GET plugin routes; nothing here calls open_endpoint). That is the
# correct default: these endpoints decide who can reach this house from
# the internet.


class DeviceAction(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=128)


def _build_core_router(sdk: PluginSDK) -> APIRouter:
    router = APIRouter()
    api = LtlApiClient(sdk)

    # NOTE: /state, not /status — the core app already serves a generic
    # GET /v1/plugins/{slug}/status (worker and hook health), and that
    # route, registered at import time, would shadow a same-path plugin
    # route mounted later.
    @router.get("/state")
    async def link_state() -> dict[str, Any]:
        """Everything the Remote Access page needs in one read."""
        state = await store.get_link_state(sdk)
        identity: crypto.Identity = sdk.state["identity"]
        return {
            "enabled": bool(sdk.config.enabled),
            "fingerprint": identity.fingerprint,
            "household_id": state.get("household_id"),
            "account_label": state.get("account_label"),
            "claimed_at": state.get("claimed_at"),
            "connection_state": state.get("connection_state"),
            "last_connected_at": state.get("last_connected_at"),
            "last_disconnected_at": state.get("last_disconnected_at"),
            "last_error": state.get("last_error"),
            "pairing_code": state.get("pairing_code"),
            "pairing_expires_at": state.get("pairing_expires_at"),
            "plan_code": state.get("plan_code"),
            "quota_used_bytes": state.get("quota_used_bytes"),
            "quota_limit_bytes": state.get("quota_limit_bytes"),
            "quota_period_end": state.get("quota_period_end"),
            "read_only": bool(sdk.config.read_only),
            "allow_core_admin": bool(sdk.config.allow_core_admin),
            "allow_media_streaming": bool(sdk.config.allow_media_streaming),
        }

    @router.get("/devices")
    async def devices(status: str | None = Query(default=None)) -> list[dict[str, Any]]:
        rows = await store.list_devices(sdk, status=status)
        # public_key is bytes and of no use to a UI; the fingerprint is
        # the part a human compares.
        for row in rows:
            row.pop("public_key", None)
        return rows

    @router.get("/access-log")
    async def access_log(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        device_id: str | None = Query(default=None),
    ) -> list[dict[str, Any]]:
        return await store.read_access_log(
            sdk, limit=limit, offset=offset, device_id=device_id
        )

    @router.post("/pairing/start")
    async def start_pairing() -> dict[str, Any]:
        """Open a pairing window: mint a code, publish its hash and our
        public keys to LTL, and show the admin the eight words."""
        if sdk.config.household_id:
            raise HTTPException(
                status_code=409,
                detail="this server is already paired; unlink it first",
            )
        identity: crypto.Identity = sdk.state["identity"]
        enrollment = pairing.new_enrollment()
        payload = enrollment.registration_payload(
            dh_pub_b64=identity.dh.public_b64,
            sig_pub_b64=identity.sig.public_b64,
            fingerprint=identity.fingerprint,
            hostname=_hostname(),
        )
        try:
            registered = await api.register_enrollment(payload)
        except LtlApiError as e:
            raise HTTPException(status_code=502, detail=str(e)) from None
        await store.start_pairing(
            sdk,
            code=enrollment.code,
            code_hash=enrollment.code_hash,
            expires_at=enrollment.expires_at,
            enrollment_id=registered["enrollment_id"],
            enrollment_token=registered["poll_token"],
        )
        return {
            "pairing_code": enrollment.code,
            "expires_at": enrollment.expires_at,
            "fingerprint": identity.fingerprint,
        }

    @router.post("/pairing/cancel")
    async def cancel_pairing() -> dict[str, bool]:
        await store.clear_pairing(sdk)
        return {"cancelled": True}

    async def _report_upstream(device_id: str, status: str) -> None:
        """Mirror a local decision to LTL, best-effort.

        Never fails the request: the approval is already committed here,
        and here is the only place it is authoritative. A relay that is
        briefly unreachable must not make the dashboard button look
        broken.
        """
        worker = sdk.state.get("relay")
        if worker is None:
            return
        try:
            await worker.report_device_state(device_id, status)
        except Exception as e:  # noqa: BLE001
            log.debug("ltl_remote: could not report %s upstream: %s", device_id, e)

    @router.post("/devices/approve")
    async def approve_device(body: DeviceAction) -> dict[str, bool]:
        """The local trust decision. Nothing LTL can say substitutes for
        this call, which is the point of the whole design."""
        if not await store.set_device_status(sdk, body.device_id, "approved"):
            raise HTTPException(status_code=404, detail="no such device")
        await _report_upstream(body.device_id, "approved")
        return {"approved": True}

    @router.post("/devices/revoke")
    async def revoke_device(body: DeviceAction) -> dict[str, bool]:
        if not await store.set_device_status(sdk, body.device_id, "revoked"):
            raise HTTPException(status_code=404, detail="no such device")
        await _report_upstream(body.device_id, "revoked")
        return {"revoked": True}

    @router.post("/token/rotate")
    async def rotate_token() -> dict[str, bool]:
        """Swap the relay token. The link drops and reconnects with the
        new one; nothing else is affected, and the household keys are
        untouched so no client has to be re-approved."""
        if not sdk.config.household_id:
            raise HTTPException(status_code=409, detail="this server is not paired")
        try:
            fresh = await api.rotate_relay_token(
                sdk.config.household_id, sdk.config.relay_token
            )
        except LtlApiError as e:
            raise HTTPException(status_code=502, detail=str(e)) from None

        from domovoi.plugins_runtime.config_bridge import PLUGIN_CONFIG

        PLUGIN_CONFIG.write_values(sdk.slug, {"relay_token": fresh})
        return {"rotated": True}

    @router.post("/unlink")
    async def unlink() -> dict[str, bool]:
        """Forget LTL entirely. Devices are revoked rather than deleted so
        the access log still resolves their labels afterwards."""
        from domovoi.plugins_runtime.config_bridge import PLUGIN_CONFIG

        PLUGIN_CONFIG.write_values(sdk.slug, {"household_id": "", "relay_token": ""})
        await store.forget_claim(sdk)
        worker = sdk.state.get("relay")
        if worker is not None:
            await worker.shutdown_links()
        return {"unlinked": True}

    return router


def _hostname() -> str:
    """A friendly name for this server, shown in the LTL web app so a
    customer with two houses can tell which one just enrolled."""
    import socket

    try:
        return socket.gethostname()[:120]
    except OSError:
        return "domovoi"
