"""The egress inventory in docs/INTERNET.md is complete.

docs/INTERNET.md, "Egress inventory", lists every internet host Domovoi's
own code can contact and which internet answer allows it. This test holds
the list to the code, so a new outbound address can't land without the
household being told:

* **Every address in the code is accounted for.** The code that runs on
  the server, the satellites, the Windows host and the Android app is
  scanned for ``http://`` / ``https://`` literals. Each host must be in a
  backticked host cell of the inventory, local by ``domovoi/egress.py``'s
  own rule (``is_local_host``: loopback/private literals, ``localhost``,
  ``.local``/``.lan``/``.home.arpa``, single-label names), a documentation
  name (RFC 2606: ``example.com``, ``.example``, ``.test``, ``.invalid``),
  or on :data:`NEVER_CONTACTED` below: addresses the code writes but never
  fetches (XML namespaces, licence and attribution links, help links shown
  to people, comments).
* **The voice handlers agree with the inventory.** The "Voice requests
  that use the internet" table must name exactly the handlers whose
  ``requires_network`` is not ``"no"`` (the core registry, plus the
  bundled radio plugin, read from its source), with the same value, and
  every host it names must be an inventory host. A handler declaring
  ``"no"`` must not write an inventory host in its module.

Hosts the code reaches without writing an address (a library's own
endpoint such as Shazam's, ``docker pull``, an Ollama registry pull, the
TCP connectivity probe) can't be found by a scan; the inventory lists them
by hand. Adding a host: add it to the inventory row it belongs to (or a new
row with the answer that allows it); a link only shown to people goes in
:data:`NEVER_CONTACTED` with a comment saying where it is shown.
"""

from __future__ import annotations

import inspect
import re
import subprocess
import sys
from pathlib import Path

import pytest

from domovoi import egress

REPO_ROOT = Path(__file__).resolve().parents[2]
INTERNET_MD = REPO_ROOT / "docs" / "INTERNET.md"
INVENTORY_HEADING = "## Egress inventory"

# What is scanned: code that runs on the server, a satellite, the Windows
# host or a phone. Developer-only tools (scripts/dev, scripts/vendor, the
# evals, release tooling) and the iPhone app (unreleased) are not.
SCAN_PREFIXES: tuple[str, ...] = (
    "domovoi/",
    "web/backend/",
    "web/static/",
    "satellite/",
    "plugins/radio/",
    "windows/",
    "android/app/src/main/",
    "scripts/linux/",
    "scripts/wake_word/",
)
SKIP_PREFIXES: tuple[str, ...] = (
    "web/static/vendor/",     # third-party bundles, vendored as built (their own tests)
    "web/static/fonts/",      # font files and their licences
    "domovoi/drafts/fonts/",  # the same
    "web/static/manual-content.json",  # generated from scripts/manual/manual.md (docs)
)
CODE_SUFFIXES = frozenset({
    ".py", ".sh", ".ps1", ".psm1", ".cs", ".iss", ".kt", ".kts", ".js", ".jsx",
    ".mjs", ".json", ".toml", ".yml", ".yaml", ".conf", ".service", ".timer",
    ".xml", ".html", ".cfg", ".ini",
})

# Hosts the code writes but never contacts. Keep each one commented with
# where it appears; a host that the code DOES fetch belongs in the
# inventory instead, never here.
NEVER_CONTACTED = frozenset({
    # XML namespaces (document formats, printing, SVG, Android resources)
    "www.w3.org",
    "schemas.openxmlformats.org",
    "schemas.microsoft.com",
    "schemas.android.com",
    "purl.org",
    "www.pwg.org",
    "schemas.hp.com",
    # Links shown to people (dashboard, app, messages), opened only by a click
    "acoustid.org",            # where to get an AcoustID key; Chromaprint's page
    "brave.com",               # where to get a Brave Search key
    "www.raspberrypi.com",     # the Raspberry Pi imager (satellite card help)
    "bztsrc.gitlab.io",        # USBImager (satellite card help)
    "aka.ms",                  # the Visual C++ runtime, named in a satellite error message
    # Licence, model-card and attribution links, and comments
    "creativecommons.org",
    "learn.microsoft.com",
    "docs.docker.com",
    "docs.nvidia.com",
    "docs.astral.sh",
    "sigsep.github.io",
    "mcdermottlab.mit.edu",
    "zenodo.org",
    "www.openslr.org",
    "www.nayuki.io",
    "brycebeattie.com",
    "codersfarm.org",          # an unused report destination, in a comment (ReplyReport.kt)
    "www.kexp.org",            # a sample station in a docstring (radio_browser.py)
    "kexp-mp3-128.streamguys1.com",  # the same sample
})

