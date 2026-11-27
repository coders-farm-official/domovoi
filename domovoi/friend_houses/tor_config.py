"""The Tor daemon's configuration, as Friend Houses wants it right now.

Friend Houses ("serverless messaging and friend system between households")
runs one C tor daemon per house. Core never talks to a control port (there
is none, so no stem, which is LGPL): it writes a config file and key files
into a folder the daemon shares, then reloads or restarts the daemon. This
module owns that folder and decides what the daemon should be doing.

The folder (``<config dir>/friend-houses/tor``), as the daemon sees it::

    /var/lib/fh/torrc                       written here
    /var/lib/fh/tor/                        tor's DataDirectory
    /var/lib/fh/hs-main/                    the house's onion (friends only)
    /var/lib/fh/hs-main/authorized_clients/ one <friend>.auth per friend
    /var/lib/fh/hs-pairing/                 the pairing onion (friend files)
    /var/lib/fh/hs-away/                    the away onion (phones: away chat)
    /var/lib/fh/hs-away/authorized_clients/ one <phone>.auth per enrolled phone
    /var/lib/fh/clientauth/                 one <friend>.auth_private per friend
    /run/fh/socks                           SOCKS, a unix socket
    /run/fh/listen/{main,pairing,away}.sock the listener's unix sockets

Rules learned in spike S1 (spike-s1/RESULTS.md, kept outside the repo):

* **An onion with no authorized clients is OPEN**, and adding the first
  client by reload doesn't close it straight away. So the main onion isn't
  configured at all until the first friend exists, and that change is
  applied with a restart, never a reload (:func:`plan_change`).
* **A reload that changes the client list briefly makes the onion
  unreachable,** so reloads are batched by the caller, never per friend.
* **The pairing onion is meant to be open**: it exists only while a friend
  file is outstanding, and its listener checks the invite secret.

Away chat (1.5.0) adds a third onion, ``hs-away``, for the household's own
phones (domovoi/away/). The same S1 rules apply to it: it isn't configured
until the first phone has enrolled (an onion with no clients is open), that
change is a restart, and later client changes ride the batched reloads. It
has its own ``authorized_clients``, so a friend's key can never reach the
phones' endpoint and a phone's can never reach friend messaging. Tor runs
for it alone when there's nothing else to do; the internet answer "never"
or "Chat from away" turned off removes it.
"""

from __future__ import annotations

import base64
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from domovoi.friend_houses import identity as fid
from domovoi.friend_houses.noise import public_bytes

# Paths inside the daemon's container.
DATA_MOUNT = "/var/lib/fh"
RUN_MOUNT = "/run/fh"
SOCKS_NAME = "socks"
MAIN_SOCKET = "main.sock"
PAIRING_SOCKET = "pairing.sock"
AWAY_SOCKET = "away.sock"
# The listener's sockets live in a folder of their own under the run
# folder: the listener's container mounts only that one, never the folder
# with Tor's SOCKS socket (audit R6 FH-12).
LISTEN_DIR = "listen"

_FRIEND_ID = re.compile(r"^[a-z0-9]{1,32}\Z")


@dataclass(frozen=True)
class Wanted:
    """What the daemon should be doing. ``run`` False: stopped."""

    run: bool
    main_onion: bool
    pairing_onion: bool
    disguise: tuple[str, ...] = ()  # torrc lines for bridges, when switched on
    away_onion: bool = False        # away chat's onion (phones), 1.5.0


def wanted(*, enabled: bool, internet_policy: str, friends: int, open_invites: int,
           disguise: tuple[str, ...] = (), away_on: bool = False, away_phones: int = 0) -> Wanted:
    """The daemon runs only when the internet answer isn't "never" and
    there's something to do. For Friend Houses: an admin turned it on, and
    there's a friend to reach or be reached by, or a friend file waiting to
    be used. For away chat: "Chat from away" is on and at least one phone
    has enrolled (it needs no friends, and not Friend Houses either).
    Otherwise it is stopped: not idle, stopped (threat-model finding 26)."""
    if internet_policy == "never":
        return Wanted(False, False, False)
    main = enabled and friends > 0
    pairing = enabled and open_invites > 0
    away = away_on and away_phones > 0
    if not (main or pairing or away):
        return Wanted(False, False, False)
    return Wanted(True, main, pairing, disguise if (main or pairing) else (), away)


