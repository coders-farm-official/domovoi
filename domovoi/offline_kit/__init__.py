"""The offline kit: a USB stick an online Domovoi server makes, so a house
that is never online can install Domovoi and later update it (release
1.5.0; docs/OFFLINE_KIT.md).

This package is the kit's format and its checker, nothing else:

- ``format.py``: the single source of truth for ``kit.json``, the folder
  layout, the split format for files over FAT32's 4 GiB limit and the
  kit's ``SHA256SUMS``.
- ``checker.py``: ``python -m domovoi.offline_kit verify DIR`` checks a kit
  and reports every problem it finds.
- ``sshsig.py``: checks the release's signed manifest on the stick against
  Domovoi's release key (the same key ``install.sh`` and the Windows and
  Mac programs pin).
- ``write.py``: writes a kit folder from a list of components (used by the
  server's kit builder).

Standard library only, and no import from the rest of ``domovoi``: the
checker has to run from the stick on a bare computer with nothing but
Python installed.
"""

from __future__ import annotations

from .format import KIT_FORMAT, KitProblem, part_paths, read_kit
from .checker import Finding, Report, verify
from .write import Component, FolderSink, Source, check_components, reassemble, write_kit, write_kit_to

__all__ = [
    "KIT_FORMAT",
    "Component",
    "Finding",
    "FolderSink",
    "KitProblem",
    "Report",
    "Source",
    "check_components",
    "part_paths",
    "read_kit",
    "reassemble",
    "verify",
    "write_kit",
    "write_kit_to",
]
