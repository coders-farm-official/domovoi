"""What a home-Wi-Fi join actually did, attempt by attempt, for the card.

Kamron's card on 2026-10-22, after the WPA2/WPA3 fix, still read
"the password wasn't accepted (Secrets were required)" with a ``join_log``
that was only the last eight lines of the journal: NetworkManager giving up
and the regulatory domain being re-applied after the disconnect. Nothing in
it said which access point the radio tried, what security it negotiated, or
what the access point answered, and with two access points broadcasting
the same name that is the whole question.

This module records, for one provisioning attempt:

* ``scan``: every access point broadcasting the chosen name, as the radio
  saw it just before joining: its security flags, channel, frequency and
  signal, under a stable label (``AP-1`` strongest) plus the last two bytes
  of its hardware address, so two radios on one name are told apart and
  the rest of the address never lands on a card anyone can read;
* ``attempts``: for each join attempt, the key-mgmt and PMF used, the
  access point (label) and frequency it tried, the outcome, the 802.11
  reason and status codes in words, and the wpa_supplicant /
  NetworkManager lines that matter, in order.

Scrubbed as the rest of the card is: never the password or a PSK, never
the network's name (``<network>``), never a whole hardware address.
Stdlib only.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

_MAC = re.compile(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b")
NETWORK = "<network>"

# 802.11 reason codes (deauthentication / disassociation) worth naming.
REASONS = {
    1: "unspecified", 2: "previous authentication no longer valid",
    3: "station is leaving", 4: "inactivity", 6: "class 2 frame from a "
    "non-authenticated station", 7: "class 3 frame from a non-associated station",
    8: "station is leaving the BSS", 14: "message integrity (MIC) failure",
    15: "4-way handshake timeout (the access point's key did not match)",
    16: "group key handshake timeout", 17: "information element differs",
    23: "802.1X authentication failed", 24: "cipher rejected",
}
# 802.11 status codes (association / authentication refusals).
STATUSES = {
    1: "unspecified failure", 12: "refused (capabilities)", 13: "unsupported "
    "authentication algorithm", 15: "authentication rejected (SAE: wrong "
    "password or commit refused)", 17: "access point is full",
    30: "temporarily refused (PMF SA query)", 31: "refused: the access point "
    "requires Protected Management Frames (PMF)", 40: "invalid information element",
    41: "invalid group cipher", 42: "invalid pairwise cipher", 43: "invalid AKM",
    53: "invalid PMKID", 72: "unsupported finite cyclic group (SAE)",
    77: "unsupported finite cyclic group",
}

# A journal line is kept when it is about the radio or the join.
_KEEP = (
    "wlan0", "trying to associate", "associated with", "ctrl-event", "4-way",
    "handshake", "sae", "wrong_key", "reason", "status_code", "auth",
    "activation", "supplicant", "secrets", "pmf", "key_mgmt", "assoc",
)
_SKIP = ("dhcp", "dns", "ipv6", "platform-linux", "rfkill", "manager: networkmanager state")

_MAX_LINES = 40
_MAX_ATTEMPTS = 9


def _split_terse(line: str) -> list[str]:
    fields, cur, i = [], [], 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    fields.append("".join(cur))
    return fields


def _text(r) -> str:
    out = r.stdout
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    return out or ""


def _int(v: Any) -> int | None:
    m = re.match(r"\s*(-?\d+)", str(v or ""))
    return int(m.group(1)) if m else None


class Diag:
    """One provisioning attempt's evidence. ``card()`` is what goes on it."""

    def __init__(self) -> None:
        self.scan: list[dict[str, Any]] = []
        self.attempts: list[dict[str, Any]] = []
        self._labels: dict[str, str] = {}

    def reset(self) -> None:
        self.__init__()

    # ── labels ──
    def label(self, mac: str) -> str:
        mac = mac.lower()
        if mac not in self._labels:
            n = sum(1 for v in self._labels.values() if v.startswith("AP-")) + 1
            self._labels[mac] = f"AP-{n}"
        return self._labels[mac]

    def _named(self, mac: str) -> str:
        return f"{self.label(mac)} (..:{mac.lower()[-5:]})"

    def scrub(self, line: str, ssid: str, psk: str) -> str:
        if psk and len(psk) >= 4:
            line = line.replace(psk, "<psk>")
        if ssid:
            for form in (f"'{ssid}'", f'"{ssid}"', f"SSID={ssid}", f"ssid={ssid}"):
                line = line.replace(form, form.replace(ssid, NETWORK))
            if len(ssid) >= 4:
                line = line.replace(ssid, NETWORK)
        # Only the access points of the chosen network are named; anything
        # else (the radio's own address, a neighbour) is just "a device".
        def sub(m: re.Match) -> str:
            mac = m.group(0).lower()
            return self._named(mac) if mac in self._labels else "xx:xx"
        return _MAC.sub(sub, line).strip()[:240]

    # ── the scan ──
    def take_scan(self, run, nmcli: str, ssid: str) -> None:
        """Every BSSID broadcasting ``ssid`` in NetworkManager's scan cache
        (the join's own rescan has just filled it)."""
        try:
            r = run([nmcli, "-t", "-f", "BSSID,SSID,SECURITY,CHAN,FREQ,SIGNAL", "device",
                     "wifi", "list", "--rescan", "no"],
                    capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return
        if r.returncode != 0:
            return
        rows = []
        for ln in _text(r).splitlines():
            f = _split_terse(ln.strip())
            if len(f) >= 2 and f[1] == ssid and _MAC.fullmatch(f[0] or ""):
                rows.append({
                    "mac": f[0].lower(),
                    "security": (f[2].strip() if len(f) > 2 else "") or "open",
                    "channel": _int(f[3]) if len(f) > 3 else None,
                    "freq_mhz": _int(f[4]) if len(f) > 4 else None,
                    "signal": _int(f[5]) if len(f) > 5 else None,
                })
        rows.sort(key=lambda x: -(x["signal"] or 0))
        self.scan = []
        for row in rows:
            mac = row.pop("mac")
            self.scan.append({"ap": self._named(mac), **row})

    # ── one attempt ──
    def record(self, run, *, since: float, key_mgmt: str, pmf: str | None, ok: bool,
               kind: str | None, detail: str, ssid: str, psk: str) -> dict[str, Any]:
        raw_lines = self._journal(run, since)
        tried = None
        freq = None
        for raw in raw_lines:
            m = re.search(r"Trying to associate with ([0-9a-fA-F:]{17})(.*)", raw)
            if m:
                tried = m.group(1).lower()
                fm = re.search(r"freq=(\d+)", m.group(2))
                freq = int(fm.group(1)) if fm else freq
        if tried:
            self.label(tried)          # named in the log lines below too
        joined = " ".join(raw_lines)
        reasons = sorted({int(x) for x in re.findall(r"\breason=(\d+)", joined)})
        statuses = sorted({int(x) for x in re.findall(r"\bstatus_code=(\d+)", joined)})
        hints = []
        if "WRONG_KEY" in joined or "4-Way Handshake failed" in joined or 15 in reasons:
            hints.append("the access point rejected the key in the 4-way handshake")
        if "CTRL-EVENT-ASSOC-REJECT" in joined:
            hints.append("the access point refused the association")
        if re.search(r"\bSAE\b", joined):
            hints.append("WPA3 (SAE) was negotiated")
        entry = {
            "key_mgmt": key_mgmt, "pmf": pmf or "-",
            "ap": self._named(tried) if tried else None, "freq_mhz": freq,
            "outcome": "joined" if ok else (kind or "failed"),
            "nmcli": detail or None,
            "reason_codes": [f"{c}: {REASONS.get(c, '?')}" for c in reasons],
            "status_codes": [f"{c}: {STATUSES.get(c, '?')}" for c in statuses],
            "hints": hints,
            # Codes and addresses were read from the raw text above; only
            # the scrubbed lines are kept.
            "log": [self.scrub(ln, ssid, psk) for ln in raw_lines],
        }
        if len(self.attempts) < _MAX_ATTEMPTS:
            self.attempts.append(entry)
        return entry

    def _journal(self, run, since: float) -> list[str]:
        try:
            r = run(["journalctl", "-b", "--no-pager", "-o", "cat",
                     "--since", f"@{int(since) - 1}",
                     "-u", "NetworkManager", "-u", "wpa_supplicant"],
                    capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return []
        if r.returncode != 0:
            return []
        raw = []
        for ln in _text(r).splitlines():
            low = ln.lower()
            if not any(k in low for k in _KEEP) or any(s in low for s in _SKIP):
                continue
            raw.append(ln)
        return raw[-_MAX_LINES:]

    def card(self) -> dict[str, Any]:
        return {"join_scan": list(self.scan), "join_attempts": list(self.attempts)}

