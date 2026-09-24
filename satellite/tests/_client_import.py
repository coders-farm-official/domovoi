"""Stand in for the audio stack so ``satellite.client`` can be imported.

``satellite/client.py`` does ``import sounddevice`` and ``import webrtcvad``
at module scope, and neither is installed on a dev box. A test module that
imports the client therefore does not fail — it fails to be COLLECTED, and
a collection error takes the whole pytest run down with it, every other
package included. An empty stand-in is enough to read the code and load
config, which is all these tests do.

One definition of the stand-in, two ways in:

* ``install_audio_stand_ins()`` / ``remove_audio_stand_ins()`` — the pair
  ``conftest.py`` holds open across the whole collection phase, so every
  module in this package imports the client identically whether it is
  collected alone, with its neighbours, or from the repo root.
* ``import_client()`` — the same thing around a single import, for a module
  that wants the client at import time without relying on the conftest, and
  for importing it outside pytest altogether.

The stand-ins are always taken out again. That is deliberate:
``test_devices.py`` asks ``pytest.importorskip("sounddevice")`` at RUN
time, and it has to get the truth. A fake that outlived collection would
turn a documented skip into a test running against a module that does
nothing.
"""

from __future__ import annotations

import sys
import types

#: The module-scope imports in ``satellite/client.py`` that a dev box lacks.
AUDIO_MODULES = ("sounddevice", "webrtcvad")


def install_audio_stand_ins() -> list[str]:
    """Make every name in ``AUDIO_MODULES`` importable. Returns the ones
    that were faked, to hand back to ``remove_audio_stand_ins``.

    A name that is already in ``sys.modules`` is left alone, so nesting is
    safe: the inner call fakes nothing and therefore removes nothing."""
    stubbed = []
    for name in AUDIO_MODULES:
        if name in sys.modules:
            continue
        try:
            __import__(name)
        except ImportError:
            sys.modules[name] = types.ModuleType(name)
            stubbed.append(name)
    return stubbed


def remove_audio_stand_ins(stubbed) -> None:
    for name in stubbed:
        sys.modules.pop(name, None)


def import_client():
    stubbed = install_audio_stand_ins()
    try:
        from satellite import client
    finally:
        remove_audio_stand_ins(stubbed)
    return client
