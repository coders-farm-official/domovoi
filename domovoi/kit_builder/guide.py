"""START-HERE.txt and START-HERE.html: plain steps for each computer, on
the stick, readable with nothing installed (design sections 4-6).

They say which release is on the stick, how to install from it on each
target ticked, the installers' SHA-256 (so a careful person can compare
them with the release page from a phone: the first hop is trust in the
stick), that the iPhone app needs the App Store, and the notices ("don't
sell this stick", the wake word's licence, "Built with Llama"). No
household data: nothing here comes from the server but the release and
the list of what is on the stick.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from domovoi.offline_kit import format as fmt


@dataclass(frozen=True)
class GuideInfo:
    version: str
    tag: str
    commit: str
    targets: tuple[str, ...]
    includes: tuple[str, ...]
    installers: dict[str, str]          # file name -> SHA-256
    notices: tuple[str, ...]            # notice ids
    household: tuple[str, ...] = ()     # names of household-added files


RELEASE_PAGE = "https://github.com/coders-farm-official/domovoi/releases/tag/"


def _sections(g: GuideInfo) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    out.append(("What this is", [
        f"An offline kit for Domovoi {g.version}: everything a house with no internet needs to set "
        "up Domovoi. Nothing from the house that made it is on it: no names, no passwords, no media.",
        "Use a kit only from a house you trust.",
    ]))
    if fmt.DEFAULT_TARGETS[0] in g.targets:
        out.append(("A Windows 11 PC", [
            "Open this stick and double-click release\\DomovoiSetup.exe. Setup sees the kit beside it "
            "and installs from it. No internet needed.",
        ]))
        out.append(("A Linux computer (Ubuntu 26.04 only)", [
            "Install Ubuntu 26.04 first, from its own installer stick. Then plug this stick in and run:",
            "  sudo bash /media/<you>/DOMOVOIKIT/linux/install.sh --kit",
            "(On a server without a desktop: sudo mount /dev/sdX1 /mnt, then sudo bash /mnt/linux/install.sh --kit.)",
            "Other Linux systems (Debian, Mint, Pop!_OS) still need the internet to install.",
        ]))
    if "mac-arm64" in g.targets:
        out.append(("A Mac with an Apple chip", [
            "Open release/DomovoiSetup-mac.pkg. The setup app sees the kit and installs from it.",
        ]))
    out.append(("Phones", [
        "Android: once the server is set up, its join page has a QR code; the app comes from the "
        "server over the house Wi-Fi. The app is also in phone/ to copy over by cable.",
        "iPhone: the app needs the App Store. An iPhone that already has it works on the house Wi-Fi.",
    ]))
    if "rooms" in g.includes:
        out.append(("Rooms", [
            "rooms/ holds what the server needs to add rooms without the internet: the Windows "
            "satellite setup with its Python, a Linux computer's Python and packages, the Raspberry Pi "
            "card's packages and wake-word models, and Debian's installer image for the room stick. "
            "The server takes them in while it installs; nothing to do here.",
            "Add rooms as usual (Settings, Household, Rooms). A Raspberry Pi (from its card), a "
            "Windows PC and a Linux computer set up with no internet (a Linux computer needs its own "
            "sound packages, which most desktops have). The room stick for an old "
            "computer still needs internet in that room: Debian installs over the network.",
        ]))
    check = ["Every installer checks this stick before it uses it. To check it yourself on Linux:",
             "  cd /media/<you>/DOMOVOIKIT && sha256sum -c SHA256SUMS",
             "A problem means: make the stick again.",
             f"The installers on this stick, to compare with the release page ({RELEASE_PAGE}{g.tag}):"]
    check += [f"  {name}  SHA-256 {sha}" for name, sha in sorted(g.installers.items())]
    out.append(("Checking the stick", check))
    notes = [fmt.NOTICES[n] for n in g.notices if n in fmt.NOTICES]
    if g.household:
        notes.append("Added by the house that made this kit, not checked against a release: "
                     + ", ".join(g.household) + ".")
    notes.append("Licences and notices are in licences/.")
    out.append(("Licences", notes))
    return out


def text(g: GuideInfo) -> bytes:
    lines = [f"DOMOVOI OFFLINE KIT - Domovoi {g.version} ({g.tag}, commit {g.commit[:12]})", ""]
    for title, body in _sections(g):
        lines += [title.upper(), *body, ""]
    return "\n".join(lines).encode("utf-8")


def page(g: GuideInfo) -> bytes:
    parts = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        f"<title>Domovoi offline kit {html.escape(g.version)}</title>",
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem;"
        "color:#1f1b16;background:#fbf8f3}h1{font-size:1.6rem}h2{font-size:1.15rem;margin-top:1.6rem}"
        "code,pre{font:14px ui-monospace,monospace;background:#efe9df;padding:.1rem .3rem;border-radius:4px}"
        "pre{padding:.6rem;overflow-x:auto}@media (prefers-color-scheme:dark){body{color:#eee6da;"
        "background:#1b1814}code,pre{background:#2b2620}}</style></head><body>",
        f"<h1>Domovoi offline kit</h1><p>Domovoi {html.escape(g.version)} "
        f"({html.escape(g.tag)}, commit <code>{html.escape(g.commit[:12])}</code>)</p>",
    ]
    for title, body in _sections(g):
        parts.append(f"<h2>{html.escape(title)}</h2>")
        for line in body:
            if line.startswith("  "):
                parts.append(f"<pre>{html.escape(line.strip())}</pre>")
            else:
                parts.append(f"<p>{html.escape(line)}</p>")
    parts.append("</body></html>\n")
    return "".join(parts).encode("utf-8")
