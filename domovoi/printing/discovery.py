"""Finding printers and scanners on the home network.

The order mirrors the satellites' ("mDNS is not load-bearing anywhere"):

1. **mDNS**, one 3-second look, group mode and legacy unicast at once
   (:mod:`domovoi.printing.mdns`): when Settings → Printers opens, on
   "Look again", and for "set up the printer".
2. **Saved printers follow their UUID**: a saved printer seen at a new
   address (DHCP) has its row updated, after the new address passes the
   LAN check.
3. **The /24 sweep**, only on "Look again" (or "look again" by voice), at
   most once a minute, never in the background (owner, Q3): TCP 631 on
   every address of the server's home /24, 32 at a time with a 300 ms
   connect timeout, then one Get-Printer-Attributes to whatever answered,
   and the eSCL capabilities on ports 80 and 443 of those hosts.
4. **A typed address** (:func:`add_typed`), held to the same LAN check.

What a look found is kept for ten minutes under opaque keys, so "Add"
names a key, never an address the client made up. The last look's
diagnostics ("found 2 printers — 2 by multicast, 0 by unicast, 0 by
sweep") are shown in Settings: that is how the owner's live install
proves multicast works from its WSL distro (P0-A).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from domovoi.printing import capabilities as caps_mod
from domovoi.printing import devices, escl, http11, ipp, lan_target, mdns, store
from domovoi.printing.client import IppClient, IppError, Printer

log = logging.getLogger(__name__)

MDNS_SECONDS = 3.0
SWEEP_MIN_INTERVAL_SEC = 60.0
SWEEP_CONCURRENCY = 32
SWEEP_CONNECT_TIMEOUT = 0.3
FOUND_TTL_SEC = 600.0


@dataclass
class Found:
    key: str
    make_and_model: str
    location: str
    address: str
    uuid: str
    via: str
    ipp: lan_target.Target | None = None
    escl: lan_target.Target | None = None
    color: bool | None = None
    duplex: bool | None = None
    at: float = field(default_factory=time.monotonic)

    def json(self, saved: dict[str, int]) -> dict[str, Any]:
        already = saved.get(self.uuid) if self.uuid else None
        if already is None:
            already = saved.get("addr:" + self.address)
        return {
            "key": self.key, "make_and_model": self.make_and_model, "location": self.location,
            "address": self.address, "prints": self.ipp is not None, "scans": self.escl is not None,
            "color": self.color, "duplex": self.duplex, "via": self.via, "already_added": already,
        }


_found: dict[str, Found] = {}
_last_sweep: float | None = None
_last_diag: dict[str, Any] | None = None
_lock = asyncio.Lock()


def last_diagnostics() -> dict[str, Any] | None:
    return _last_diag


def reset_state() -> None:
    """Tests only."""
    global _last_sweep, _last_diag
    _found.clear()
    _last_sweep = None
    _last_diag = None


def found(key: str) -> Found | None:
    f = _found.get(key)
    if f is None or time.monotonic() - f.at > FOUND_TTL_SEC:
        return None
    return f


def _truthy(v: str | None) -> bool | None:
    if v is None:
        return None
    return v.strip().upper() in ("T", "TRUE", "1", "Y", "YES")


def _from_mdns(result: mdns.BrowseResult) -> list[Found]:
    """Group services into devices by UUID, else by address."""
    devices_: dict[str, Found] = {}
    for svc in result.services:
        addr = svc.address
        if not addr or not svc.port:
            continue
        txt = svc.txt
        stype = svc.service_type
        is_print = stype in ("_ipp._tcp", "_ipps._tcp")
        scheme = {"_ipp._tcp": "ipp", "_ipps._tcp": "ipps", "_uscan._tcp": "http", "_uscans._tcp": "https"}[stype]
        if is_print:
            path = "/" + (txt.get("rp") or "ipp/print").strip("/")
        else:
            path = "/" + (txt.get("rs") or "eSCL").strip("/")
        try:
            target = lan_target.vet(scheme, addr, int(svc.port), path, allow_any_port=False)
        except lan_target.NotOnLan as e:
            log.info("printing: ignored %s at %s:%s (%s)", stype, addr, svc.port, e.reason)
            continue
        uuid = (txt.get("uuid") or "").lower().removeprefix("urn:uuid:")
        key = uuid or ("addr:" + target.address)
        dev = devices_.get(key)
        if dev is None:
            dev = Found(key="", make_and_model=(txt.get("ty") or svc.label or "Printer")[:120],
                        location=(txt.get("note") or "")[:120], address=target.address, uuid=uuid,
                        via="multicast" if "multicast" in svc.heard_on else "unicast")
            devices_[key] = dev
        if "multicast" in svc.heard_on:
            dev.via = "multicast"
        if is_print:
            # Prefer IPPS when offered (plan part 1).
            if dev.ipp is None or (scheme == "ipps" and dev.ipp.scheme == "ipp"):
                dev.ipp = target
            if txt.get("color") is not None:
                dev.color = _truthy(txt.get("color"))
            if txt.get("duplex") is not None:
                dev.duplex = _truthy(txt.get("duplex"))
        else:
            if dev.escl is None or (scheme == "https" and dev.escl.scheme == "http"):
                dev.escl = target
    return list(devices_.values())


def browse_mdns() -> mdns.BrowseResult:
    """Module-level so tests substitute it."""
    return mdns.browse(lan_target.server_lan_ipv4(), MDNS_SECONDS)


async def _tcp_open(address: str, port: int) -> bool:
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(address, port), timeout=SWEEP_CONNECT_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        return False
    w.close()
    try:
        await asyncio.wait_for(w.wait_closed(), timeout=1.0)
    except (Exception, asyncio.TimeoutError):  # noqa: BLE001
        pass
    return True


def sweep_hosts() -> list[str]:
    """Every address of the server's home /24 except its own (a wider
    home subnet is swept only in the /24 holding the server)."""
    ip = lan_target.server_lan_ipv4()
    if not ip:
        return []
    net = ipaddress.IPv4Network("%s/24" % ip, strict=False)
    own = lan_target.own_addresses()
    return [str(h) for h in net.hosts() if str(h) not in own]


async def sweep() -> list[Found]:
    hosts = sweep_hosts()
    gate = asyncio.Semaphore(SWEEP_CONCURRENCY)

    async def check(addr: str) -> str | None:
        async with gate:
            return addr if await _tcp_open(addr, 631) else None

    open_hosts = [a for a in await asyncio.gather(*(check(h) for h in hosts)) if a]
    out: list[Found] = []
    for addr in open_hosts:
        try:
            target = lan_target.vet("ipp", addr, 631, "/ipp/print")
            attrs, _cert = await devices.probe_ipp(target, timeout=4.0)
        except (lan_target.NotOnLan, devices.DeviceError, IppError, http11.HttpError) as e:
            log.debug("printing sweep: %s:631 isn't a printer (%s)", addr, e)
            continue
        caps = caps_mod.summarize(attrs)
        dev = Found(key="", make_and_model=caps["make_and_model"], location=caps["location"], address=addr,
                    uuid=caps["uuid"], via="sweep", ipp=target, color=caps["color"], duplex=caps["duplex"])
        for port, scheme in ((80, "http"), (443, "https")):
            try:
                et = lan_target.vet(scheme, addr, port, "/eSCL")
                await devices.probe_escl(et, timeout=3.0)
            except (lan_target.NotOnLan, devices.DeviceError, http11.HttpError):
                continue
            dev.escl = et
            break
        out.append(dev)
    return out


async def _answers_as(printer: Printer, uuid: str) -> bool:
    """Whether the IPP printer at ``printer`` says it is ``uuid`` (and, for
    IPPS with a pin, presents the pinned certificate: a different one
    raises before anything is sent)."""
    attrs = await IppClient(printer, timeout=4.0).get_printer_attributes(["printer-uuid"])
    said = str(ipp.first(attrs, "printer-uuid", "") or "").lower().removeprefix("urn:uuid:")
    return bool(uuid) and said == uuid


async def _scanner_answers_as(endpoint: http11.Endpoint, path: str, uuid: str) -> bool:
    resp = await http11.request(endpoint, "GET", path.rstrip("/") + "/ScannerCapabilities",
                                max_bytes=escl.MAX_XML, timeout=4.0)
    if resp.status != 200:
        return False
    return bool(uuid) and escl.parse_capabilities(resp.body).uuid == uuid


async def _follow_uuid(found_list: list[Found]) -> None:
    """A saved printer announced at a new address. Announcing a UUID proves
    nothing (any LAN host can say it), so the row moves only when:

    * the old address no longer answers as that printer, and
    * the new address (already through the LAN check, without the
      operator's allowlist) answers Get-Printer-Attributes / eSCL
      capabilities with the same UUID, over the same scheme, with the pinned
      certificate when the printer has one.

    Rows an admin added through the operator's allowlist never move. The
    move is noted for Settings ("The office printer moved to …")."""
    rows = await store.list_printers()
    by_uuid = {r["uuid"]: r for r in rows if r.get("uuid")}
    for f in found_list:
        row = by_uuid.get(f.uuid) if f.uuid else None
        if row is None or row.get("operator_allowed"):
            continue
        changes: dict[str, Any] = {}
        if f.ipp is not None and row.get("ipp_address") and row["ipp_address"] != f.ipp.address:
            if await _ipp_moved(row, f.ipp):
                changes.update(ipp_address=f.ipp.address, ipp_port=f.ipp.port)
        if f.escl is not None and row.get("escl_address") and row["escl_address"] != f.escl.address:
            if await _escl_moved(row, f.escl):
                changes.update(escl_address=f.escl.address, escl_port=f.escl.port)
        if changes:
            new_addr = changes.get("ipp_address") or changes.get("escl_address")
            if row.get("host") in (row.get("ipp_address"), row.get("escl_address")):
                changes["host"] = new_addr
            changes["moved_note"] = "%s moved to %s." % (devices.The(row["name"]), new_addr)
            log.info("printing: %s moved to %s (it proved who it is there)", row["name"], new_addr)
            await store.update_printer(row["id"], changes | {"misses": 0})
            await store.mark_moved(row["id"])


async def _ipp_moved(row: dict[str, Any], new: lan_target.Target) -> bool:
    tls = row.get("ipp_scheme") == "ipps"
    if tls and not new.tls:
        return False                       # never step down from IPPS
    try:
        if await _answers_as(devices.ipp_printer(row), row["uuid"]):
            return False                   # still there: keep it
    except (devices.DeviceError, IppError, http11.HttpError, ValueError):
        pass                               # the old address is gone
    candidate = Printer(address=new.address, port=new.port, path=row.get("ipp_path") or new.path,
                        host=new.address, tls=tls, cert_sha256=row.get("cert_sha256"))
    try:
        return await _answers_as(candidate, row["uuid"])
    except (IppError, http11.HttpError, ValueError):
        return False


async def _escl_moved(row: dict[str, Any], new: lan_target.Target) -> bool:
    tls = row.get("escl_scheme") == "https"
    if tls and not new.tls:
        return False
    path = row.get("escl_path") or "/eSCL"
    try:
        old = devices.escl_client(row)
        if await _scanner_answers_as(old.endpoint, path, row["uuid"]):
            return False
    except (devices.DeviceError, escl.EsclError, http11.HttpError, ValueError):
        pass
    ep = http11.Endpoint(address=new.address, port=new.port, tls=tls, host=new.address,
                         cert_sha256=row.get("escl_cert_sha256"))
    try:
        return await _scanner_answers_as(ep, path, row["uuid"])
    except (escl.EsclError, http11.HttpError, ValueError):
        return False


async def look(*, look_again: bool) -> dict[str, Any]:
    """One look. Returns ``{"found": [...], "diagnostics": {...}}``."""
    global _last_sweep, _last_diag
    async with _lock:
        errors: list[str] = []
        try:
            result = await asyncio.to_thread(browse_mdns)
        except Exception as e:  # noqa: BLE001 — a look never fails the page
            log.warning("printing: mDNS look failed: %s", e)
            result = mdns.BrowseResult(services=[], heard={}, errors=["mDNS: %s" % type(e).__name__])
        errors.extend(result.errors)
        found_list = _from_mdns(result)
        by_multicast = sum(1 for f in found_list if f.via == "multicast")
        by_unicast = sum(1 for f in found_list if f.via == "unicast")
        rows = await store.list_printers()
        saved_uuids = {r["uuid"] for r in rows if r.get("uuid")}
        sweep_state = "skipped"
        retry = 0
        by_sweep = 0
        if look_again:
            now = time.monotonic()
            new = [f for f in found_list if not f.uuid or f.uuid not in saved_uuids]
            if _last_sweep is not None and now - _last_sweep < SWEEP_MIN_INTERVAL_SEC:
                sweep_state = "rate_limited"
                retry = int(SWEEP_MIN_INTERVAL_SEC - (now - _last_sweep)) + 1
            elif new:
                sweep_state = "not_needed"
            else:
                _last_sweep = now
                sweep_state = "ran"
                try:
                    swept = await sweep()
                except Exception as e:  # noqa: BLE001
                    log.warning("printing: sweep failed: %s", e)
                    errors.append("sweep: %s" % type(e).__name__)
                    swept = []
                known = {f.uuid for f in found_list if f.uuid} | {f.address for f in found_list}
                for s in swept:
                    if (s.uuid and s.uuid in known) or s.address in known:
                        continue
                    found_list.append(s)
                    by_sweep += 1
        await _follow_uuid(found_list)
        for f in found_list:
            f.key = secrets.token_urlsafe(9)
            _found[f.key] = f
        for k in [k for k, v in _found.items() if time.monotonic() - v.at > FOUND_TTL_SEC]:
            _found.pop(k, None)
        saved: dict[str, int] = {}
        for r in await store.list_printers():
            if r.get("uuid"):
                saved[r["uuid"]] = r["id"]
            for a in (r.get("ipp_address"), r.get("escl_address")):
                if a:
                    saved["addr:" + a] = r["id"]
        typed = sum(1 for r in rows if r.get("added_via") == "typed")
        diag = {
            "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "found": len(found_list), "by_multicast": by_multicast, "by_unicast": by_unicast,
            "by_sweep": by_sweep, "typed": typed, "sweep": sweep_state, "sweep_retry_sec": retry,
            "errors": errors[:6],
        }
        _last_diag = diag
        log.info("printing: looked for printers: %s", {k: v for k, v in diag.items() if k != "errors"})
        return {"found": [f.json(saved) for f in found_list], "diagnostics": diag}


async def add_found(key: str, *, name: str | None, room_id: str | None, room_label: str | None) -> dict[str, Any]:
    f = found(key)
    if f is None:
        raise devices.DeviceError("That printer isn't in the last look any more. Look again.")
    return await devices.add_device(ipp_target=f.ipp, escl_target=f.escl, name=name, room_id=room_id,
                                    room_label=room_label, added_via=f.via)


async def add_typed(text: str, *, name: str | None, room_id: str | None, room_label: str | None,
                    admin: bool) -> dict[str, Any]:
    t = lan_target.parse_typed(text)
    if t.full_url:
        scheme = t.scheme or "ipp"
        port = t.port or {"ipp": 631, "ipps": 443, "http": 80, "https": 443}[scheme]
        target = lan_target.vet(scheme, t.host, port, t.path or "/", allow_any_port=admin, operator_ok=admin)
        operator = admin and lan_target.needs_operator(target)
        if scheme in ("ipp", "ipps"):
            return await devices.add_device(ipp_target=target, escl_target=None, name=name, room_id=room_id,
                                            room_label=room_label, added_via="typed", operator_allowed=operator)
        return await devices.add_device(ipp_target=None, escl_target=target, name=name, room_id=room_id,
                                        room_label=room_label, added_via="typed", operator_allowed=operator)
    # An address alone: IPP on 631 (or the port given), then IPPS on 443,
    # and eSCL on 80/443 of the same host. One row for whatever answers.
    ipp_target = escl_target = None
    ports = [t.port] if t.port else [631, 443]
    for port in ports:
        scheme = "ipps" if port == 443 else "ipp"
        try:
            cand = lan_target.vet(scheme, t.host, port, "/ipp/print", allow_any_port=admin, operator_ok=admin)
            await devices.probe_ipp(cand, timeout=5.0)
        except (devices.DeviceError, IppError, http11.HttpError):
            continue
        ipp_target = cand
        break
    for port, scheme in ((80, "http"), (443, "https")):
        if t.port and t.port != port and ipp_target is not None:
            continue
        try:
            cand = lan_target.vet(scheme, t.host, port, "/eSCL", allow_any_port=admin, operator_ok=admin)
            await devices.probe_escl(cand, timeout=4.0)
        except (devices.DeviceError, http11.HttpError):
            continue
        escl_target = cand
        break
    if ipp_target is None and escl_target is None:
        raise devices.DeviceError("Nothing at that address answered like a printer or a scanner.")
    operator = admin and any(lan_target.needs_operator(t_) for t_ in (ipp_target, escl_target) if t_ is not None)
    return await devices.add_device(ipp_target=ipp_target, escl_target=escl_target, name=name, room_id=room_id,
                                    room_label=room_label, added_via="typed", operator_allowed=operator)
