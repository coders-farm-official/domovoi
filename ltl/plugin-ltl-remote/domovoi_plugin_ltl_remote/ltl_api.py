"""HTTP client for the LTL control plane (not the relay).

Three calls, all outbound, all over TLS: register an enrollment, poll it
until someone claims it, and rotate the relay token. The relay socket is
:mod:`link`'s job; this module only speaks to ``/api/v1``.

Every response is treated as untrusted input. LTL is a party this plugin
talks to, not a party it obeys — a malformed or hostile reply must fail
the call rather than end up in the database.
"""

from __future__ import annotations

from typing import Any

USER_ENROLL_PATH = "/api/v1/enroll"
TOKEN_ROTATE_PATH = "/api/v1/households/{household_id}/relay-token"


class LtlApiError(RuntimeError):
    """The LTL API refused, was unreachable, or answered with something
    we will not act on."""


def _require_str(payload: dict[str, Any], key: str, *, max_len: int = 512) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > max_len:
        raise LtlApiError(f"LTL response is missing a usable {key!r}")
    return value


class LtlApiClient:
    """Thin wrapper over ``sdk.http``, which presets the product
    user-agent and a sane timeout."""

    def __init__(self, sdk: Any) -> None:
        self._sdk = sdk

    @property
    def _base(self) -> str:
        return str(self._sdk.config.api_base).rstrip("/")

    async def _post(self, path: str, payload: dict[str, Any], *, token: str = "") -> dict:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with self._sdk.http.client() as client:
                response = await client.post(
                    self._base + path, json=payload, headers=headers
                )
        except Exception as e:  # noqa: BLE001
            raise LtlApiError(f"could not reach LTL at {self._base}: {e}") from e
        return self._unwrap(response)

    async def _get(self, path: str, *, token: str = "") -> dict:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with self._sdk.http.client() as client:
                response = await client.get(self._base + path, headers=headers)
        except Exception as e:  # noqa: BLE001
            raise LtlApiError(f"could not reach LTL at {self._base}: {e}") from e
        return self._unwrap(response)

    @staticmethod
    def _unwrap(response: Any) -> dict:
        """Unpack LTL's ``{"data": …, "error": …}`` envelope.

        The envelope is the same one the rest of the Lazy Thumb Labs API
        uses, so an error carries a code and a message worth showing the
        admin verbatim rather than a generic failure.
        """
        try:
            body = response.json()
        except Exception:  # noqa: BLE001
            raise LtlApiError(
                f"LTL answered {response.status_code} with a non-JSON body"
            ) from None
        if not isinstance(body, dict):
            raise LtlApiError("LTL answered with an unexpected body")
        error = body.get("error")
        if error:
            code = error.get("code", "ERROR") if isinstance(error, dict) else "ERROR"
            message = (
                error.get("message", "") if isinstance(error, dict) else str(error)
            )
            raise LtlApiError(f"{code}: {message}")
        if response.status_code >= 400:
            raise LtlApiError(f"LTL answered {response.status_code}")
        data = body.get("data")
        return data if isinstance(data, dict) else {}

    # ── enrollment ─────────────────────────────────────────────────────

    async def register_enrollment(self, payload: dict[str, str]) -> dict[str, str]:
        """Publish the code hash and our public keys. Returns the
        enrollment id and the token used to poll it.

        Note what is *not* in ``payload``: the pairing code itself. LTL
        receives only its hash, so the pending row is not replayable.
        """
        data = await self._post(USER_ENROLL_PATH, payload)
        return {
            "enrollment_id": _require_str(data, "enrollment_id", max_len=64),
            "poll_token": _require_str(data, "poll_token"),
        }

    async def poll_enrollment(self, enrollment_id: str, token: str) -> dict[str, Any]:
        """Ask whether someone has claimed this enrollment yet.

        Returns ``{"status": "pending"}`` or a claimed payload carrying
        the household id and relay token. Anything else is an error, so a
        truncated or reshaped response cannot half-write a claim.
        """
        data = await self._get(f"{USER_ENROLL_PATH}/{enrollment_id}", token=token)
        status = data.get("status")
        if status == "pending":
            return {"status": "pending"}
        if status != "claimed":
            raise LtlApiError(f"unexpected enrollment status {status!r}")
        return {
            "status": "claimed",
            "household_id": _require_str(data, "household_id", max_len=64),
            "relay_token": _require_str(data, "relay_token"),
            "account_label": str(data.get("account_label") or "")[:200],
        }

    # ── token rotation ─────────────────────────────────────────────────

    async def rotate_relay_token(self, household_id: str, current_token: str) -> str:
        """Ask LTL for a fresh relay token, authenticating with the
        current one. Used when an admin thinks the old one leaked."""
        data = await self._post(
            TOKEN_ROTATE_PATH.format(household_id=household_id),
            {},
            token=current_token,
        )
        return _require_str(data, "relay_token")
