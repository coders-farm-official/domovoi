"""The short address's state and switch (web/backend/short_address.py).

``GET /api/short-address``: any paired device (the header reads it to
pick the address to show). ``PUT /api/short-address``: the admin turns it
off or on; on tries port 80 again at once.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from domovoi.admin_auth import require_admin_mutation, require_device_read
from web.backend import short_address

router = APIRouter(prefix="/api", tags=["config"])


class ShortAddressRequest(BaseModel):
    enabled: bool


@router.get("/short-address", dependencies=[Depends(require_device_read)])
async def get_short_address() -> dict:
    """``{enabled, state, port, dashboard_port, message}``. ``state``:
    ``on`` (port 80 sends browsers to the dashboard), ``off`` (switched
    off), ``taken`` (something else on this computer uses port 80; on a
    Mac install, also another program on the Mac itself),
    ``no_permission`` (the dashboard may not use a low port: an install
    whose service unit predates the feature), ``failed`` or
    ``unsupported`` (not a Linux server). ``message`` is the line
    Settings shows."""
    return short_address.SERVICE.status()


@router.put("/short-address", dependencies=[Depends(require_admin_mutation)])
async def put_short_address(body: ShortAddressRequest) -> dict:
    """Turn the short address off or on (admin). Saved in
    ``~/.domovoi/short_address.json``; applies at once. The answer is the
    read above, after the change."""
    return await short_address.SERVICE.set_enabled(body.enabled)
