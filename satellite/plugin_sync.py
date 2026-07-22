"""Mirror enabled plugins' satellite payloads from the Domovoi server.

The fourth sync channel (after sounds, code, wake models): fetches
``/v1/satellite-plugins/manifest`` and mirrors each ``<slug>/<rel>`` file
into ``~/.domovoi/plugin_payloads/<slug>/``, sha256-verifying every body.
Prune is conservative, exactly like code_sync: only paths present in the
PREVIOUS synced manifest and absent from the new one are removed — so a
disabled plugin's subtree converges away and nothing else is ever touched.

Root work (apt packages / post-install scripts, gated by the plugin's
``satellite_root`` permission server-side) is NOT run here: this module
stages a request file (``~/.domovoi/pending_payload.json``) and invokes the
sudoers-allowlisted ``domovoi-apply-payload`` helper, which does the
privileged part and logs to ``~/.domovoi/payload_apply.log``. Payload sync
failure never blocks a code upgrade — plugins degrade, the satellite runs.

UNTESTED on the dev host beyond unit tests — exercised on a real device
during an upgrade, like code_sync.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import requests

from satellite.sound_sync import _safe_rel, _sha256

log = logging.getLogger("satellite.plugin_sync")

CONFIG_DIR = Path("~/.domovoi").expanduser()
PAYLOADS_DIR = CONFIG_DIR / "plugin_payloads"
MANIFEST_SIDECAR = CONFIG_DIR / "plugin_payload_manifest.json"
STATE_SIDECAR = CONFIG_DIR / "plugin_payload_state.json"
PENDING_FILE = CONFIG_DIR / "pending_payload.json"
APPLY_HELPER = "/usr/local/sbin/domovoi-apply-payload"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def sync_plugin_payloads(
    http_base: str,
    payloads_root: Path = PAYLOADS_DIR,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Mirror the channel into ``payloads_root``. Returns
    ``{"meta", "downloaded", "pruned", "root_work"}`` where ``root_work``
    lists slugs whose apt/post-install state changed (the caller then
    stages + invokes the root helper). Raises on network/HTTP errors or a
    sha mismatch — like code_sync, a corrupt body never lands."""
    base = http_base.rstrip("/")
    r = requests.get(f"{base}/v1/satellite-plugins/manifest", timeout=timeout)
    r.raise_for_status()
    doc = r.json()
    files: dict[str, str] = doc.get("files") or {}
    meta: dict[str, Any] = doc.get("meta") or {}

    payloads_root.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    for rel, sha in files.items():
        if not _safe_rel(rel) or "/" not in rel:
            log.warning("plugin sync: skipping unsafe manifest path %r", rel)
            continue
        dest = payloads_root / rel
        if dest.is_file() and _sha256(dest) == sha:
            continue
        body = requests.get(f"{base}/v1/satellite-plugins/{rel}", timeout=timeout)
        body.raise_for_status()
        got = hashlib.sha256(body.content).hexdigest()
        if got != sha:
            raise RuntimeError(
                f"plugin payload sha mismatch for {rel!r} (manifest {sha[:12]}…, "
                f"body {got[:12]}…)"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body.content)
        downloaded += 1

    # Conservative prune: previous-manifest paths that vanished.
    prev: dict[str, str] = _read_json(MANIFEST_SIDECAR).get("files") or {}
    pruned = 0
    for rel in prev:
        if rel in files or not _safe_rel(rel):
            continue
        stale = payloads_root / rel
        try:
            if stale.is_file():
                stale.unlink()
                pruned += 1
        except OSError as e:
            log.warning("plugin sync: prune of %s failed: %s", rel, e)

    MANIFEST_SIDECAR.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_SIDECAR.write_text(
        json.dumps({"files": files}, indent=2), encoding="utf-8"
    )

    root_work = _pending_root_work(meta, payloads_root)
    log.info(
        "plugin sync: %d file(s) downloaded, %d pruned, %d slug(s) with "
        "pending root work", downloaded, pruned, len(root_work),
    )
    return {
        "meta": meta,
        "downloaded": downloaded,
        "pruned": pruned,
        "root_work": root_work,
    }


def _pending_root_work(
    meta: dict[str, Any], payloads_root: Path
) -> list[str]:
    """Slugs whose ROOT-side state (apt set / post-install content) differs
    from what was last applied (STATE_SIDECAR)."""
    applied: dict[str, Any] = _read_json(STATE_SIDECAR)
    out: list[str] = []
    for slug, m in sorted(meta.items()):
        apt = sorted(m.get("apt_packages") or [])
        post = m.get("post_install")
        post_sha = None
        if post:
            p = payloads_root / slug / post
            if p.is_file():
                post_sha = _sha256(p)
        if not apt and post_sha is None:
            continue
        prev = applied.get(slug) or {}
        if prev.get("apt_packages") != apt or prev.get("post_install_sha") != post_sha:
            out.append(slug)
    return out


def request_root_apply(
    meta: dict[str, Any],
    slugs: list[str],
    payloads_root: Path = PAYLOADS_DIR,
    run=subprocess.run,
) -> bool:
    """Stage ``pending_payload.json`` and invoke the sudoers-allowlisted
    root helper for the given slugs. Best-effort: False (with a log) when
    the helper is missing or refused — the satellite keeps running and the
    dashboard's upgrade report shows the payload as pending."""
    payload = {
        "slugs": {
            slug: {
                "apt_packages": sorted((meta.get(slug) or {}).get("apt_packages") or []),
                "post_install": (meta.get(slug) or {}).get("post_install"),
                "version": (meta.get(slug) or {}).get("version"),
            }
            for slug in slugs
        },
        "payloads_root": str(payloads_root),
        "state_file": str(STATE_SIDECAR),
    }
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        r = run(
            ["sudo", "-n", APPLY_HELPER],
            capture_output=True,
            timeout=600,
        )
        if r.returncode != 0:
            log.warning(
                "apply-payload helper failed (rc=%d) — is the sudoers entry "
                "from PROVISIONING.md installed?", r.returncode,
            )
            return False
        return True
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("apply-payload helper unavailable: %s", e)
        return False
