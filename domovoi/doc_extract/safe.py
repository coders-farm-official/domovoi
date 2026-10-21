"""Guards for somebody else's file: zip bombs, XML entities, sizes.

* :func:`parse_xml` — XML through expat with handlers that REFUSE a
  DOCTYPE, any entity declaration, an external entity reference and a
  notation (the same refusal ``domovoi/printing/escl.py`` uses; no new
  dependency). The refusal is in the parser, so it holds whatever the
  encoding: a UTF-16 document can't walk past a byte search. With no
  DOCTYPE there are no entities to expand and nothing to fetch, so a
  billion-laughs file or an ``<!ENTITY x SYSTEM "http://...">`` is refused
  before anything is read from it.
* :class:`SafeZip` — a .docx, .odt or .xlsx is a zip. Before any member
  is read: at most :data:`MAX_MEMBERS` entries, at most
  :data:`MAX_UNZIPPED` bytes inflated in all (the Android app's
  ``XlsxSheet.MAX_UNZIPPED``), no member that inflates more than
  :data:`MAX_RATIO` times its compressed size past
  :data:`RATIO_FLOOR`, and no encrypted member. A member is read through
  a bounded stream, so a size in the zip's own directory that lies is
  caught as it inflates (Python's zipfile stops at the declared size, and
  the bound stops it here too).
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Iterator
from xml.etree import ElementTree as ET

#: The largest file the chat reads (plan part 1: a safety net, not a page
#: limit). The upload route and the worker both hold to it.
MAX_FILE_BYTES = 100 * 1024 * 1024

MAX_MEMBERS = 5000
MAX_UNZIPPED = 200 * 1024 * 1024
#: The largest single XML part read (a 1,000-page .docx is ~20 MB).
MAX_MEMBER_BYTES = 64 * 1024 * 1024
#: Office XML compresses well (10-30 times is ordinary); a bomb is
#: hundreds to millions of times. Small members aren't asked.
MAX_RATIO = 200
RATIO_FLOOR = 1024 * 1024


class Refused(Exception):
    """The file is refused; ``str()`` is a sentence for the person."""


class _EntityRefused(Exception):
    pass


def _refuse(*_args: object) -> None:
    raise _EntityRefused()


def _harden(parser: "object") -> None:
    from xml.parsers import expat

    parser.StartDoctypeDeclHandler = _refuse          # type: ignore[attr-defined]
    parser.EntityDeclHandler = _refuse                # type: ignore[attr-defined]
    parser.UnparsedEntityDeclHandler = _refuse        # type: ignore[attr-defined]
    parser.ExternalEntityRefHandler = _refuse         # type: ignore[attr-defined]
    parser.NotationDeclHandler = _refuse              # type: ignore[attr-defined]
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)  # type: ignore[attr-defined]


def parse_xml(data: bytes, what: str = "document") -> ET.Element:
    """``data`` as an element tree (namespaced tags as ``{uri}local``),
    refusing any DOCTYPE, entity or notation. Raises :class:`Refused`."""
    if len(data) > MAX_MEMBER_BYTES:
        raise Refused(f"Part of that {what} is too large to read.")
    from xml.parsers import expat

    builder = ET.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator="}")

    def tag(name: str) -> str:
        return "{" + name if "}" in name else name

    def start(name: str, attrs: dict[str, str]) -> None:
        builder.start(tag(name), {tag(k): v for k, v in attrs.items()})

    parser.StartElementHandler = start
    parser.EndElementHandler = lambda name: builder.end(tag(name))
    parser.CharacterDataHandler = builder.data
    parser.buffer_text = True
    _harden(parser)
    try:
        parser.Parse(data, True)
        root = builder.close()
    except _EntityRefused as e:
        raise Refused(f"That {what} has XML entity declarations, which Domovoi doesn't read.") from e
    except (expat.ExpatError, ValueError, AssertionError) as e:
        raise Refused(f"That {what} is damaged and couldn't be read.") from e
    if root is None:
        raise Refused(f"That {what} is damaged and couldn't be read.")
    return root


def check_xml(data: bytes, what: str = "document") -> None:
    """Raise :class:`Refused` if ``data`` declares a DOCTYPE, an entity or a
    notation, or isn't XML. Streams through expat without building a tree
    (for parts another library will read: openpyxl's sheets)."""
    from xml.parsers import expat

    parser = expat.ParserCreate()
    _harden(parser)
    try:
        parser.Parse(data, True)
    except _EntityRefused as e:
        raise Refused(f"That {what} has XML entity declarations, which Domovoi doesn't read.") from e
    except (expat.ExpatError, ValueError) as e:
        raise Refused(f"That {what} is damaged and couldn't be read.") from e


class SafeZip:
    """A zip checked before anything in it is read (see the module doc)."""

    def __init__(self, path: Path | str, what: str = "document") -> None:
        self.what = what
        try:
            self.zf = zipfile.ZipFile(str(path))
        except (zipfile.BadZipFile, OSError, ValueError) as e:
            raise Refused(f"That {what} is damaged and couldn't be read.") from e
        try:
            self._check()
        except BaseException:
            self.zf.close()
            raise

    def _check(self) -> None:
        infos = self.zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise Refused(f"That {self.what} has too many parts inside it to be read safely.")
        total = 0
        for zi in infos:
            if zi.flag_bits & 0x1:
                raise Refused(f"That {self.what} is encrypted. Save a copy without a password, then attach that.")
            total += zi.file_size
            if total > MAX_UNZIPPED:
                raise Refused(f"That {self.what} unpacks to more than "
                              f"{MAX_UNZIPPED // 2 ** 20} MB, so Domovoi won't read it.")
            if zi.file_size > RATIO_FLOOR and zi.file_size > MAX_RATIO * max(zi.compress_size, 1):
                raise Refused(f"That {self.what} looks like a zip bomb (a part inflates "
                              f"{zi.file_size // max(zi.compress_size, 1):,} times), so Domovoi won't read it.")

    def names(self) -> list[str]:
        return self.zf.namelist()

    def has(self, name: str) -> bool:
        try:
            self.zf.getinfo(name)
            return True
        except KeyError:
            return False

    def read(self, name: str, cap: int = MAX_MEMBER_BYTES) -> bytes:
        """Member ``name``, inflated through a bounded stream."""
        try:
            with self.zf.open(name) as f:
                data = f.read(cap + 1)
        except KeyError as e:
            raise Refused(f"That {self.what} is missing a part it needs.") from e
        except (zipfile.BadZipFile, OSError, EOFError, ValueError, NotImplementedError) as e:
            raise Refused(f"That {self.what} is damaged and couldn't be read.") from e
        if len(data) > cap:
            raise Refused(f"Part of that {self.what} is too large to read.")
        return data

    def xml(self, name: str) -> ET.Element:
        return parse_xml(self.read(name), self.what)

    def xml_members(self) -> Iterator[str]:
        for n in self.zf.namelist():
            if n.lower().endswith((".xml", ".rels")):
                yield n

    def close(self) -> None:
        self.zf.close()

    def __enter__(self) -> "SafeZip":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
