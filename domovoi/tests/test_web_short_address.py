"""The short address (web/backend/short_address.py): port 80 hands a
browser over to the dashboard on 6369, and does nothing else.

* The redirect keeps the path and query, keeps the host, and only for this
  server's own name or address: never an open redirect. GET and HEAD are
  redirected; every other method is a 405.
* On by default only when port 80 is free: a taken port leaves it off
  (``taken``), quietly, and Settings says so.
* The admin's switch (``PUT /api/short-address``, admin tier) frees the
  port and takes it again, and is kept across restarts.

DB-free. The listener tests bind a free loopback port, never port 80.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from web.backend import short_address as sa
from web.backend.short_address import ShortAddress, answer, host_allowed, parse_host, server_names

NAMES = server_names(["domovoi", "kitchen-box"])
LAN_IP = "192.168.1.20"


def ask(request: str, *, local_ip: str = LAN_IP, names=NAMES, target_port: int = 6369):
    data, status = answer(request.encode("ascii"), local_ip=local_ip, names=names, target_port=target_port)
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("ascii").split("\r\n")
    headers = {}
    for line in lines[1:]:
        k, _, v = line.partition(": ")
        headers[k.lower()] = v
    return status, headers, body.decode("utf-8")


def get(target: str, host: str | None, method: str = "GET", version: str = "HTTP/1.1") -> str:
    req = f"{method} {target} {version}\r\n"
    if host is not None:
        req += f"Host: {host}\r\n"
    return req + "User-Agent: test\r\n\r\n"


# ─── the redirect ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("target", ["/", "/music", "/x?y=1&z=%20a", "/#frag-is-never-sent", "/a/b/c?q=domovoi.local:80"])
def test_the_path_and_query_are_kept(target: str) -> None:
    status, h, body = ask(get(target, "domovoi.local"))
    assert status == 302
    assert h["location"] == f"http://domovoi.local:6369{target}"
    assert h["cache-control"] == "no-store"
    assert h["connection"] == "close"
    assert body == ""


@pytest.mark.parametrize("host,where", [
    ("domovoi.local", "domovoi.local"),
    ("DOMOVOI.LOCAL", "domovoi.local"),
    ("domovoi.local.", "domovoi.local"),
    ("domovoi.local:80", "domovoi.local"),
    ("domovoi", "domovoi"),
    ("kitchen-box.local", "kitchen-box.local"),
    (LAN_IP, LAN_IP),
    (f"{LAN_IP}:80", LAN_IP),
])
def test_this_servers_own_names_and_address_are_redirected(host: str, where: str) -> None:
    status, h, _ = ask(get("/p?q=1", host))
    assert status == 302
    assert h["location"] == f"http://{where}:6369/p?q=1"


def test_the_dashboard_port_is_the_one_configured() -> None:
    status, h, _ = ask(get("/", "domovoi.local"), target_port=6390)
    assert status == 302 and h["location"] == "http://domovoi.local:6390/"


def test_loopback_on_the_server_itself() -> None:
    for host in ("localhost", "127.0.0.1", "localhost:80"):
        status, h, _ = ask(get("/", host), local_ip="127.0.0.1")
        assert status == 302, host
        assert h["location"].startswith("http://") and h["location"].endswith(":6369/")
    # From the LAN, "localhost" would be the visitor's own machine.
    status, _, _ = ask(get("/", "localhost"))
    assert status == 400


def test_ipv6_address_of_the_connection() -> None:
    status, h, _ = ask(get("/x", "[fe80::20]"), local_ip="fe80::20")
    assert status == 302 and h["location"] == "http://[fe80::20]:6369/x"
    status, _, _ = ask(get("/x", "[fe80::21]"), local_ip="fe80::20")
    assert status == 400


@pytest.mark.parametrize("host", [
    "evil.example",
    "domovoi.local.evil.example",
    "evil.example.domovoi",
    "192.168.1.21",            # another machine on the LAN
    "8.8.8.8",
    "domovoi.local@evil.example",
    "evil.example/domovoi.local",
    "domovoi.local:6369",      # not the port this listener is on
    "domovoi.local:abc",
    "domovoi.local:80:80",
    "[::1",
    "",
    " ",
    "do_movoi.local",
    "-domovoi.local",
])
def test_never_an_open_redirect_hosts(host: str) -> None:
    status, h, body = ask(get("/", host))
    assert status == 400
    assert "location" not in h
    # It says where this server answers instead: its own address only.
    assert f"http://{LAN_IP}:6369/" in body
    assert "evil" not in body


@pytest.mark.parametrize("target", [
    "http://evil.example/",       # absolute-form
    "https://domovoi.local/",
    "evil.example",
    "*",
    "",
    "/\x7f",
    "/" + "a" * 5000,
])
def test_never_an_open_redirect_targets(target: str) -> None:
    status, h, _ = ask(get(target, "domovoi.local"))
    assert status == 400
    assert "location" not in h


def test_a_path_that_looks_like_a_host_stays_a_path() -> None:
    """``//evil.example`` after ``host:6369`` is a path on this server."""
    for target in ("//evil.example", "/\\evil.example", "/@evil.example"):
        status, h, _ = ask(get(target, "domovoi.local"))
        assert status == 302
        assert h["location"] == "http://domovoi.local:6369" + target
        assert urlsplit(h["location"]).netloc == "domovoi.local:6369"


