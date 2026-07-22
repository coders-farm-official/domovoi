"""plugin_sync — mirror/prune/verify against a stubbed HTTP layer, plus
the root-work change detection."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from satellite import plugin_sync


class _Resp:
    def __init__(self, *, content: bytes = b"", json_data=None, status: int = 200):
        self.content = content
        self._json = json_data
        self.status_code = status

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def sidecars(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_sync, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        plugin_sync, "MANIFEST_SIDECAR", tmp_path / "plugin_payload_manifest.json"
    )
    monkeypatch.setattr(
        plugin_sync, "STATE_SIDECAR", tmp_path / "plugin_payload_state.json"
    )
    monkeypatch.setattr(plugin_sync, "PENDING_FILE", tmp_path / "pending_payload.json")
    return tmp_path


def _stub_http(monkeypatch, files: dict[str, bytes], meta: dict) -> None:
    manifest = {
        "files": {rel: _sha(body) for rel, body in files.items()},
        "meta": meta,
    }

    def fake_get(url, timeout=10.0):
        if url.endswith("/v1/satellite-plugins/manifest"):
            return _Resp(json_data=manifest)
        rel = url.split("/v1/satellite-plugins/")[1]
        if rel in files:
            return _Resp(content=files[rel])
        return _Resp(status=404)

    monkeypatch.setattr(plugin_sync.requests, "get", fake_get)


def test_mirror_verify_and_prune(sidecars, tmp_path, monkeypatch):
    root = tmp_path / "payloads"
    files = {
        "radio/stations.json": b'{"fm": true}',
        "radio/tools/helper": b"\x7fELF",
    }
    _stub_http(monkeypatch, files, {"radio": {"version": "1.0.0"}})
    result = plugin_sync.sync_plugin_payloads("http://server:6370", root)
    assert result["downloaded"] == 2
    assert (root / "radio/stations.json").read_bytes() == b'{"fm": true}'

    # Second sync: nothing changed → nothing downloaded.
    result = plugin_sync.sync_plugin_payloads("http://server:6370", root)
    assert result["downloaded"] == 0

    # Plugin disabled → its files leave the manifest → conservative prune.
    _stub_http(monkeypatch, {}, {})
    result = plugin_sync.sync_plugin_payloads("http://server:6370", root)
    assert result["pruned"] == 2
    assert not (root / "radio/stations.json").exists()


def test_sha_mismatch_aborts(sidecars, tmp_path, monkeypatch):
    root = tmp_path / "payloads"
    body = b"real bytes"
    manifest = {"files": {"radio/x.bin": _sha(b"DIFFERENT")}, "meta": {}}

    def fake_get(url, timeout=10.0):
        if url.endswith("/manifest"):
            return _Resp(json_data=manifest)
        return _Resp(content=body)

    monkeypatch.setattr(plugin_sync.requests, "get", fake_get)
    with pytest.raises(RuntimeError, match="sha mismatch"):
        plugin_sync.sync_plugin_payloads("http://server:6370", root)
    assert not (root / "radio/x.bin").exists()


def test_unsafe_paths_skipped(sidecars, tmp_path, monkeypatch):
    root = tmp_path / "payloads"
    _stub_http(
        monkeypatch,
        {"../escape.sh": b"#!/bin/sh", "noslash": b"x"},
        {},
    )
    result = plugin_sync.sync_plugin_payloads("http://server:6370", root)
    assert result["downloaded"] == 0
    assert not (tmp_path / "escape.sh").exists()


def test_root_work_detection(sidecars, tmp_path):
    root = tmp_path / "payloads"
    (root / "radio").mkdir(parents=True)
    (root / "radio" / "post.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    meta = {
        "radio": {"apt_packages": ["libfoo2"], "post_install": "post.sh"},
        "quiet": {"apt_packages": [], "post_install": None},
    }
    # Nothing applied yet → radio flagged, quiet (no root work) not.
    assert plugin_sync._pending_root_work(meta, root) == ["radio"]

    # Record the applied state → no longer flagged.
    plugin_sync.STATE_SIDECAR.write_text(
        json.dumps({
            "radio": {
                "apt_packages": ["libfoo2"],
                "post_install_sha": plugin_sync._sha256(root / "radio" / "post.sh"),
            }
        }),
        encoding="utf-8",
    )
    assert plugin_sync._pending_root_work(meta, root) == []

    # Script content changes → flagged again.
    (root / "radio" / "post.sh").write_text("#!/bin/sh\necho v2\n", encoding="utf-8")
    assert plugin_sync._pending_root_work(meta, root) == ["radio"]


def test_request_root_apply_stages_and_invokes(sidecars, tmp_path):
    calls = []

    def fake_run(cmd, capture_output=True, timeout=600):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    meta = {"radio": {"apt_packages": ["libfoo2"], "post_install": "post.sh", "version": "1.0.0"}}
    ok = plugin_sync.request_root_apply(meta, ["radio"], tmp_path / "payloads", run=fake_run)
    assert ok is True
    assert calls[0][:2] == ["sudo", "-n"]
    staged = json.loads(plugin_sync.PENDING_FILE.read_text(encoding="utf-8"))
    assert staged["slugs"]["radio"]["apt_packages"] == ["libfoo2"]
