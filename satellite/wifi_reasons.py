"""Why a home-Wi-Fi join failed, in words a person can act on.

Until 2026-10-22 every failed join reached the setup page as "Couldn't join
that network. Check the password and try again." followed by the LAST line
nmcli printed. On a real NetworkManager that last line is always

    Hint: use 'journalctl -xe NM_CONNECTION=<uuid> + NM_DEVICE=wlan0' to get more details.

so the actual reason (the line above it) was thrown away, and a household
whose password was right was told to check it (Kamron's satellites,
2026-10-22, a WPA2/WPA3 router). This module is the one place a failure
gets a name:

* :func:`classify` reads nmcli's whole output for one attempt and says what
  kind of failure it was;
* the join tags its error ``[kind]`` (see :func:`tagged`), so every reader
  (the portal page, the card) can tell the kinds apart without re-guessing
  from free text;
* :data:`PORTAL` / :data:`CARD` are what each of them says. Only
  ``WRONG_PASSWORD`` mentions the password as THE reason.

Stdlib only, importable on its own: provisioning mode, the portal and the
card report all use it.
"""

from __future__ import annotations

import re

# ─── kinds ───────────────────────────────────────────────────────────────

WRONG_PASSWORD = "wrong-password"
WPA3_ONLY = "wpa3-only"                 # every SAE attempt was refused
WPA3_RAW_KEY = "wpa3-raw-key"           # WPA3-only network, 64-hex key typed
AUTH_TIMEOUT = "auth-timeout"           # the handshake never finished
NOT_FOUND = "not-found"
NOT_FOUND_24 = "not-found-2.4"          # ... and this radio is 2.4 GHz only
TIMEOUT = "timeout"
NO_ADDRESS = "no-address"               # associated, no DHCP lease
ENTERPRISE = "enterprise"
WEP = "wep"
NEEDS_PASSWORD = "needs-password"       # secured network, blank password
RADIO = "radio"
SETUP_FAILED = "setup-failed"           # the profile itself was refused
OTHER = "other"

KINDS = (
    WRONG_PASSWORD, WPA3_ONLY, WPA3_RAW_KEY, AUTH_TIMEOUT, NOT_FOUND,
    NOT_FOUND_24, TIMEOUT, NO_ADDRESS, ENTERPRISE, WEP, NEEDS_PASSWORD,
    RADIO, SETUP_FAILED, OTHER,
)

# An attempt whose failure is one of these REACHED the access point and
# was turned away during authentication: the only kind where trying another
# security setting can help.
AUTH_KINDS = (WRONG_PASSWORD, AUTH_TIMEOUT)

# ─── what nmcli says ─────────────────────────────────────────────────────
#
# NetworkManager's device-state reasons as nmcli prints them, newest wording
# and the older "(N)" codes both (libnm NMDeviceStateReason). Checked in
# this order: the first match names the attempt.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (NOT_FOUND, (
        "no network with ssid",
        "could not be found",            # (53) SSID_NOT_FOUND
        "(53)",
        "ssid-not-found",
    )),
    (NO_ADDRESS, (
        "ip configuration could not be reserved",   # (5)
        "dhcp",
        "ip-config-unavailable",
    )),
    (AUTH_TIMEOUT, (
        "supplicant took too long",      # (11) SUPPLICANT_TIMEOUT
        "supplicant-timeout",
    )),
    (WRONG_PASSWORD, (
        "secrets were required",         # (7) NO_SECRETS
        "no secrets",
        "(7)",
        "supplicant disconnected",       # (8) SUPPLICANT_DISCONNECT
        "supplicant failed",             # (10) SUPPLICANT_FAILED
        "supplicant configuration failed",  # (9)
        "authentication",
        "4-way handshake",
        "association rejected",
    )),
    (RADIO, (
        "no such device",
        "not available",
        "rfkill",
        "unmanaged",
        "is not ready",
        "no wi-fi device",
        "device not found",
    )),
    (TIMEOUT, (
        "timeout",
        "timed out",
        "expired",
    )),
)


def classify(text: str | bytes | None) -> str:
    """The kind of one attempt's failure, from everything nmcli printed
    (stderr and stdout, every line: the reason is never the last one)."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    low = (text or "").lower()
    for kind, markers in _RULES:
        if any(m in low for m in markers):
            return kind
    return OTHER


def reason_line(text: str | bytes | None) -> str:
    """nmcli's own reason, one line: the ``Error:`` line if there is one,
    otherwise the last line that is not a ``Hint:``. Empty when it said
    nothing useful."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    lines = [ln for ln in lines if not ln.lower().startswith("hint:")]
    for ln in lines:
        if ln.lower().startswith("error:"):
            return ln
    return lines[-1] if lines else ""


_TAG = re.compile(r"\[(" + "|".join(re.escape(k) for k in KINDS) + r")\]")