def render_torrc(w: Wanted) -> str:
    lines = [
        "# Written by Domovoi (Friend Houses). Changes here are overwritten.",
        f"DataDirectory {DATA_MOUNT}/tor",
        # Onions only: the SOCKS port can't be used to reach the clear web
        # (audit R6 FH-12).
        f"SocksPort unix:{RUN_MOUNT}/{SOCKS_NAME} OnionTrafficOnly",
        f"ClientOnionAuthDir {DATA_MOUNT}/clientauth",
        "AvoidDiskWrites 1",
        "Log notice stdout",
    ]
    if w.main_onion:
        lines += [
            f"HiddenServiceDir {DATA_MOUNT}/hs-main",
            f"HiddenServicePort 80 unix:{RUN_MOUNT}/{LISTEN_DIR}/{MAIN_SOCKET}",
        ]
    if w.pairing_onion:
        lines += [
            f"HiddenServiceDir {DATA_MOUNT}/hs-pairing",
            f"HiddenServicePort 80 unix:{RUN_MOUNT}/{LISTEN_DIR}/{PAIRING_SOCKET}",
        ]
    if w.away_onion:
        lines += [
            f"HiddenServiceDir {DATA_MOUNT}/hs-away",
            f"HiddenServicePort 80 unix:{RUN_MOUNT}/{LISTEN_DIR}/{AWAY_SOCKET}",
        ]
    lines += list(w.disguise)
    return "\n".join(lines) + "\n"


def plan_change(before: Wanted | None, after: Wanted) -> str:
    """How to get the daemon from ``before`` to ``after``: ``"none"``,
    ``"stop"``, ``"start"``, ``"reload"`` (key files or bridges changed) or
    ``"restart"`` (an onion appeared or went away: see the module doc)."""
    if not after.run:
        return "stop" if before is not None and before.run else "none"
    if before is None or not before.run:
        return "start"
    if (before.main_onion != after.main_onion or before.pairing_onion != after.pairing_onion
            or before.away_onion != after.away_onion):
        return "restart"
    if before.disguise != after.disguise:
        return "restart"
    return "reload"


# ─── key files ────────────────────────────────────────────────────────────


def tor_b32(raw: bytes) -> str:
    """Tor's encoding for client-authorization keys: unpadded base32."""
    return base64.b32encode(raw).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class ReachKey:
    """A client-authorization key pair: the house that is reached keeps the
    public half in its ``authorized_clients``; the friend holds the private
    half to fetch the descriptor."""

    private: bytes
    public: bytes

    @classmethod
    def generate(cls) -> ReachKey:
        key = X25519PrivateKey.generate()
        return cls(key.private_bytes_raw(), public_bytes(key))


