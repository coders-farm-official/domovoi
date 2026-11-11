"""Docker waits for the media folders (incident 2026-11-11).

On a Windows install, the music folder is a drvfs mount from /etc/fstab
with ``nofail``, which also drops its ordering before local-fs.target, so
dockerd could restore the rooms' music players before the folder was
there. Their starts failed (exit code 128) and Docker never retries a
failed start. ``provision.sh`` (its units step) and ``apply-update.sh``
(for boxes provisioned before) install a docker.service drop-in that
orders Docker after those mounts; both carry the same block of bash,
pinned here. The bash harnesses run it (``docker_*`` cases).
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPLY = ROOT / "scripts" / "linux" / "apply-update.sh"
PROVISION = ROOT / "scripts" / "linux" / "provision.sh"

BEGIN = "# >>> docker-media-order block >>>\n"
END = "# <<< docker-media-order block <<<\n"


def _block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text.count(BEGIN) == 1 and text.count(END) == 1, path.name
    return text[text.index(BEGIN):text.index(END) + len(END)]


def test_apply_update_and_provision_carry_the_same_block() -> None:
    assert _block(APPLY) == _block(PROVISION)


def test_the_block_reads_provision_s_own_fstab_markers() -> None:
    """The markers the block looks for are the ones the media step writes."""
    prov = PROVISION.read_text(encoding="utf-8")
    block = _block(PROVISION)
    for marker in ("# BEGIN Domovoi media folders (scripts/linux/provision.sh rewrites this block)",
                   "# END Domovoi media folders"):
        assert f'"{marker}"' in prov.split(BEGIN)[0]          # FSTAB_BEGIN / FSTAB_END
        assert f'"{marker}"' in block


def test_wants_and_after_never_requires() -> None:
    """A folder that is missing (a USB drive unplugged; the lines say
    nofail) must never keep Docker, and so all of Domovoi, from starting."""
    block = _block(APPLY)
    assert '"Wants=${units[*]}"' in block and '"After=${units[*]}"' in block
    assert "Requires" not in block.replace("not Requires=", "").replace("RequiresMountsFor", "")


def test_neither_script_restarts_docker_for_it() -> None:
    """The order matters at the next boot; restarting Docker now would stop
    every container."""
    for path in (APPLY, PROVISION):
        text = path.read_text(encoding="utf-8")
        fn = "reconcile_docker_media_order() {" if path == APPLY else "do_units() {"
        body = text[text.index(fn):]
        body = body[:body.index("\n}\n")]
        assert "restart docker" not in body
        assert "daemon-reload" in body