def tagged(prefix: str, kind: str, detail: str = "") -> str:
    """``<prefix> [<kind>]: <detail>`` — the shape every join error has."""
    out = f"{prefix} [{kind}]"
    return f"{out}: {detail}" if detail else out


def kind_of(error: str | None) -> str | None:
    """The kind a join error was tagged with, or None for an untagged
    (older, or not-a-join) error. The LAST tag wins: a network name typed
    with brackets in it comes before the tag."""
    found = _TAG.findall(error or "")
    return found[-1] if found else None


def detail_of(error: str | None) -> str:
    """What follows the tag: nmcli's words, already free of the network's
    name and the password (the join scrubs both before tagging)."""
    m = None
    for m in _TAG.finditer(error or ""):
        pass
    if m is None:
        return ""
    rest = (error or "")[m.end():]
    return rest[1:].strip() if rest.startswith(":") else rest.strip()


# ─── what people are told ────────────────────────────────────────────────

_MIXED = ("set your router's Wi-Fi security to WPA2/WPA3 mixed (often called "
          "\"WPA2-PSK/WPA3-SAE\" or \"WPA3-Personal Transition\")")

# The setup page. The person is standing next to the speaker with a phone.
PORTAL = {
    WRONG_PASSWORD: "The Wi-Fi password wasn't accepted. Check it (capital "
                    "letters count) and try again.",
    WPA3_ONLY: "This network only allows WPA3, and this speaker couldn't join "
               "it that way. If the password is right, " + _MIXED + " and try "
               "again. A Raspberry Pi Zero 2 W can't use WPA3.",
    WPA3_RAW_KEY: "This network only allows WPA3, which needs the Wi-Fi "
                  "password itself, not a 64-character key. Type the password.",
    AUTH_TIMEOUT: "The router didn't finish letting the speaker in. Move it "
                  "closer to the router and try again; if it's already close, "
                  "check the password.",
    NOT_FOUND: "The speaker couldn't find that network. Move it closer to the "
               "router, and check the name if you typed it (capital letters "
               "count).",
    NOT_FOUND_24: "The speaker couldn't find that network. This speaker only "
                  "uses 2.4 GHz Wi-Fi: if your router has this network on "
                  "5 GHz only, turn on its 2.4 GHz band. Otherwise move the "
                  "speaker closer, and check the name if you typed it.",
    TIMEOUT: "The router took too long to answer. Move the speaker closer to "
             "the router and try again.",
    NO_ADDRESS: "The speaker joined the network but the router didn't give it "
                "an address. Restart the router, or check it isn't blocking "
                "new devices, then try again.",
    ENTERPRISE: "That network needs a username as well as a password "
                "(WPA-Enterprise / 802.1X), which this setup page can't do. "
                "Choose a network that only needs a password; a guest "
                "network often works.",
    WEP: "That network uses WEP, an old security type this speaker can't use. "
         "Switch the router to WPA2 or WPA2/WPA3 mixed.",
    NEEDS_PASSWORD: "That network needs a password.",
    RADIO: "This speaker's Wi-Fi radio wasn't ready. Unplug it for ten "
           "seconds, plug it back in, and try again.",
    SETUP_FAILED: "The speaker couldn't save those Wi-Fi details. Check the "
                  "network name and password and try again.",
    OTHER: "Couldn't join that network.",
}

# The card's device-info.json: anyone holding the card can read it, so no
# network name, ever; short, because it is read in a text editor.
CARD = {
    WRONG_PASSWORD: "couldn't join your home Wi-Fi — the password wasn't accepted",
    WPA3_ONLY: "couldn't join your home Wi-Fi — it is WPA3-only; turn on WPA2/WPA3 mixed mode",
    WPA3_RAW_KEY: "couldn't join your home Wi-Fi — WPA3 needs the password, not a 64-character key",
    AUTH_TIMEOUT: "couldn't join your home Wi-Fi — the router didn't finish letting it in (range or password)",
    NOT_FOUND: "couldn't find your home Wi-Fi — is it in range?",
    NOT_FOUND_24: "couldn't find your home Wi-Fi — in range, and on 2.4 GHz?",
    TIMEOUT: "couldn't join your home Wi-Fi in time — is it in range?",
    NO_ADDRESS: "joined your home Wi-Fi but the router gave no address",
    ENTERPRISE: "your home Wi-Fi needs a username (802.1X), which setup can't do",
    WEP: "your home Wi-Fi uses WEP, which this speaker can't use",
    NEEDS_PASSWORD: "your home Wi-Fi needs a password",
    RADIO: "couldn't join your home Wi-Fi — the radio wasn't ready",
    SETUP_FAILED: "the home Wi-Fi details were refused — check the name and password",
    OTHER: "couldn't join your home Wi-Fi",
}