def test_no_host_or_two_hosts_is_refused() -> None:
    assert ask(get("/", None, version="HTTP/1.0"))[0] == 400
    two = "GET / HTTP/1.1\r\nHost: domovoi.local\r\nHost: evil.example\r\n\r\n"
    assert ask(two)[0] == 400


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE", "PROPFIND"])
def test_only_get_and_head_are_redirected(method: str) -> None:
    status, h, body = ask(get("/x", "domovoi.local", method=method))
    assert status == 405
    assert h["allow"] == "GET, HEAD"
    assert "location" not in h
    assert body


def test_head_gets_the_redirect_without_a_body() -> None:
    data, status = answer(get("/x?y=1", "domovoi.local", method="HEAD").encode(),
                          local_ip=LAN_IP, names=NAMES, target_port=6369)
    assert status == 302
    assert data.endswith(b"\r\n\r\n")
    assert b"Location: http://domovoi.local:6369/x?y=1\r\n" in data
    data, status = answer(get("/", "evil.example", method="HEAD").encode(),
                          local_ip=LAN_IP, names=NAMES, target_port=6369)
    assert status == 400 and data.endswith(b"\r\n\r\n")


@pytest.mark.parametrize("raw", [
    "GET / HTTP/2.0\r\nHost: domovoi.local\r\n\r\n",
    "GET /\r\n\r\n",
    "GET  / HTTP/1.1\r\nHost: domovoi.local\r\n\r\n",
    "GET / HTTP/1.1\r\nHost domovoi.local\r\n\r\n",
    "GET / HTTP/1.1\r\n folded: header\r\nHost: domovoi.local\r\n\r\n",
    "G(T / HTTP/1.1\r\nHost: domovoi.local\r\n\r\n",
])
def test_malformed_requests_are_400(raw: str) -> None:
    assert ask(raw)[0] == 400


def test_a_non_ascii_head_is_400() -> None:
    data, status = answer("GET /é HTTP/1.1\r\nHost: domovoi.local\r\n\r\n".encode("utf-8"),
                          local_ip=LAN_IP, names=NAMES, target_port=6369)
    assert status == 400 and b"Location" not in data


def test_parse_host_and_host_allowed() -> None:
    assert parse_host("Domovoi.Local:80") == ("domovoi.local", 80)
    assert parse_host("[::1]:80") == ("::1", 80)
    assert parse_host("[fe80::1%25eth0]") is None
    assert parse_host("1.2.3") is None or parse_host("1.2.3")[0] == "1.2.3"
    assert host_allowed("domovoi.local", LAN_IP, NAMES)
    assert not host_allowed("domovoi.local", LAN_IP, frozenset())
    assert not host_allowed(LAN_IP, None, NAMES)


def test_server_names() -> None:
    names = server_names(["Domovoi2", "kitchen.local", None, "bad_name"], ["box.lan"])
    assert {"domovoi2", "domovoi2.local", "kitchen", "kitchen.local", "box.lan"} <= names
    assert "bad_name" not in names and "bad_name.local" not in names


# ─── the switch, on disk ──────────────────────────────────────────────────