RFC2606_NAMES = frozenset({"example.com", "example.org", "example.net"})
RFC2606_SUFFIXES = (".example.com", ".example.org", ".example.net", ".example", ".test", ".invalid")

_URL = re.compile(rb"https?://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.\-]+)")
_HOST_TOKEN = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)+$")
_BACKTICKED = re.compile(r"`([^`]+)`")


# ─── the inventory ───────────────────────────────────────────────────────


def _inventory_section(text: str) -> str:
    start = text.find("\n" + INVENTORY_HEADING + "\n")
    assert start >= 0, f"docs/INTERNET.md has no '{INVENTORY_HEADING}' section"
    rest = text[start + 1 + len(INVENTORY_HEADING):]
    end = re.search(r"\n## ", rest)
    return rest[: end.start()] if end else rest


def _tables(section: str) -> list[tuple[list[str], list[list[str]]]]:
    """Each markdown table as (header cells, body rows of cells)."""
    tables: list[tuple[list[str], list[list[str]]]] = []
    current: list[list[str]] = []
    for line in section.splitlines() + [""]:
        if line.startswith("|"):
            current.append([c.strip() for c in line.strip().strip("|").split("|")])
            continue
        if current:
            header, *body = current
            body = [r for r in body if not all(set(c) <= set("-: ") for c in r)]
            tables.append((header, body))
            current = []
    return tables


def _hosts_in_cell(cell: str) -> set[str]:
    """Backticked tokens that are host names (``1.1.1.1`` included):
    ``CONNECTIVITY_PROBE_TARGET`` or ``-cloud`` are not."""
    hosts = set()
    for token in _BACKTICKED.findall(cell):
        token = token.strip().lower()
        if _HOST_TOKEN.match(token):
            hosts.add(token)
    return hosts


def _load_inventory(text: str) -> tuple[set[str], dict[str, tuple[str, set[str]]]]:
    """(every inventory host, {handler: (declared, hosts it names)})."""
    hosts: set[str] = set()
    handlers: dict[str, tuple[str, set[str]]] = {}
    for header, body in _tables(_inventory_section(text)):
        if header and header[0] == "Host":
            assert header[3:6] == ["Yes", "Sometimes", "No"], header
            for row in body:
                assert len(row) == len(header), f"inventory row has {len(row)} cells: {row}"
                assert all(row[i] for i in (3, 4, 5)), f"inventory row without an answer: {row}"
                hosts |= _hosts_in_cell(row[0])
        elif header and header[0] == "Handler":
            for row in body:
                names = _BACKTICKED.findall(row[0])
                assert names, f"handler row without a handler name: {row}"
                handlers[names[0]] = (row[1].strip(), _hosts_in_cell(row[2]))
    return hosts, handlers


@pytest.fixture(scope="module")
def inventory() -> tuple[set[str], dict[str, tuple[str, set[str]]]]:
    return _load_inventory(INTERNET_MD.read_text(encoding="utf-8"))


# ─── the scan ────────────────────────────────────────────────────────────


def _tracked() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
            capture_output=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        pytest.skip(f"not a git checkout, so no tracked-file list: {e}")
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def _in_scope(rel: str) -> bool:
    if not rel.startswith(SCAN_PREFIXES) or rel.startswith(SKIP_PREFIXES):
        return False
    parts = rel.split("/")
    if any(p in ("tests", "test", "testdata", "fixtures") for p in parts[:-1]):
        return False
    name = parts[-1]
    if name.startswith("test_") or name.endswith(("_test.py", "Test.kt")):
        return False
    suffix = Path(name).suffix.lower()
    return suffix in CODE_SUFFIXES or name.startswith("Dockerfile") or (
        suffix == "" and "/scripts/" in "/" + rel
    )


def _normalise(raw: bytes) -> str:
    host = raw.decode("ascii", "replace").lower().strip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def _needs_no_listing(host: str) -> bool:
    if not host or set(host) <= set("."):
        return True
    if host in RFC2606_NAMES or host.endswith(RFC2606_SUFFIXES):
        return True
    return egress.is_local_host(host)


def _hosts_in(data: bytes) -> set[str]:
    return {_normalise(m.group(1)) for m in _URL.finditer(data)}


def test_inventory_parses(inventory) -> None:
    hosts, handlers = inventory
    # Spot checks that the parser reads the tables it should.
    for expected in ("1.1.1.1", "musicbrainz.org", "lrclib.net", "github.com",
                     "huggingface.co", "registry.ollama.ai", "amp.shazam.com"):
        assert expected in hosts, f"{expected} missing from the parsed inventory"
    assert "double_check" in handlers
    # The documentation names of the "local" rule must never be listed.
    assert not {h for h in hosts if _needs_no_listing(h)}, "a local host is in the inventory"


