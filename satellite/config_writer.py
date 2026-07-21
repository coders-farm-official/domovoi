"""Comment-preserving surgical merge of edits into a satellite
``config.toml``.

The web dashboard's per-satellite Settings tab sends a flat dict of
``{"section.key": value}`` edits. ``tomllib`` can read TOML but not write
it, and a ``tomli_w`` round-trip would discard every comment in the
hand-documented config. So we edit the file as text: for each
``section.key`` we rewrite (or uncomment, or append) just that one line,
leaving every other line — comments, blank lines, unedited keys — exactly
as it was.

Pure and dependency-free so it can be unit-tested without the rest of the
satellite client.
"""

from __future__ import annotations

import re

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def _key_line_re(key: str) -> re.Pattern[str]:
    # Matches `key =` or a commented `# key =` at line start (any indent),
    # capturing the indent so we can preserve it when rewriting.
    return re.compile(r"^(\s*)#?\s*" + re.escape(key) + r"\s*=")


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value)
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def apply_changes(toml_text: str, changes: dict[str, object]) -> str:
    """Return ``toml_text`` with ``changes`` ({"section.key": value})
    applied. An existing (or commented-out) ``key`` line in its section is
    rewritten in place; a missing key is appended to its section; a missing
    section is appended at the end. Everything else is preserved verbatim."""
    by_section: dict[str, dict[str, object]] = {}
    for dotted, val in changes.items():
        section, _, key = dotted.partition(".")
        if not section or not key:
            continue
        by_section.setdefault(section, {})[key] = val

    # Split into a leading preamble block (name=None) + one block per
    # [section], each block carrying its own lines (header included).
    blocks: list[dict] = [{"name": None, "lines": []}]
    for line in toml_text.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            blocks.append({"name": m.group(1).strip(), "lines": [line]})
        else:
            blocks[-1]["lines"].append(line)

    for section, kv in by_section.items():
        block = next((b for b in blocks if b["name"] == section), None)
        if block is None:
            # Blank line before a brand-new section header for readability.
            block = {"name": section, "lines": ["", f"[{section}]"]}
            blocks.append(block)
        for key, val in kv.items():
            kre = _key_line_re(key)
            for i, line in enumerate(block["lines"]):
                m = kre.match(line)
                if m:
                    block["lines"][i] = f"{m.group(1)}{key} = {_format_value(val)}"
                    break
            else:
                block["lines"].append(f"{key} = {_format_value(val)}")

    out: list[str] = []
    for b in blocks:
        out.extend(b["lines"])
    text = "\n".join(out)
    return text + "\n" if not text.endswith("\n") else text