def test_the_switch_is_on_until_set_and_kept(tmp_path) -> None:
    f = tmp_path / "short_address.json"
    assert sa.read_enabled(f) is True
    sa.write_enabled(False, f)
    assert sa.read_enabled(f) is False
    assert json.loads(f.read_text()) == {"v": 1, "enabled": False}
    sa.write_enabled(True, f)
    assert sa.read_enabled(f) is True
    f.write_text("not json")
    assert sa.read_enabled(f) is True
    f.write_text('{"enabled": "no"}')
    assert sa.read_enabled(f) is True


def test_the_switch_file_lives_in_dot_domovoi() -> None:
    assert sa.state_path().parent.name == ".domovoi"
    assert sa.state_path().name == "short_address.json"


# ─── the listener: probe, taken, switch ───────────────────────────────────


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _svc(tmp_path, port: int) -> ShortAddress:
    svc = ShortAddress(port=port, state_file=tmp_path / "short_address.json", is_supported=lambda: True)
    svc.fetch_lan_name = None
    return svc


async def _fetch(port: int, request: str) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request.encode("ascii"))
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    return data


@pytest.mark.asyncio
async def test_a_free_port_is_taken_and_redirects(tmp_path) -> None:
    port = _free_port()
    svc = _svc(tmp_path, port)
    await svc.start(host="127.0.0.1", target_port=6369)
    try:
        assert svc.status()["state"] == "on"
        assert svc.status()["enabled"] is True
        data = await _fetch(port, f"GET /music?a=1 HTTP/1.1\r\nHost: localhost:{port}\r\n\r\n")
        # The Host names this listener's port, which here isn't 80.
        assert data.startswith(b"HTTP/1.1 302 Found\r\n")
        assert b"\r\nLocation: http://localhost:6369/music?a=1\r\n" in data
        data = await _fetch(port, "POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 3\r\n\r\nabc")
        assert data.startswith(b"HTTP/1.1 405 ")
        data = await _fetch(port, "GET / HTTP/1.1\r\nHost: evil.example\r\n\r\n")
        assert data.startswith(b"HTTP/1.1 400 ") and b"Location" not in data
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_a_huge_head_is_refused(tmp_path) -> None:
    port = _free_port()
    svc = _svc(tmp_path, port)
    await svc.start(host="127.0.0.1")
    try:
        data = await _fetch(port, "GET / HTTP/1.1\r\nX-Big: " + "a" * 20000 + "\r\n\r\n")
        assert data.startswith(b"HTTP/1.1 431 ") or data == b""
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_a_port_something_else_holds_stays_off_quietly(tmp_path, caplog) -> None:
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        svc = _svc(tmp_path, port)
        with caplog.at_level("INFO"):
            await svc.start(host="127.0.0.1")
        st = svc.status()
        assert st["state"] == "taken"
        assert st["enabled"] is True
        assert st["message"] == f"Short address off: something else on this computer uses port {port}."
        assert not [r for r in caplog.records if r.name == sa.__name__ and r.levelname in ("WARNING", "ERROR")]
        # Freed later: switching it on again tries once more.
        holder.close()
        assert (await svc.set_enabled(True))["state"] == "on"
        await svc.stop()
    finally:
        holder.close()


def test_the_message_for_port_80_reads_as_the_owner_asked() -> None:
    svc = ShortAddress(port=80, is_supported=lambda: True)
    svc.state = "taken"
    assert svc.status()["message"] == "Short address off: something else on this computer uses port 80."


@pytest.mark.asyncio
async def test_no_permission_for_a_low_port(tmp_path, monkeypatch) -> None:
    async def refuse(*a, **kw):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(sa.asyncio, "start_server", refuse)
    svc = _svc(tmp_path, 80)
    await svc.start()
    assert svc.status()["state"] == "no_permission"


@pytest.mark.asyncio
async def test_not_a_linux_server_is_unsupported(tmp_path) -> None:
    svc = ShortAddress(port=_free_port(), state_file=tmp_path / "s.json", is_supported=lambda: False)
    await svc.start(host="127.0.0.1")
    assert svc.status()["state"] == "unsupported"
    assert svc.started is False


