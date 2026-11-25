"""What ``domovoi-provisioning.service`` runs as root: root's own copy.

The unit runs ``/usr/bin/python3 -E -s -m satellite.root_entry`` with
``/usr/local/lib/domovoi/root-code`` (a link to root's current release,
satellite/root_code.py) as its working directory, so every module root
imports comes from root's copy, never from the service account's venv or
``~/domovoi`` tree. The unit names the account (``DOMOVOI_SATELLITE_USER``)
rather than a path in its home.

1. ``HOME`` becomes the account's home, as the old unit set it: the
   provisioning code reads and writes the account's ``~/.domovoi``.
2. :func:`satellite.root_code.refresh`: root brings its copy up to the
   code the client synced, verified against the root-owned pin, and keeps
   the wrapper, the unit and sudoers switched.
3. The provisioning code runs, imported fresh through the link, so a
   refresh made a moment ago is the code that runs this boot.
"""

from __future__ import annotations

import logging
import os
import runpy
import sys


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from satellite import root_code

    user = os.environ.get(root_code.USER_ENV, "")
    home = root_code.home_of(user)
    if home is not None:
        os.environ["HOME"] = str(home)
    else:
        logging.getLogger("satellite.root_entry").error(
            "%s names no account (%r); the provisioning code runs with HOME=%s",
            root_code.USER_ENV, user, os.environ.get("HOME"))
    root_code.refresh(user)
    # The working directory systemd gave us is the release the link named at
    # start; import through the link instead, so a new release is used now.
    link = str(root_code.Layout().root_code)
    here = os.getcwd()
    sys.path[:] = [link] + [p for p in sys.path if p not in ("", here, link)]
    try:
        os.chdir(link)
    except OSError:
        pass
    for name in [m for m in sys.modules if m == "satellite" or m.startswith("satellite.")]:
        if name != __name__:
            del sys.modules[name]
    runpy.run_module("satellite.provisioning_mode", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
