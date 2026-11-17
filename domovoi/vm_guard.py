"""Whether the VM fence is up, for Health.

``provision.sh --vm-guard`` (a Lima VM on a Mac; docs/LINUX_HOST.md, "ARM and
VMs") fences this machine off from its host's other services with an
nftables table that a oneshot unit, ``domovoi-vm-guard.service``, loads at
every boot. Domovoi does not refuse to start when that load fails: a box
with no fence still plays music and answers the house. Health says so
instead, so someone fixes it.

Only a box provisioned with ``--vm-guard`` is asked: the ruleset file the
option writes is what says so, and without it :func:`status` is ``None``
and Health shows nothing. The core runs as the service user, which may not
list nftables rules, so the unit's own state is the answer: it stays
``active`` after a successful load (``RemainAfterExit``) and is ``failed``
or ``inactive`` when the load did not happen. (A table flushed by hand
after a good boot is not seen from here.)
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

#: What ``--vm-guard`` writes (scripts/linux/provision.sh VM_GUARD_FILE).
RULES_FILE = Path("/etc/domovoi/vm-guard.nft")
UNIT = "domovoi-vm-guard.service"


def status() -> dict | None:
    """None when this box was not provisioned with ``--vm-guard``;
    otherwise ``{"loaded": bool | None, "unit": UNIT}`` (``loaded`` None:
    systemd could not be asked)."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        if not RULES_FILE.is_file():
            return None
    except OSError:
        return None
    try:
        proc = subprocess.run(["systemctl", "is-active", "--quiet", UNIT],
                              capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("vm guard: can't ask systemd about %s: %s", UNIT, e)
        return {"loaded": None, "unit": UNIT}
    return {"loaded": proc.returncode == 0, "unit": UNIT}
