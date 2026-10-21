""""Report a problem": a redacted diagnostics summary the person posts.

When help can't solve something, the person can ask for a summary to post
themselves (GitHub Discussions, Discord). It is built here, shown to them
in full, and they copy it or save it as a file. **Nothing is sent**: there
is no upload, no address it goes to, and the server keeps no copy.

What it holds: the version, the kind of host, AI on or off, the internet
answer, the plugins and their versions, how many rooms and in which state,
the problems the checks show (:mod:`.troubleshoot`), the last update's
result, and what the person wrote about the problem.

What is taken out: the values this install holds — the household token,
the setup code, password and token hashes, the Wi-Fi network names and
host names its satellites reported, its own host name and the hosts and
domains of its configured URLs, people's names — by exact match
(:mod:`.known`), then whatever the patterns in :mod:`.redact` recognise
(addresses, URLs, quoted text, tokens, codes, home folders). Rooms are
"room 1", "room 2"… unless the person ticks "include room names" (their
own choice). Every free-text value goes through :func:`.redact.redact`,
what the person typed included, and the whole text once more at the end.
What it can't promise: a value Domovoi never saw, written in a way no
pattern matches. So the person reads the whole summary before posting it,
and the note at its top says so.
"""

from __future__ import annotations

import platform
import re
from datetime import datetime, timezone

from domovoi.help_desk.facts import INTERNET_WORDS, LiveFacts
from domovoi.help_desk.redact import redact
from domovoi.help_desk.troubleshoot import Finding

HEADER = "Domovoi problem report"
NOTE = (
    "Made by Domovoi's \"Report a problem\". Nothing was sent anywhere. Domovoi took out the "
    "tokens, codes, addresses and names it knows about; read it all before you post it, and remove "
    "anything else you'd rather keep private."
)
WHAT_MAX = 2000


def host_kind() -> str:
    """"Linux", "Windows (WSL)", "macOS": the kind of machine, no more."""
    system = platform.system()
    if system == "Linux":
        try:
            with open("/proc/version", encoding="utf-8") as f:
                if "microsoft" in f.read().lower():
                    return "Windows (WSL)"
        except OSError:
            pass
        return "Linux"
    return {"Darwin": "macOS"}.get(system, system or "unknown")


def build(
    facts: LiveFacts,
    found: list[Finding],
    *,
    what_happened: str = "",
    include_room_names: bool = False,
    last_update_status: str | None = None,
    now: datetime | None = None,
    host: str | None = None,
) -> str:
    rooms = list(facts.rooms)
    names = {r.name: (r.name if include_room_names else f"room {i}") for i, r in enumerate(rooms, 1)}

    def rename(text: str) -> str:
        if include_room_names:
            return text
        out = text
        # Longest first, so "kitchen 2" is replaced before "kitchen".
        for real in sorted(names, key=len, reverse=True):
            out = re.sub(rf"(?i)\b{re.escape(real)}\b", names[real], out)
        return out

    when = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"## {HEADER}", "", f"_{NOTE}_", "", f"- Made: {when}",
             f"- Domovoi version: {facts.version or 'unknown'}",
             f"- Host: {host or host_kind()}",
             f"- AI chat and questions: {'on' if facts.ai_on else 'off'}",
             f"- Internet: {INTERNET_WORDS.get(facts.internet, facts.internet)}"]
    if rooms:
        counts: dict[str, int] = {}
        for r in rooms:
            counts[r.status] = counts.get(r.status, 0) + 1
        lines.append(f"- Rooms: {len(rooms)} (" + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) + ")")
        for r in rooms:
            lines.append(f"  - {names[r.name]}: {r.status}")
    else:
        lines.append("- Rooms: none")
    if facts.plugins:
        lines.append("- Plugins: " + ", ".join(
            f"{p.slug} {p.version}" + ("" if p.enabled else " (disabled)")
            + ("" if p.status == "ok" else f" ({p.status})") for p in facts.plugins))
    else:
        lines.append("- Plugins: none")
    if last_update_status:
        lines.append(f"- Last update: {redact(last_update_status, limit=40)}")
    lines += ["", "### What the checks show", ""]
    if found:
        for f in found:
            lines.append(f"- {rename(f.text)}")
            for d in f.details:
                lines.append(f"  - {rename(d)}")
    else:
        lines.append("- nothing: every check is fine")
    lines += ["", "### What happened", ""]
    said = redact(what_happened, limit=WHAT_MAX).strip()
    lines.append(rename(said) if said else "_(not filled in)_")
    text = "\n".join(lines)
    # The whole text once more, line by line, so nothing slips through a
    # field that was not redacted on its own (a plugin's name, a status).
    # The lines this function wrote itself (the heading, the note, the
    # version, the host kind…) are left as written; everything else, once
    # more, line by line.
    own = {f"## {HEADER}", f"_{NOTE}_", f"- Host: {host or host_kind()}",
           f"- Domovoi version: {facts.version or 'unknown'}"}
    return "\n".join(
        ln if (not ln.strip() or ln in own)
        else ln[: len(ln) - len(ln.lstrip())] + redact(ln.strip(), limit=0)
        for ln in text.splitlines()
    ) + "\n"
