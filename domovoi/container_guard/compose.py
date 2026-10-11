"""What ``domovoi/docker-compose.yml`` says, read without a YAML library.

The core takes no YAML dependency for one file it owns, so this reads the
small subset the guard needs: each service's ``image``,
``container_name`` and ``profiles``. ``domovoi/tests/test_container_guard_expected.py``
holds this reader to ``yaml.safe_load`` on the real file, so a layout the
reader can't follow fails a test instead of silently watching nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_KEY_RE = re.compile(r"^(?P<indent> *)(?P<key>[A-Za-z0-9_.-]+):(?P<rest>.*)$")


@dataclass(frozen=True)
class ComposeService:
    service: str
    image: str | None
    container_name: str | None
    profiles: tuple[str, ...] = ()


def _scalar(raw: str) -> str:
    value = raw.strip()
    if value.startswith(("'", '"')):
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end > 0 else value[1:]
    # An unquoted scalar ends at a comment.
    hash_at = value.find(" #")
    if hash_at >= 0:
        value = value[:hash_at]
    return value.strip()


def _inline_list(raw: str) -> tuple[str, ...]:
    value = raw.strip()
    if not (value.startswith("[") and "]" in value):
        return ()
    inner = value[1:value.index("]")]
    return tuple(_scalar(part) for part in inner.split(",") if part.strip())


def parse_services(text: str) -> dict[str, ComposeService]:
    """``{service: ComposeService}`` from compose file text. Comment lines,
    blank lines and every key the guard does not read are skipped."""
    services: dict[str, ComposeService] = {}
    in_services = False
    service_indent: int | None = None
    current: str | None = None
    prop_indent: int | None = None
    fields: dict[str, object] = {}

    def flush() -> None:
        if current is not None:
            services[current] = ComposeService(
                service=current,
                image=fields.get("image") or None,          # type: ignore[arg-type]
                container_name=fields.get("container_name") or None,  # type: ignore[arg-type]
                profiles=tuple(fields.get("profiles") or ()),  # type: ignore[arg-type]
            )

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\r")
        i += 1
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _KEY_RE.match(line)
        if m is None:
            continue
        indent = len(m.group("indent"))
        key = m.group("key")
        rest = m.group("rest")
        if indent == 0:
            if in_services:
                flush()
                current = None
            in_services = key == "services"
            service_indent = None
            continue
        if not in_services:
            continue
        if service_indent is None:
            service_indent = indent
        if indent == service_indent:
            flush()
            current = key
            fields = {}
            prop_indent = None
            continue
        if current is None or indent <= service_indent:
            continue
        if prop_indent is None:
            prop_indent = indent
        if indent != prop_indent:
            # A key nested deeper (an `image:` inside `environment:`).
            continue
        if key in ("image", "container_name"):
            fields[key] = _scalar(rest)
        elif key == "profiles":
            listed = _inline_list(rest)
            if not listed and not rest.strip():
                # A block list on the following lines.
                items: list[str] = []
                while i < len(lines) and lines[i].strip().startswith("- "):
                    items.append(_scalar(lines[i].strip()[2:]))
                    i += 1
                listed = tuple(items)
            fields["profiles"] = listed
    if in_services:
        flush()
    return services


def project_name(compose_file: Path) -> str:
    """The compose project name Docker gives this file's containers when
    nothing overrides it: the directory's name, lowercased, with what a
    project name can't hold dropped (``domovoi``)."""
    raw = compose_file.resolve().parent.name.lower()
    return re.sub(r"[^a-z0-9_-]", "", raw) or "domovoi"


def read_services(compose_file: Path) -> dict[str, ComposeService]:
    """The services of ``compose_file``, or ``{}`` when it can't be read."""
    try:
        return parse_services(compose_file.read_text(encoding="utf-8"))
    except OSError:
        return {}