@pytest.mark.asyncio
async def test_the_switch_frees_and_takes_the_port(tmp_path) -> None:
    port = _free_port()
    svc = _svc(tmp_path, port)
    await svc.start(host="127.0.0.1")
    try:
        assert svc.state == "on"
        st = await svc.set_enabled(False)
        assert st["state"] == "off" and st["enabled"] is False
        # Freed: anything may bind it now.
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))
        # Kept across a restart: a new process starts with it off.
        again = _svc(tmp_path, port)
        await again.start(host="127.0.0.1")
        assert again.state == "off"
        await again.stop()
        st = await svc.set_enabled(True)
        assert st["state"] == "on" and st["enabled"] is True
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_the_cores_network_name_is_accepted(tmp_path) -> None:
    port = _free_port()
    svc = _svc(tmp_path, port)

    async def lan():
        return {"requested": "kitchen", "claimed": "kitchen2", "state": "announced",
                "addresses": ["192.168.1.30", "not an address"]}

    svc.fetch_lan_name = lan
    await svc.start(host="127.0.0.1")
    try:
        await svc._refresh_names()
        assert {"kitchen2.local", "kitchen.local", "kitchen2"} <= svc.names()
        data = await _fetch(port, "GET /x HTTP/1.1\r\nHost: kitchen2.local\r\n\r\n")
        assert b"\r\nLocation: http://kitchen2.local:6369/x\r\n" in data
        # The address the core reports for this server counts as its own
        # (the Mac install: the LAN reaches the Mac's address, redirected
        # into the VM). Any other address still doesn't.
        data = await _fetch(port, "GET /x HTTP/1.1\r\nHost: 192.168.1.30\r\n\r\n")
        assert b"\r\nLocation: http://192.168.1.30:6369/x\r\n" in data
        data = await _fetch(port, "GET /x HTTP/1.1\r\nHost: 192.168.1.31\r\n\r\n")
        assert data.startswith(b"HTTP/1.1 400 ")
        assert "not an address" not in svc.names()
    finally:
        await svc.stop()


def test_trusted_hosts_exact_names_count_patterns_dont(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sa, "configured_hosts", lambda: ("home.example.net", "*.example.org"))
    svc = _svc(tmp_path, _free_port())
    svc.set_lan_labels([])
    names = set(svc.names())
    assert {"home.example.net"} <= names
    assert not any(n == "example.org" or n.endswith(".example.org") for n in names)


def test_children_dont_inherit_the_capability_call_is_harmless() -> None:
    # Off Linux a no-op; on Linux the prctl either clears the ambient set
    # or (an old kernel) fails: never raises.
    assert sa.keep_bind_capability_from_children() in (True, False)


def test_a_failed_capability_clear_is_logged(monkeypatch, caplog) -> None:
    import ctypes

    class _Libc:
        def prctl(self, *a):
            return -1

    monkeypatch.setattr(sa.sys, "platform", "linux")
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: _Libc())
    with caplog.at_level("WARNING", logger=sa.log.name):
        assert sa.keep_bind_capability_from_children() is False
    assert "ambient capability" in caplog.text


# ─── the HTTP routes ──────────────────────────────────────────────────────


def _deps(app, path, method):
    from domovoi.tests.route_walk import iter_route_contexts

    for r in iter_route_contexts(app.routes):
        if getattr(r, "path", None) == path and method in (getattr(r, "methods", None) or set()):
            return {d.call.__name__ for d in r.dependant.dependencies}
    raise AssertionError(f"no {method} {path}")


def test_read_by_devices_switched_by_admins() -> None:
    from web.backend.main import app as web_app

    assert "require_device_read" in _deps(web_app, "/api/short-address", "GET")
    assert "require_admin_mutation" in _deps(web_app, "/api/short-address", "PUT")


