"""What a read returns: the file's kind, its length, its tags, its pictures.

Each container keeps its tags under its own names (ID3 frame ids, Vorbis
field names, MP4 atom names, ASF attribute names, APEv2 keys), as taggers
write them; :meth:`Tags.first` takes several names, so one call covers the
formats. Every lookup answers an empty list for a name the format can't
hold rather than raising.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass(frozen=True)
class Picture:
    """An embedded image. ``type`` is the ID3 / FLAC picture type (3 is the
    front cover, 0 "other", 4 the back cover, ...); None where the container
    has no such notion (an MP4 ``covr`` atom, a bare-base64 ``COVERART``)."""

    type: int | None
    mime: str
    desc: str
    data: bytes


class Tags:
    """One tag block, as (name, value) pairs in file order."""

    kind = "tags"

    def __init__(self, items: list[tuple[str, Any]] | None = None) -> None:
        self.items: list[tuple[str, Any]] = list(items or [])

    def _match(self, stored: str, wanted: str) -> bool:
        return stored == wanted

    def get(self, key: str) -> list[Any]:
        """Every value stored under ``key``, in file order; [] when none."""
        if not isinstance(key, str):
            return []
        return [v for k, v in self.items if self._match(k, key)]

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and any(self._match(k, key) for k, _ in self.items)

    def __iter__(self) -> Iterator[tuple[str, Any]]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __bool__(self) -> bool:
        return bool(self.items)

    def first(self, *keys: str) -> str | None:
        """The first non-blank text among ``keys``, tried in order: for each
        name present, its first value that isn't blank, stripped. The
        indexers' lookup."""
        for key in keys:
            for value in self.get(key):
                if value is None:
                    continue
                text = str(value).strip()
                if text:
                    return text
        return None


class VorbisTags(Tags):
    """Vorbis comments (FLAC, Ogg Vorbis / Opus / FLAC / Speex): field names
    compare without regard to case, values are text."""

    kind = "vorbis"

    def __init__(self, items: list[tuple[str, Any]] | None = None, vendor: str = "") -> None:
        super().__init__(items)
        self.vendor = vendor

    def _match(self, stored: str, wanted: str) -> bool:
        return stored.lower() == wanted.lower()


class MP4Tags(Tags):
    """iTunes-style ``ilst`` items: the atom name (``\\xa9nam``, ``covr``,
    ``----:mean:name``) and its values — text, bytes for pictures and
    freeform data, ints or tuples for the numeric atoms."""

    kind = "mp4"


@dataclass
class ASFValue:
    """One ASF attribute. ``type`` is the ASF data type (0 text, 1 bytes,
    2 bool, 3 dword, 4 qword, 5 word, 6 GUID)."""

    type: int
    value: Any

    def __str__(self) -> str:
        if isinstance(self.value, bytes):
            return self.value.decode("utf-16-le", "replace").rstrip("\x00")
        return str(self.value)


class ASFTags(Tags):
    """WMA / ASF attributes (``Title``, ``Author``, ``WM/AlbumTitle``,
    ``WM/Lyrics``, ``WM/Picture``), values :class:`ASFValue`. Names are
    exact, as Windows Media writes them."""

    kind = "asf"


@dataclass
class APEValue:
    """One APEv2 item. ``kind`` 0 is text (``values`` its NUL-separated
    strings), 1 binary, 2 an external link."""

    kind: int
    raw: bytes
    values: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return "\x00".join(self.values) if self.kind != 1 else ""


class APETags(Tags):
    """APEv2 items; keys compare without regard to case. ``get`` answers the
    text values of a text item (a binary item's ``APEValue`` otherwise)."""

    kind = "ape"

    def _match(self, stored: str, wanted: str) -> bool:
        return stored.lower() == wanted.lower()

    def item(self, key: str) -> APEValue | None:
        for k, v in self.items:
            if self._match(k, key):
                return v
        return None

    def get(self, key: str) -> list[Any]:
        value = self.item(key) if isinstance(key, str) else None
        if value is None:
            return []
        return list(value.values) if value.kind != 1 else [value]


@dataclass
class MediaFile:
    """A read file. ``kind`` names the container (``mp3``, ``flac``,
    ``ogg_vorbis``, ``ogg_opus``, ``ogg_flac``, ``ogg_speex``, ``mp4``,
    ``asf``, ``wav``, ``aiff``, ``aac``, ``ape``, ``id3``); ``duration`` is
    in seconds, None when the file doesn't say; ``tags`` None when it has
    none; ``pictures`` are FLAC's own PICTURE blocks (the other formats keep
    theirs in their tags)."""

    kind: str
    duration: float | None = None
    tags: Tags | None = None
    pictures: list[Picture] = field(default_factory=list)
