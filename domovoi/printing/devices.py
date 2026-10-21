"""Saved printers and scanners: reaching them, adding them, their status.

Every connection re-checks the stored address against the LAN rules
(:func:`domovoi.printing.lan_target.check_address`) — the home subnet can
change under a saved row — and carries the certificate pin. A device is
only ever saved after it answered like one: an IPP Get-Printer-Attributes
with a ``printer-uuid``, or eSCL capabilities XML. So "add a printer"
can't be used to poke a router's admin page and keep a record of it.

A changed IPPS/https certificate stops printing to that device until an
admin trusts the new one (Q14): a changed certificate is what someone in
the middle would cause, and firmware updates and factory resets cause it
too, which the message says.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from domovoi.printing import capabilities as caps_mod
from domovoi.printing import escl, http11, ipp, lan_target, options, store
from domovoi.printing.client import IppClient, IppError, Printer

log = logging.getLogger(__name__)

CERT_MESSAGE = (
    "{the}'s security certificate changed. An admin needs to check it. "
    "Firmware updates and factory resets also cause this."
)
STATUS_FRESH_SEC = 50.0
OFFLINE_AFTER_MISSES = 2


class DeviceError(Exception):
    """A sentence for people."""


def the(name: str) -> str:
    n = name or "printer"
    return n if n.lower().startswith("the ") else "the " + n


def The(name: str) -> str:
    t = the(name)
    return t[0].upper() + t[1:]


# ─── Endpoints for saved rows ────────────────────────────────────────────


def ipp_printer(row: dict[str, Any]) -> Printer:
    if not row.get("ipp_address"):
        raise DeviceError("%s doesn't print." % The(row["name"]))
    if row.get("cert_seen_sha256"):
        raise DeviceError(CERT_MESSAGE.format(the=The(row["name"])))
    try:
        lan_target.check_address(row["ipp_address"], int(row["ipp_port"]), host=row.get("host"))
    except lan_target.NotOnLan as e:
        raise DeviceError("%s's address isn't on the home network any more. %s" % (The(row["name"]), e.reason)) from e
    return Printer(
        address=row["ipp_address"], port=int(row["ipp_port"]), path=row["ipp_path"] or "/ipp/print",
        host=row.get("host") or row["ipp_address"], tls=row.get("ipp_scheme") == "ipps",
        cert_sha256=row.get("cert_sha256"),
    )


def escl_client(row: dict[str, Any]) -> escl.EsclClient:
    if not row.get("escl_address"):
        raise DeviceError("%s doesn't scan." % The(row["name"]))
    if row.get("cert_seen_sha256"):
        raise DeviceError(CERT_MESSAGE.format(the=The(row["name"])))
    try:
        lan_target.check_address(row["escl_address"], int(row["escl_port"]), host=row.get("host"))
    except lan_target.NotOnLan as e:
        raise DeviceError("%s's address isn't on the home network any more. %s" % (The(row["name"]), e.reason)) from e
    ep = http11.Endpoint(
        address=row["escl_address"], port=int(row["escl_port"]), tls=row.get("escl_scheme") == "https",
        host=row.get("host") or row["escl_address"], cert_sha256=row.get("escl_cert_sha256"),
    )
    return escl.EsclClient(ep, row.get("escl_path") or "/eSCL")


async def note_cert_change(row: dict[str, Any], seen: str) -> None:
    await store.update_printer(row["id"], {"cert_seen_sha256": seen})
    log.warning("printing: %s presented a different TLS certificate; printing to it is paused until an "
                "admin trusts it", row.get("name"))


# ─── Probing (adding) ────────────────────────────────────────────────────


async def probe_ipp(target: lan_target.Target, *, timeout: float = 8.0) -> tuple[dict[str, list[Any]], str | None]:
    printer = Printer(address=target.address, port=target.port, path=target.path, host=target.host,
                      tls=target.tls)
    client = IppClient(printer, timeout=timeout)
    attrs = await client.get_printer_attributes()
    uuid = str(ipp.first(attrs, "printer-uuid", "") or "")
    if not uuid:
        raise DeviceError("Nothing at that address answered like a printer or a scanner.")
    return attrs, client.last_cert_sha256


async def probe_escl(target: lan_target.Target, *, timeout: float = 8.0) -> tuple[escl.Capabilities, str | None]:
    ep = http11.Endpoint(address=target.address, port=target.port, tls=target.tls, host=target.host)
    resp = await http11.request(ep, "GET", target.path.rstrip("/") + "/ScannerCapabilities",
                                max_bytes=escl.MAX_XML, timeout=timeout)
    if resp.status != 200:
        raise DeviceError("Nothing at that address answered like a printer or a scanner.")
    try:
        return escl.parse_capabilities(resp.body), resp.cert_sha256
    except escl.EsclError as e:
        raise DeviceError("Nothing at that address answered like a printer or a scanner.") from e


async def add_device(
    *, ipp_target: lan_target.Target | None, escl_target: lan_target.Target | None,
    name: str | None, room_id: str | None, room_label: str | None, added_via: str,
) -> dict[str, Any]:
    """Probe what's there, then save ONE row (both badges for an MFP)."""
    attrs = None
    cert = escl_cert = None
    scan_caps = None
    errors = []
    if ipp_target is not None:
        try:
            attrs, cert = await probe_ipp(ipp_target)
        except (DeviceError, IppError, http11.HttpError) as e:
            errors.append(e)
            ipp_target = None
    if escl_target is not None:
        try:
            scan_caps, escl_cert = await probe_escl(escl_target)
        except (DeviceError, http11.HttpError) as e:
            errors.append(e)
            escl_target = None
    if attrs is None and scan_caps is None:
        raise DeviceError("Nothing at that address answered like a printer or a scanner.")
    caps = caps_mod.summarize(attrs) if attrs is not None else {}
    uuid = caps.get("uuid") or (scan_caps.uuid if scan_caps else "")
    existing = await store.find_by_uuid(uuid) if uuid else None
    if existing is not None:
        raise DeviceError("That printer is already added, as %s." % existing["name"])
    model = caps.get("make_and_model") or (scan_caps.make_and_model if scan_caps else "") or "Printer"
    default_name = ("%s printer" % room_label) if room_label else model
    final_name = clean_name(name) or clean_name(default_name) or "printer"
    host = (ipp_target or escl_target).host  # type: ignore[union-attr]
    values: dict[str, Any] = {
        "name": final_name, "uuid": uuid or None, "make_and_model": model[:120],
        "location": (caps.get("location") or "")[:120], "host": host[:253],
        "capabilities": caps, "added_via": added_via,
    }
    if ipp_target is not None:
        values.update(ipp_scheme=ipp_target.scheme, ipp_address=ipp_target.address,
                      ipp_port=ipp_target.port, ipp_path=ipp_target.path, cert_sha256=cert)
    if escl_target is not None and scan_caps is not None:
        values.update(escl_scheme=escl_target.scheme, escl_address=escl_target.address,
                      escl_port=escl_target.port, escl_path=escl_target.path,
                      escl_cert_sha256=escl_cert, scan_capabilities=scan_caps.to_json())
    if attrs is not None:
        values["status"] = caps_mod.status_of(attrs, final_name) | {
            "supplies": caps_mod.supplies_of(attrs), "checked_at": time.time()}
    pid = await store.insert_printer(values)
    if room_id:
        await store.add_room(pid, room_id)
    row = await store.get_printer(pid)
    assert row is not None
    return row