@pytest.mark.asyncio
async def test_the_switch_over_http_is_admin_only(tmp_path, monkeypatch) -> None:
    from domovoi.tests.auth_testkit import bearer, install_fake_db, web_client

    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="household")
    svc = _svc(tmp_path, _free_port())
    monkeypatch.setattr(sa, "SERVICE", svc)
    await svc.start(host="127.0.0.1")
    try:
        async with web_client() as c:
            r = await c.get("/api/short-address", headers={"X-Device-Token": "household"})
            assert r.status_code == 200
            body = r.json()
            assert body["state"] == "on" and body["enabled"] is True
            assert body["dashboard_port"] == 6369 and "message" in body
            # A paired device can read it but not switch it.
            r = await c.put("/api/short-address", json={"enabled": False},
                            headers={"X-Device-Token": "household", "X-Requested-With": "t"})
            assert r.status_code in (401, 403)
            assert svc.state == "on"
            r = await c.put("/api/short-address", json={"enabled": False},
                            headers={**bearer("admin-token"), "X-Requested-With": "t"})
            assert r.status_code == 200
            assert r.json()["state"] == "off" and r.json()["enabled"] is False
            r = await c.put("/api/short-address", json={"enabled": "maybe"},
                            headers={**bearer("admin-token"), "X-Requested-With": "t"})
            assert r.status_code == 422
            r = await c.get("/api/short-address")
            assert r.status_code == 401
    finally:
        await svc.stop()


# ─── a Mac install: the Mac's own port 80 decides ─────────────────────────


def _mac_report(tmp_path, doc) -> Path:
    folder = tmp_path / "domovoi-mac"
    folder.mkdir(exist_ok=True)
    f = folder / "short_address.json"
    f.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return f


def _mac_svc(tmp_path, report) -> ShortAddress:
    svc = ShortAddress(port=80, state_file=tmp_path / "s.json", is_supported=lambda: True, host_report=report)
    svc.state = "on"  # listening in the VM
    return svc


def test_a_mac_whose_port_80_is_another_programs_says_taken(tmp_path, monkeypatch) -> None:
    """The VM's bind worked, but the Mac's helper leaves 80 out of its
    redirect while something on the Mac serves it: the header must not
    offer domovoi.local, which would reach that program."""
    monkeypatch.setattr(sa, "_root_only", lambda st: True)
    rep = _mac_report(tmp_path, {"schema": 1, "state": "taken", "holder": "httpd", "written_at": "x"})
    st = _mac_svc(tmp_path, rep).status()
    assert st["state"] == "taken"
    assert st["message"] == "Short address off: something else on this Mac uses port 80 (httpd)."
    rep.write_text(json.dumps({"schema": 1, "state": "taken", "holder": None}), encoding="utf-8")
    assert _mac_svc(tmp_path, rep).status()["message"] == "Short address off: something else on this Mac uses port 80."


def test_a_mac_that_passes_80_on_leaves_the_vms_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sa, "_root_only", lambda st: True)
    rep = _mac_report(tmp_path, {"schema": 1, "state": "on", "holder": None})
    assert _mac_svc(tmp_path, rep).status()["state"] == "on"
    # Its word only matters while the VM listens: off stays off.
    rep.write_text(json.dumps({"schema": 1, "state": "taken", "holder": "httpd"}), encoding="utf-8")
    svc = _mac_svc(tmp_path, rep)
    svc.state = "off"
    assert svc.status()["state"] == "off"


def test_a_mac_that_couldnt_check_says_failed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sa, "_root_only", lambda st: True)
    rep = _mac_report(tmp_path, {"schema": 1, "state": "unchecked", "holder": None})
    assert _mac_svc(tmp_path, rep).status()["state"] == "failed"


@pytest.mark.parametrize("doc", [
    "not json",
    {"schema": 2, "state": "taken"},
    {"schema": 1, "state": "stolen"},
    ["taken"],
])
def test_a_report_that_isnt_the_schema_is_ignored(tmp_path, monkeypatch, doc) -> None:
    monkeypatch.setattr(sa, "_root_only", lambda st: True)
    assert _mac_svc(tmp_path, _mac_report(tmp_path, doc)).status()["state"] == "on"


def test_a_report_that_isnt_roots_alone_is_ignored(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sa, "_root_only", lambda st: False)
    rep = _mac_report(tmp_path, {"schema": 1, "state": "taken", "holder": "httpd"})
    assert sa.read_host_report(rep) is None
    assert _mac_svc(tmp_path, rep).status()["state"] == "on"


def test_the_holder_is_shown_only_when_plain(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sa, "_root_only", lambda st: True)
    rep = _mac_report(tmp_path, {"schema": 1, "state": "taken", "holder": "<b>x</b>"})
    assert sa.read_host_report(rep) == {"state": "taken", "holder": None}
    assert sa.read_host_report(tmp_path / "missing.json") is None
    big = _mac_report(tmp_path, {"schema": 1, "state": "taken", "holder": "a", "pad": "x" * 2000})
    assert sa.read_host_report(big) is None


