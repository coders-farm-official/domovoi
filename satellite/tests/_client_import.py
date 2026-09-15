"""Import ``satellite.client`` on a box without the audio stack.

The client imports ``sounddevice`` and ``webrtcvad`` at module level, so
on a dev box without them every test that reads the module errors at
collection. An empty stand-in is enough to read code and load config; the
stand-ins are removed again straight after the import, so a later
``pytest.importorskip("sounddevice")`` elsewhere still sees the truth.
"""

from __future__ import annotations

import sys
import types


def import_client():
    stubbed = []
    for name in ("sounddevice", "webrtcvad"):
        if name in sys.modules:
            continue
        try:
            __import__(name)
        except ImportError:
            sys.modules[name] = types.ModuleType(name)
            stubbed.append(name)
    try:
        from satellite import client
    finally:
        for name in stubbed:
            sys.modules.pop(name, None)
    return client