def test_every_outbound_address_in_the_code_is_in_the_inventory(inventory) -> None:
    listed, _ = inventory
    assert not listed & NEVER_CONTACTED, (
        f"in the inventory AND on the never-contacted list: {sorted(listed & NEVER_CONTACTED)}"
    )
    unlisted: dict[str, list[str]] = {}
    scanned = 0
    for rel in _tracked():
        if not _in_scope(rel):
            continue
        path = REPO_ROOT / rel
        try:
            data = path.read_bytes()
        except OSError:
            continue
        scanned += 1
        for host in _hosts_in(data):
            if host in listed or host in NEVER_CONTACTED or _needs_no_listing(host):
                continue
            unlisted.setdefault(host, []).append(rel)
    assert scanned > 300, f"only {scanned} files scanned: is SCAN_PREFIXES right?"
    assert not unlisted, (
        "internet hosts in the code that docs/INTERNET.md's egress inventory doesn't list "
        "(add them to the inventory, or to NEVER_CONTACTED if the code only shows them):\n"
        + "\n".join(f"  {h}: {', '.join(sorted(set(f))[:5])}" for h, f in sorted(unlisted.items()))
    )


def test_never_contacted_entries_are_still_used() -> None:
    """A stale never-contacted entry would quietly excuse a later fetch."""
    seen: set[str] = set()
    for rel in _tracked():
        if _in_scope(rel):
            try:
                seen |= _hosts_in((REPO_ROOT / rel).read_bytes())
            except OSError:
                continue
    stale = NEVER_CONTACTED - seen
    assert not stale, f"NEVER_CONTACTED entries no scanned file uses any more: {sorted(stale)}"


# ─── the voice handlers ──────────────────────────────────────────────────

RADIO_HANDLER = REPO_ROOT / "plugins" / "radio" / "domovoi_plugin_radio" / "handlers" / "radio.py"


def _radio_declaration() -> tuple[str, str]:
    src = RADIO_HANDLER.read_text(encoding="utf-8")
    name = re.search(r'^\s+name\s*=\s*"([^"]+)"', src, re.M)
    value = re.search(r'^\s+requires_network\s*=\s*"([^"]+)"', src, re.M)
    assert name and value, "the radio plugin's handler no longer declares name / requires_network"
    return name.group(1), value.group(1)


def _network_handlers() -> dict[str, str]:
    from domovoi.handlers import HANDLERS

    declared = {h.name: h.requires_network for h in HANDLERS if h.requires_network != "no"}
    name, value = _radio_declaration()
    if value != "no":
        declared[name] = value
    return declared


def test_handler_table_matches_the_declarations(inventory) -> None:
    listed, table = inventory
    assert {k: v[0] for k, v in table.items()} == _network_handlers(), (
        "docs/INTERNET.md 'Voice requests that use the internet' must list exactly the "
        "handlers whose requires_network is not 'no', with the value they declare"
    )
    for handler, (_, hosts) in table.items():
        assert hosts <= listed, f"{handler} names hosts the inventory doesn't list: {sorted(hosts - listed)}"


def test_offline_handlers_hold_no_inventory_host(inventory) -> None:
    from domovoi.handlers import HANDLERS

    listed, _ = inventory
    offenders: dict[str, set[str]] = {}
    for h in HANDLERS:
        if h.requires_network != "no":
            continue
        module = sys.modules[type(h).__module__]
        hits = _hosts_in(inspect.getsource(module).encode()) & listed
        if hits:
            offenders[h.name] = hits
    assert not offenders, (
        "handlers declaring requires_network='no' that write an internet host from the "
        f"inventory (declare 'degraded'/'yes' with fallback_offline, or move the fetch out): {offenders}"
    )


# ─── the helpers themselves ──────────────────────────────────────────────


def test_helpers_classify_hosts() -> None:
    assert _needs_no_listing("192.168.1.20")
    assert _needs_no_listing("domovoi.local")
    assert _needs_no_listing("feeds.example.com")
    assert _needs_no_listing("ollama.test")
    assert _needs_no_listing("mpd")
    assert not _needs_no_listing("musicbrainz.org")
    assert not _needs_no_listing("8.8.8.8")
    assert _hosts_in(b'x = "https://API.Example.com/v1" y="http://[::1]:6370/"') == {"api.example.com", "::1"}
    assert _hosts_in_cell("`1.1.1.1` (TCP 443) · `huggingface.co`, `CONNECTIVITY_PROBE_TARGET`") == {
        "1.1.1.1", "huggingface.co",
    }