def test_the_report_is_where_the_mac_helper_writes_it() -> None:
    swift = (Path(sa.__file__).resolve().parents[2] / "mac" / "Sources" / "DomovoiMacKit" / "HostFiles.swift").read_text(
        encoding="utf-8")
    assert 'shortAddressFile = folder + "/short_address.json"' in swift
    assert 'folder = "/var/lib/domovoi-mac"' in swift
    assert sa.HOST_REPORT == "/var/lib/domovoi-mac/short_address.json"


# ─── a VM install: the host's LAN address from lan.json ───────────────────

VM_IP = "192.168.64.6"  # the VM's private address, which no device reaches
MAC_LAN_IP = "192.168.1.20"


def _vm_svc(tmp_path, monkeypatch, *, lan: str | None, in_vm: bool = True) -> ShortAddress:
    monkeypatch.setattr(sa, "_host_lan_ip", lambda: lan)
    monkeypatch.setattr(sa, "_in_vm", lambda: in_vm)
    monkeypatch.setattr(sa, "configured_hosts", lambda: ())
    svc = _svc(tmp_path, _free_port())
    svc.set_lan_labels([])
    return svc


def _ask_svc(svc: ShortAddress, host: str, local_ip: str = VM_IP):
    data, status = answer(
        get("/music", host).encode("ascii"), local_ip=local_ip, names=svc.names(),
        target_port=6369, suggest_ip=svc.suggested_ip(local_ip),
    )
    return status, data.decode("utf-8")


def test_the_lan_json_address_is_this_servers_own(tmp_path, monkeypatch) -> None:
    svc = _vm_svc(tmp_path, monkeypatch, lan=MAC_LAN_IP)
    assert MAC_LAN_IP in svc.names()
    status, data = _ask_svc(svc, MAC_LAN_IP)
    assert status == 302
    assert f"\r\nLocation: http://{MAC_LAN_IP}:6369/music\r\n" in data


@pytest.mark.parametrize("host", ["evil.example", "192.168.1.21", "10.0.0.5", "rebind.example.com"])
def test_a_vm_install_still_refuses_a_foreign_name(tmp_path, monkeypatch, host: str) -> None:
    svc = _vm_svc(tmp_path, monkeypatch, lan=MAC_LAN_IP)
    status, data = _ask_svc(svc, host)
    assert status == 400 and "Location" not in data
    # The refusal names the Mac's own address, never the VM's.
    assert f"Open http://{MAC_LAN_IP}:6369/ instead." in data
    assert VM_IP not in data


def test_a_vm_install_without_lan_json_names_no_vm_address(tmp_path, monkeypatch) -> None:
    svc = _vm_svc(tmp_path, monkeypatch, lan=None)
    status, data = _ask_svc(svc, "evil.example")
    assert status == 400
    assert VM_IP not in data
    assert "this server's address with :6369" in data


def test_without_a_vm_the_connections_address_is_named_as_before(tmp_path, monkeypatch) -> None:
    svc = _vm_svc(tmp_path, monkeypatch, lan=None, in_vm=False)
    status, data = _ask_svc(svc, "evil.example", local_ip=LAN_IP)
    assert status == 400
    assert f"Open http://{LAN_IP}:6369/ instead." in data


def test_lan_json_is_read_through_the_cores_reader(tmp_path, monkeypatch) -> None:
    from domovoi import lan_address

    d = tmp_path / "domovoi-mac"
    d.mkdir()
    (d / "lan.json").write_text(json.dumps({"schema": 1, "ipv4": MAC_LAN_IP}), encoding="utf-8")
    monkeypatch.setenv("DOMOVOI_HOST_LAN_FILE", str(d / "lan.json"))
    monkeypatch.setattr(lan_address, "_root_only", lambda st: True)
    assert sa._host_lan_ip() == MAC_LAN_IP
    # Not root's alone: not believed, so it adds no address.
    monkeypatch.setattr(lan_address, "_root_only", lambda st: False)
    assert sa._host_lan_ip() is None
