"""AssetAPI — canned sounds (design §4.13).

``add_canned_sounds`` appends entries to the core ``_CANNED`` set; the
existing idempotent per-voice sidecar-marker renderer regenerates the
clips on the next voice change / regenerate call, and they ride the
``/v1/sounds`` manifest channel to satellites unchanged. Keys are
namespaced ``<slug>_<key>`` on disk so uninstall-purge can delete a
plugin's rendered clips by prefix.

Satellite asset *channels* (the manifest+sha256 mirror pattern) are
deliberately NOT exposed in v1 beyond what core ships — both validation
plugins need zero satellite changes (design §4.13).
"""

from __future__ import annotations

from dataclasses import dataclass

from domovoi import canned_sounds


@dataclass(frozen=True)
class CannedSound:
    key: str    # [a-z0-9_]; becomes <slug>_<key>.mp3
    text: str   # what TTS renders


class AssetAPI:
    def __init__(self, slug: str) -> None:
        self.slug = slug

    def add_canned_sounds(self, sounds: list[CannedSound]) -> list[str]:
        """Register clips for rendering. Returns the on-disk clip names.
        Idempotent per key (re-registration replaces the text)."""
        names: list[str] = []
        for sound in sounds:
            stem = f"{self.slug}_{sound.key}"
            entry = (f"{stem}.mp3", f"{stem}.voice", sound.text)
            canned_sounds._CANNED[:] = [
                e for e in canned_sounds._CANNED if e[0] != entry[0]
            ]
            canned_sounds._CANNED.append(entry)
            names.append(entry[0])
        return names

    def remove_canned_sounds(self) -> int:
        """Teardown: drop every clip this plugin registered."""
        prefix = f"{self.slug}_"
        before = len(canned_sounds._CANNED)
        canned_sounds._CANNED[:] = [
            e for e in canned_sounds._CANNED if not e[0].startswith(prefix)
        ]
        return before - len(canned_sounds._CANNED)
