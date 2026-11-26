"""The house identity backup (1.5.0, S3-4): the core's half.

| Route | Tier |
|---|---|
| ``POST /v1/admin/server-identity/backup`` | security + password sign-in within 5 min + the password typed again |

The answer is the backup file (:func:`domovoi.server_identity.backup_document`):
this server's PRIVATE key, which a reinstall can be given
(``install.sh --restore-identity FILE``, Windows Setup's "Restore from a
backup") so the rooms set up for this house keep trusting it. Because it
is the key, the route asks for as much as anything in Domovoi: an admin
session on the security tier (fails closed before setup), a password
sign-in within the last five minutes (a phone-grant session never has
one), and the password typed again, behind the login backoff. POST, so the
password rides in the body and nothing is cached or prefetched; the
response says ``Cache-Control: no-store``. Logged: that a backup was
downloaded, and the (public) fingerprint. Never the key, never the
password.

It is in no household-tier export, and the dashboard (web/backend) only
relays it to the admin who asked.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from domovoi import admin_auth, server_identity
from domovoi.admin_auth import require_admin_security
from domovoi.db.session import session_scope

log = logging.getLogger(__name__)

router = APIRouter()

STALE_DETAIL = (
    "sign in with the password again first: the house identity backup needs a "
    f"password sign-in within the last {admin_auth.PHONE_GRANT_FRESH_SIGNIN_SEC // 60} minutes"
)
WRONG_PASSWORD_DETAIL = "that password is not right"


class BackupBody(BaseModel):
    # A plain str, checked in the handler: a pydantic constraint would echo
    # a rejected value back in a 422 body.
    password: str = ""


@router.post(
    "/v1/admin/server-identity/backup",
    dependencies=[Depends(require_admin_security)],
)
async def identity_backup(body: BackupBody, request: Request) -> Response:
    """The house identity backup, as a file to save. See the module doc."""
    token = admin_auth.bearer_token(request)
    async with session_scope() as s:
        if not await admin_auth.signed_in_recently(s, token):
            raise HTTPException(status_code=403, detail=STALE_DETAIL)
    source = admin_auth.enforce_login_backoff(request)
    ok = False
    if 0 < len(body.password) <= 1024:
        async with session_scope() as s:
            stored = await admin_auth.get_password_hash(s)
        ok = bool(stored) and admin_auth.verify_password(stored, body.password)
    if not ok:
        admin_auth.LOGIN_BACKOFF.record_failure(source)
        log.info("house identity backup refused: wrong password")
        raise HTTPException(status_code=403, detail=WRONG_PASSWORD_DETAIL)
    admin_auth.LOGIN_BACKOFF.release(source)
    try:
        identity = server_identity.load_or_create()
    except OSError as e:
        log.warning("house identity backup: the identity can't be read (%s)", type(e).__name__)
        raise HTTPException(status_code=503, detail="this server's identity can't be read") from None
    log.warning("house identity backup downloaded by an admin (%s)", identity.fingerprint)
    return Response(
        content=server_identity.backup_bytes(identity),
        media_type="application/json",
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Content-Disposition": f'attachment; filename="{server_identity.BACKUP_FILENAME}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