def clean_name(name: str | None) -> str:
    s = " ".join(str(name or "").split())
    s = "".join(ch for ch in s if ch >= " " and ch != "\x7f")
    return s[:60]


# ─── Status ──────────────────────────────────────────────────────────────

_refresh_locks: dict[int, asyncio.Lock] = {}


async def refresh_status(row: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """Re-read a printer's status (``force`` or when older than
    :data:`STATUS_FRESH_SEC`) and store it. Returns the stored status."""
    status = row.get("status") or {}
    fresh = isinstance(status.get("checked_at"), (int, float)) and time.time() - status["checked_at"] < STATUS_FRESH_SEC
    if (fresh and not force) or not row.get("ipp_address"):
        return status
    lock = _refresh_locks.setdefault(row["id"], asyncio.Lock())
    async with lock:
        misses = int(row.get("misses") or 0)
        try:
            printer = ipp_printer(row)
            client = IppClient(printer, timeout=8.0)
            attrs = await client.get_printer_attributes(caps_mod_status_attrs())
        except http11.CertificateChanged as e:
            await note_cert_change(row, e.seen)
            new = {"state": "needs attention", "tone": "err",
                   "words": [CERT_MESSAGE.format(the=The(row["name"]))], "message": ""}
            new.update(supplies=status.get("supplies") or [], checked_at=time.time())
            await store.set_status(row["id"], new, misses=misses)
            return new
        except DeviceError as e:
            new = {"state": "needs attention", "tone": "err", "words": [str(e)], "message": "",
                   "supplies": status.get("supplies") or [], "checked_at": time.time()}
            await store.set_status(row["id"], new, misses=misses)
            return new
        except (http11.HttpError, IppError) as e:
            misses += 1
            log.debug("printing: %s didn't answer (%s)", row.get("name"), e)
            if misses >= OFFLINE_AFTER_MISSES:
                new = caps_mod.status_of(None, row["name"], offline=True)
            else:
                new = dict(status) if status else {"state": "checking", "tone": "warn", "words": [], "message": ""}
            new.update(supplies=status.get("supplies") or [], checked_at=time.time())
            await store.set_status(row["id"], new, misses=misses)
            return new
        new = caps_mod.status_of(attrs, row["name"])
        new.update(supplies=caps_mod.supplies_of(attrs), checked_at=time.time(),
                   supplies_reported=bool(attrs.get("marker-levels") or attrs.get("printer-supply")))
        await store.set_status(row["id"], new, misses=0)
        return new


def caps_mod_status_attrs() -> list[str]:
    from domovoi.printing.client import PRINTER_ATTRS_STATUS

    return list(PRINTER_ATTRS_STATUS)


# ─── API rows ────────────────────────────────────────────────────────────


def printer_json(row: dict[str, Any], *, limits: dict[str, int], active_job: dict | None = None) -> dict[str, Any]:
    caps = row.get("capabilities") or {}
    status = row.get("status") or {}
    can_print = bool(row.get("ipp_address"))
    supplies = status.get("supplies") or []
    choices = options.choices(caps, limits["max_copies"]) if can_print else None
    scan = None
    sc = row.get("scan_capabilities")
    if row.get("escl_address") and sc:
        modes = set()
        res: set[int] = set()
        for key in ("platen", "adf"):
            c = sc.get(key) or {}
            for m in c.get("color_modes") or []:
                modes.add("color" if m.startswith("RGB") else "grey")
            res.update(c.get("resolutions") or [])
        sources = []
        if sc.get("platen"):
            sources.append("platen")
        if sc.get("adf"):
            sources.append("adf")
            if sc.get("adf_duplex"):
                sources.append("adf_duplex")
        scan = {"sources": sources, "color": sorted(modes) or ["color"], "resolutions": sorted(res)}
    needs = CERT_MESSAGE.format(the=The(row["name"])) if row.get("cert_seen_sha256") else None
    st = {
        "state": status.get("state") or ("ready" if can_print else "ready"),
        "tone": status.get("tone") or "ok",
        "words": list(status.get("words") or []),
        "message": status.get("message") or "",
        "checked_at": _iso_epoch(status.get("checked_at")),
    }
    if needs:
        st.update(state="needs attention", tone="err")
        if needs not in st["words"]:
            st["words"].insert(0, needs)
    return {
        "id": row["id"],
        "name": row["name"],
        "make_and_model": row.get("make_and_model") or "",
        "location": row.get("location") or "",
        "address": row.get("ipp_address") or row.get("escl_address") or "",
        "uri": _uri(row),
        "added_via": row.get("added_via") or "typed",
        "can_print": can_print,
        "can_scan": bool(row.get("escl_address")),
        "color": bool(caps.get("color")),
        "duplex": bool(caps.get("duplex")),
        "is_default": bool(row.get("is_default")),
        "rooms": sorted(row.get("rooms") or []),
        "two_sided_default": row.get("two_sided_default"),
        "color_default": row.get("color_default"),
        "defaults": options.defaults(caps, two_sided=row.get("two_sided_default"),
                                     color=row.get("color_default")) if can_print else None,
        "choices": choices,
        "status": st,
        "supplies": supplies,
        "supplies_reported": bool(status.get("supplies_reported", bool(supplies))),
        "admin_url": caps.get("admin_url") or None,
        "needs_attention": needs,
        "identify": 0x003C in (caps.get("operations") or []),
        "scan": scan,
        "active_job": active_job,
    }


def _uri(row: dict[str, Any]) -> str:
    if row.get("ipp_address"):
        p = Printer(address=row["ipp_address"], port=int(row["ipp_port"]), path=row["ipp_path"] or "/ipp/print",
                    host=row.get("host") or row["ipp_address"], tls=row.get("ipp_scheme") == "ipps")
        return p.uri
    scheme = row.get("escl_scheme") or "http"
    return "%s://%s:%s%s" % (scheme, row.get("host") or row.get("escl_address"), row.get("escl_port"),
                             row.get("escl_path") or "/eSCL")


def _iso_epoch(v: Any) -> str | None:
    if not isinstance(v, (int, float)):
        return None
    import datetime as dt

    return dt.datetime.fromtimestamp(v, dt.timezone.utc).isoformat().replace("+00:00", "Z")


async def pick_printer(printer_id: int | None, room_id: str | None, *, need: str = "print") -> dict[str, Any]:
    """The printer to use: the one named, else the room's default, else
    the household default, else the only one. DeviceError otherwise."""
    rows = await store.list_printers()
    usable = [r for r in rows if (r.get("ipp_address") if need == "print" else r.get("escl_address"))]
    if printer_id is not None:
        for r in usable:
            if r["id"] == printer_id:
                return r
        raise DeviceError("That printer isn't set up." if need == "print" else "That scanner isn't set up.")
    if not usable:
        raise DeviceError("No printer is set up." if need == "print" else "There's no scanner set up.")
    if room_id:
        for r in usable:
            if room_id in (r.get("rooms") or []):
                return r
    for r in usable:
        if r.get("is_default"):
            return r
    if len(usable) == 1:
        return usable[0]
    raise AmbiguousPrinter([r["name"] for r in usable])


class AmbiguousPrinter(DeviceError):
    def __init__(self, names: list[str]) -> None:
        super().__init__("Which printer: %s?" % " or ".join(names[:4]))
        self.names = names