class TorFolder:
    """The folder core shares with the daemon. All writes are atomic and
    private (0600 files, 0700 folders); the daemon runs as the same user."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.data = root / "data"
        self.run = root / "run"

    @classmethod
    def default(cls) -> TorFolder:
        from domovoi.admin_auth import CONFIG_DIR

        return cls(CONFIG_DIR / "friend-houses" / "tor")

    def ensure(self) -> None:
        # Every level gets 0700 itself: tor refuses an onion folder that is
        # group- or world-readable, and mkdir(parents=True) would leave the
        # parents at the umask's mode.
        for d in (self.root, self.data, self.data / "tor", self.data / "clientauth",
                  self.data / "hs-main", self.data / "hs-main" / "authorized_clients",
                  self.data / "hs-pairing", self.data / "hs-away",
                  self.data / "hs-away" / "authorized_clients", self.run, self.listen):
            d.mkdir(parents=True, exist_ok=True)
            _chmod(d, 0o700)

    @property
    def socks_path(self) -> Path:
        return self.run / SOCKS_NAME

    @property
    def listen(self) -> Path:
        """The listener's socket folder (its container mounts only this)."""
        return self.run / LISTEN_DIR

    @property
    def main_socket(self) -> Path:
        return self.listen / MAIN_SOCKET

    @property
    def pairing_socket(self) -> Path:
        return self.listen / PAIRING_SOCKET

    @property
    def away_socket(self) -> Path:
        return self.listen / AWAY_SOCKET

    def write_torrc(self, w: Wanted) -> None:
        _write_private(self.data / "torrc", render_torrc(w))

    def authorize(self, friend_id: str, reach_public: bytes) -> None:
        """Let a friend fetch this house's descriptor."""
        _check_id(friend_id)
        _write_private(
            self.data / "hs-main" / "authorized_clients" / f"{friend_id}.auth",
            f"descriptor:x25519:{tor_b32(reach_public)}\n",
        )

    def deauthorize(self, friend_id: str) -> None:
        _check_id(friend_id)
        (self.data / "hs-main" / "authorized_clients" / f"{friend_id}.auth").unlink(missing_ok=True)

    def authorized(self) -> set[str]:
        folder = self.data / "hs-main" / "authorized_clients"
        return _friend_stems(folder, "*.auth")

    # Away chat's phones: their own folder, never hs-main's.

    def authorize_phone(self, phone_id: str, tor_auth_pub: str) -> None:
        """Let an enrolled phone fetch the away onion's descriptor.
        ``tor_auth_pub`` is the phone's key as it sent it (52 characters of
        base32; :func:`domovoi.away.store.tor_auth_raw` checked it)."""
        _check_id(phone_id)
        line = f"descriptor:x25519:{tor_auth_pub}\n"
        path = self.data / "hs-away" / "authorized_clients" / f"{phone_id}.auth"
        try:
            if path.read_text(encoding="ascii") == line:
                return
        except OSError:
            pass
        _write_private(path, line)

    def deauthorize_phone(self, phone_id: str) -> None:
        _check_id(phone_id)
        (self.data / "hs-away" / "authorized_clients" / f"{phone_id}.auth").unlink(missing_ok=True)

    def phones_authorized(self) -> set[str]:
        return _friend_stems(self.data / "hs-away" / "authorized_clients", "*.auth")

    def hold_key_for(self, friend_id: str, onion_key: bytes, reach_private: bytes) -> None:
        """Keep the key this house uses to reach a friend's onion."""
        _check_id(friend_id)
        address = fid.onion_address(onion_key)[: -len(".onion")]
        _write_private(
            self.data / "clientauth" / f"{friend_id}.auth_private",
            f"{address}:descriptor:x25519:{tor_b32(reach_private)}\n",
        )

    def drop_key_for(self, friend_id: str) -> None:
        _check_id(friend_id)
        (self.data / "clientauth" / f"{friend_id}.auth_private").unlink(missing_ok=True)

    def held(self) -> set[str]:
        folder = self.data / "clientauth"
        return _friend_stems(folder, "*.auth_private")

    def onion_key(self, service: str = "hs-main") -> bytes | None:
        """This house's onion key as tor wrote it (``hostname``), or None
        before the daemon has made one."""
        try:
            text = (self.data / service / "hostname").read_text(encoding="ascii")
        except OSError:
            return None
        return fid.parse_onion(text.strip())

    def forget_onion(self, service: str) -> None:
        """Delete an onion's keys so the next start makes a new address (the
        pairing onion when no friend file is left; the main onion on block)."""
        folder = self.data / service
        for name in ("hostname", "hs_ed25519_secret_key", "hs_ed25519_public_key"):
            (folder / name).unlink(missing_ok=True)


def _friend_stems(folder: Path, pattern: str) -> set[str]:
    """The friend ids with a key file in ``folder``. A name that isn't one
    of ours is ignored, so a stray file can't stop a reconcile."""
    if not folder.is_dir():
        return set()
    return {p.stem for p in folder.glob(pattern) if _FRIEND_ID.match(p.stem)}


def _check_id(friend_id: str) -> None:
    if not _FRIEND_ID.match(friend_id):
        raise ValueError("a friend id is 1-32 lowercase letters and digits")


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:  # pragma: no cover — Windows dev boxes
        pass


def _write_private(path: Path, text: str | bytes) -> None:
    """Atomic 0600 write. The folder is shared with the daemon's container,
    so the temp file gets a fresh random name and is created exclusively,
    never through an existing name or a link."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        try:
            os.write(fd, text.encode("utf-8") if isinstance(text, str) else text)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
