"""PrintingHandler — printing and scanning by voice.

What it owns (plan part 8), all anchored:

* "print my cover letter", "print two copies of the lease, double-sided",
  "print a test page" — every voice print confirms first: "That's 2 pages
  to the office printer. Print it?";
* "stop printing" / "cancel the print";
* "how much ink is left?", "how much toner is left?", "is the printer low
  on ink?";
* "is the printer out of paper?", "is the printer jammed?", "what's the
  printer's status?" — never promising there IS paper (many printers only
  notice an empty tray when they try to print);
* "set up the printer" / "add a printer" / "look for printers": a 3-second
  look, "I found an HP OfficeJet Pro 8020. Add it?", then a test page;
* "scan this to Documents" / "scan this letter": the glass question (eSCL
  has no sensor for it), "another page?", saved to Documents → Scans.

It does NOT own "scan for new music" or "rescan my library" (music, band
300 — excluded here explicitly as well), nor "how much is 5 times 3"
(calculator, band 150, already ahead of it), nor anything that merely
mentions paper or printing in passing ("the paper says…").

Who may: a satellite's turn is a household device (owner: "voice from a
room's satellite counts"); a phone's turn too. Everything said back is a
sentence we chose — printer-supplied strings only through
``speech_sanitize`` and only the printer's given name, never its own
status message.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.confirmations import request_confirmation
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response
from domovoi.speech_sanitize import sanitize_for_speech

log = logging.getLogger(__name__)

PRINT_CONFIRM = "core.print_confirm"
PRINT_PICK = "core.print_pick"
PRINT_PRINTER = "core.print_printer"
PRINTER_ADD = "core.printer_add"
PRINTER_PICK = "core.printer_pick"
SCAN_READY = "core.scan_ready"
SCAN_MORE = "core.scan_more"
SCAN_READ = "core.scan_read"

_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10}

_COPIES = r"(?:(?P<copies>\d{1,3}|a|an|one|two|three|four|five|six|seven|eight|nine|ten) cop(?:y|ies) of )?"
_SIDES = r"(?:,? (?P<sides>double[- ]sided|two[- ]sided|on both sides|single[- ]sided|one[- ]sided))?"
_COLOUR = r"(?:,? in (?P<colour>black and white|colou?r|gr[ae]y(?:scale)?))?"

_TEST_PAGE_RE = re.compile(r"^print (?:a |the )?test page(?: on (?:the |my )?(?P<printer>[\w ]+?))?$")
_PRINT_RE = re.compile(r"^print (?:out )?" + _COPIES + r"(?P<what>.+?)" + _SIDES + _COLOUR + _SIDES.replace(
    "sides", "sides2") + r"$")
_CANCEL_RE = re.compile(
    r"^(?:stop|cancel)(?: the| my)? print(?:ing| job|er)?$|^cancel (?:the |my )?print job$"
)
_INK_RE = re.compile(
    r"^(?:how much (?:ink|toner)(?: is| do (?:i|we) have| does (?:the |my )?(?:[\w]+ )?printer have)?"
    r"(?: left| remaining)?(?: in (?:the |my )?(?:[\w]+ )?printer)?"
    r"|(?:what(?:'s| is| are)|check) (?:the |my )?(?:[\w]+ )?(?:printer'?s? )?(?:ink|toner)(?: levels?)?"
    r"|(?:is|are) (?:the |my )?(?:[\w]+ )?printers? (?:low on|out of|running low on) (?:ink|toner)"
    r"|do (?:i|we) need (?:more )?(?:ink|toner))$"
)
_PAPER_RE = re.compile(
    r"^(?:(?:is|are) (?:the |my )?(?:[\w]+ )?printers? (?:out of paper|low on paper|jammed)"
    r"|does (?:the |my )?(?:[\w]+ )?printer (?:have|need) (?:more )?paper"
    r"|(?:what(?:'s| is)) (?:the |my )?(?:[\w]+ )?printer'?s? status"
    r"|is (?:the |my )?(?:[\w]+ )?printer (?:ok|okay|working|on|ready))$"
)
_SETUP_RE = re.compile(
    r"^(?:(?:set ?up|add|connect|install) (?:a |the |my |our )?(?:new )?printer"
    r"|(?:find|look for) (?:a |the |my )?(?:new )?printers?"
    r"|look again for (?:a |the )?printers?)$"
)
_SCAN_RE = re.compile(
    r"^scan(?: (?:this|it|that|a|the|my|one|another))?"
    r"(?: (?:page|letter|document|receipt|form|paper|sheet))?"
    r"(?: (?:to|into|in) (?:my |the )?documents)?$"
)
# Never ours: the music library's rescans.
_NOT_SCAN_RE = re.compile(r"^scan for |^(?:re)?scan (?:my |the )?(?:music|library|songs|tracks)")

_DONE_RE = re.compile(r"^(?:done|that's it|that is it|that's all|that is all|finished|i'm done|no|nope|"
                      r"no more|no thanks|stop|save it)\b")
_MORE_RE = re.compile(r"^(?:yes|yeah|yep|yup|sure|ok|okay|another|one more|next|ready|go|go ahead)\b")
_ORDINALS = {"first": 0, "1st": 0, "one": 0, "second": 1, "2nd": 1, "two": 1, "third": 2, "3rd": 2, "three": 2,
             "last": -1}


def _said(text: str) -> str:
    return sanitize_for_speech(text)


def _list_words(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return "%s and %s" % (names[0], names[1])
    return ", ".join(names[:-1]) + ", and " + names[-1]


def _number_word(n: int) -> str:
    return {1: "one", 2: "two", 3: "three"}.get(n, str(n))


def _pick_from(transcript: str, names: list[str]) -> int | None:
    t = transcript.lower().strip()
    for word, idx in _ORDINALS.items():
        if re.search(r"\b%s\b" % re.escape(word), t):
            return idx if idx >= 0 else len(names) - 1
    best = None
    for i, n in enumerate(names):
        words = [w for w in re.findall(r"[a-z0-9]+", n.lower()) if len(w) > 2]
        if words and all(w in t for w in words):
            return i
        if words and any(w in t.split() for w in words):
            best = i if best is None else -2
    return best if best is not None and best >= 0 else None


class PrintingHandler(Handler):
    name = "printing"
    # band rationale: after chat_mode (220) and before voice_notes (230)
    # and the Documents plan's documents (225). Its "^print (.+)$" is
    # anchored at the verb; nothing in a later band starts with "print",
    # "scan this" or asks about ink and paper. calculator (150) keeps
    # "how much is …" ahead of the ink question, and music (300) keeps
    # "scan for new music" (excluded here too).
    priority_band = 222
    display = HandlerDisplay(label="Printing", tone="device")
    requires_network = "no"
    confirmation_kinds = (PRINT_CONFIRM, PRINT_PICK, PRINT_PRINTER, PRINTER_ADD, PRINTER_PICK,
                          SCAN_READY, SCAN_MORE, SCAN_READ)
    choice_kinds = (PRINT_PICK, PRINT_PRINTER, PRINTER_PICK, SCAN_MORE)
    example_phrases = (
        "print my cover letter",
        "how much ink is left",
        "is the printer out of paper",
        "set up the printer",
        "scan this to documents",
    )

    tool_schema = {
        "name": "printing",
        "description": (
            "Use the home's network printer or scanner: print a document from Documents, cancel "
            "printing, report ink/toner or paper status, set up a printer, or scan a page to "
            "Documents. Only when the user asks to print, scan, or about the printer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["print", "cancel", "ink", "status", "setup", "scan"]},
                "document": {"type": "string", "description": "The document's name, for action=print."},
                "copies": {"type": "integer"},
            },
            "required": ["action"],
        },
    }

    # A printing request says so: the verb or the device, or a supply
    # question. "the printing press" and "ink" in passing don't count.
    _CUE = re.compile(
        r"\b(?:print(?:ers?)?\b(?! press)|printing\b(?! press)|scan(?:ner|ners)?\b|toner\b"
        r"|ink (?:left|levels?|cartridges?)|out of paper)"
    )

    def offers_tool(self, transcript: str) -> bool:
        return bool(self._CUE.search(transcript)) and not _NOT_SCAN_RE.search(transcript)

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_CANCEL_RE, PrintingHandler._cancel_from_match),
            FastPath(_INK_RE, PrintingHandler._ink_from_match),
            FastPath(_PAPER_RE, PrintingHandler._status_from_match),
            FastPath(_SETUP_RE, PrintingHandler._setup_from_match),
            FastPath(_SCAN_RE, PrintingHandler._scan_from_match),
            FastPath(_TEST_PAGE_RE, PrintingHandler._test_page_from_match),
            # Last: the print verb with anything after it.
            FastPath(_PRINT_RE, PrintingHandler._print_from_match),
        ]

    # ─── plumbing ────────────────────────────────────────────────────────

    def _reply(self, ctx: Context, text: str, *, ask: bool = False, data: dict | None = None) -> Response:
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name,
                        expect_followup=ask, data=data or {})

    async def _ask(self, ctx: Context, session: AsyncSession, kind: str, text: str, data: dict) -> Response:
        if ctx.session_id is None or not ctx.answerable:
            return self._reply(ctx, text.rsplit(".", 1)[0] + ". Say it again with the details, or use Settings, "
                               "Printers and scanners.")
        await request_confirmation(session, ctx.session_id, kind=kind, handler=self.name, data=data, prompt=text)
        return self._reply(ctx, text, ask=True)

    @staticmethod
    def _room_label(ctx: Context) -> str | None:
        room = ctx.satellite_room_id
        return room.replace("_", " ").replace("-", " ") if room else None

    def _who(self, ctx: Context):
        from domovoi.printing.jobs import Who

        room = ctx.satellite_room_id
        label = ("%s (voice)" % self._room_label(ctx)) if room else ("a phone (voice)" if ctx.from_phone else "voice")
        return Who(label=label, device_key="room:%s" % (room or "phone"), source="voice", room_id=room,
                   speaker=str(ctx.person_id) if ctx.person_id else None)

    async def execute(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        return self._reply(ctx, "Say print and the document's name, or scan this to Documents.")

    async def execute_from_tool(self, args: dict, ctx: Context, session: AsyncSession) -> Response | None:
        action = args.get("action")
        if action == "print":
            doc = str(args.get("document") or "").strip()
            if not doc:
                return None
            copies = args.get("copies") if isinstance(args.get("copies"), int) else 1
            return await self._print(doc, {"copies": max(1, copies)}, ctx, session)
        if action == "cancel":
            return await self._cancel(ctx)
        if action == "ink":
            return await self._ink(ctx)
        if action == "status":
            return await self._status(ctx, "")
        if action == "setup":
            return await self._setup(ctx, session, look_again=False)
        if action == "scan":
            return await self._scan(ctx, session)
        return None

    # ─── fast-path adapters ──────────────────────────────────────────────

    async def _cancel_from_match(self, m, ctx, session):
        return await self._cancel(ctx)

    async def _ink_from_match(self, m, ctx, session):
        return await self._ink(ctx)

    async def _status_from_match(self, m, ctx, session):
        return await self._status(ctx, m.group(0))

    async def _setup_from_match(self, m, ctx, session):
        return await self._setup(ctx, session, look_again="again" in m.group(0))

    async def _scan_from_match(self, m, ctx, session):
        if _NOT_SCAN_RE.search(m.group(0)):
            return None
        return await self._scan(ctx, session)

    async def _test_page_from_match(self, m, ctx, session):
        from domovoi.printing import devices, jobs

        try:
            row = await devices.pick_printer(None, ctx.satellite_room_id)
        except devices.AmbiguousPrinter as e:
            return self._reply(ctx, _said("Which printer? Say print a test page on the %s." % e.names[0]))
        except devices.DeviceError as e:
            return self._reply(ctx, _said(self._no_printer_text(str(e))))
        try:
            await jobs.test_page(row["id"], self._who(ctx))
        except jobs.PrintRefused as e:
            return self._reply(ctx, _said(str(e)))
        return self._reply(ctx, _said("Printing a test page on %s." % devices.the(row["name"])))

    async def _print_from_match(self, m, ctx, session):
        what = (m.group("what") or "").strip()
        if _NOT_SCAN_RE.search(what):
            return None
        wanted: dict[str, Any] = {}
        copies = m.group("copies")
        if copies:
            wanted["copies"] = int(copies) if copies.isdigit() else _NUM.get(copies, 1)
        sides = m.group("sides") or m.group("sides2")
        if sides:
            wanted["sides"] = "one-sided" if ("single" in sides or "one" in sides) else "two-sided-long-edge"
        colour = m.group("colour")
        if colour:
            wanted["color"] = "color" if colour.startswith("colo") else "monochrome"
        if what in ("it", "that", "this", "them", "something"):
            return self._reply(ctx, "Which document? Say print and its name, like print my cover letter.")
        return await self._print(what, wanted, ctx, session)

    # ─── printing ────────────────────────────────────────────────────────

    @staticmethod
    def _no_printer_text(reason: str) -> str:
        if reason.startswith("No printer"):
            return ("There's no printer set up. Say set up the printer, or add one in Settings, "
                    "Printers and scanners.")
        return reason

    async def _print(self, what: str, wanted: dict, ctx: Context, session: AsyncSession,
                     doc_rel: str | None = None, printer_id: int | None = None) -> Response:
        from domovoi.printing import devices, lookup

        if doc_rel is None:
            found = lookup.find(what)
            if not found:
                return self._reply(ctx, _said(
                    "I couldn't find a document called %s in Documents. I can only print documents in "
                    "Documents." % what[:80]))
            if len(found) > 1:
                names = [d.title for d in found]
                return await self._ask(
                    ctx, session, PRINT_PICK,
                    _said("I found %s: %s. Which one?" % (_number_word(len(names)), _list_words(names))),
                    {"docs": [{"rel": d.rel, "title": d.title} for d in found], "wanted": wanted},
                )
            doc_rel = found[0].rel
        try:
            row = await devices.pick_printer(printer_id, ctx.satellite_room_id)
            devices.ipp_printer(row)
        except devices.AmbiguousPrinter as e:
            names = e.names[:3]
            return await self._ask(
                ctx, session, PRINT_PRINTER,
                _said("To %s?" % " or ".join(devices.the(n) for n in names)),
                {"doc": doc_rel, "wanted": wanted, "printers": names},
            )
        except devices.DeviceError as e:
            return self._reply(ctx, _said(self._no_printer_text(str(e))))
        return await self._confirm_print(doc_rel, row, wanted, ctx, session)

    async def _confirm_print(self, doc_rel: str, row: dict, wanted: dict, ctx: Context,
                             session: AsyncSession) -> Response:
        from domovoi.printing import jobs, lookup, options, store

        path = lookup.resolve(doc_rel)
        if path is None:
            return self._reply(ctx, "That document isn't in Documents any more.")
        title = path.stem
        try:
            pages, r = await jobs.count_pages(path, filename=path.name, title=title, row=row, wanted=wanted)
            lim = await store.get_limits()
            sheets = options.sheets_for(pages, r.copies, r.sides)
            over = jobs.over_limit_text(pages, r.copies, sheets, lim)
        except jobs.PrintRefused as e:
            return self._reply(ctx, _said(str(e)))
        if over:
            return self._reply(ctx, _said(over))
        parts = ["%d page%s" % (pages, "" if pages == 1 else "s")]
        if r.copies > 1:
            parts.append("%s copies" % _number_word(r.copies))
        if r.sides != "one-sided":
            parts.append("double-sided")
        if r.color == "monochrome" and (row.get("capabilities") or {}).get("color"):
            parts.append("in black and white")
        text = "That's %s%s to %s. Print it?" % (", ".join(parts), "," if len(parts) > 1 else "",
                                                  self._printer_phrase(row))
        return await self._ask(ctx, session, PRINT_CONFIRM, _said(text),
                               {"doc": doc_rel, "printer_id": row["id"], "wanted": r.as_json(), "title": title})

    @staticmethod
    def _printer_phrase(row: dict) -> str:
        from domovoi.printing import devices

        return devices.the(row["name"])

    async def _submit(self, data: dict, ctx: Context) -> Response:
        from domovoi.printing import jobs, lookup

        path = lookup.resolve(str(data.get("doc") or ""))
        if path is None:
            return self._reply(ctx, "That document isn't in Documents any more.")
        try:
            await jobs.submit(path, filename=path.name, title=str(data.get("title") or path.stem),
                              printer_id=data.get("printer_id"), wanted=data.get("wanted") or {},
                              who=self._who(ctx), library_id="core:documents", path=str(data.get("doc")))
        except jobs.PrintRefused as e:
            return self._reply(ctx, _said(str(e)))
        return self._reply(ctx, "Printing.")

    async def _cancel(self, ctx: Context) -> Response:
        from domovoi.printing import jobs, store

        job = await store.last_job_for_room(ctx.satellite_room_id)
        if job is None and ctx.satellite_room_id is None:
            active = await store.active_jobs()
            job = active[-1] if active else None
        if job is None:
            return self._reply(ctx, "Nothing is printing.")
        await jobs.cancel(job["id"])
        return self._reply(ctx, "Canceled.")

    # ─── ink and paper ───────────────────────────────────────────────────

    async def _printers_for_status(self, ctx: Context) -> list[dict]:
        from domovoi.printing import devices, store

        try:
            return [await devices.pick_printer(None, ctx.satellite_room_id)]
        except devices.AmbiguousPrinter:
            return [r for r in await store.list_printers() if r.get("ipp_address")]
        except devices.DeviceError:
            return []

    async def _ink(self, ctx: Context) -> Response:
        from domovoi.printing import capabilities, devices

        rows = await self._printers_for_status(ctx)
        if not rows:
            return self._reply(ctx, self._no_printer_text("No printer"))
        lines = []
        for row in rows[:3]:
            status = await devices.refresh_status(row)
            if status.get("state") == "offline":
                lines.append("%s isn't answering. Is it switched on?" % devices.The(row["name"]))
                continue
            lines.append(capabilities.spoken_supplies(row["name"], status.get("supplies") or []))
        return self._reply(ctx, _said(" ".join(lines)))

    async def _status(self, ctx: Context, said: str) -> Response:
        from domovoi.printing import devices

        rows = await self._printers_for_status(ctx)
        if not rows:
            return self._reply(ctx, self._no_printer_text("No printer"))
        lines = []
        asked_paper = "paper" in said
        asked_jam = "jam" in said
        for row in rows[:3]:
            status = await devices.refresh_status(row)
            state = status.get("state") or "ready"
            words = status.get("words") or []
            if state == "offline":
                lines.append("%s isn't answering. Is it switched on?" % devices.The(row["name"]))
            elif asked_paper:
                if state == "out of paper":
                    lines.append("Yes, %s is out of paper." % devices.the(row["name"]))
                elif state == "low on paper":
                    lines.append("%s is low on paper." % devices.The(row["name"]))
                else:
                    lines.append("%s isn't reporting a paper problem." % devices.The(row["name"]))
            elif asked_jam:
                lines.append("Yes, paper is stuck in %s." % devices.the(row["name"]) if state == "jammed"
                             else "%s isn't reporting a jam." % devices.The(row["name"]))
            elif words:
                lines.append(" ".join(words[:2]))
            else:
                lines.append("%s is %s." % (devices.The(row["name"]), state))
        return self._reply(ctx, _said(" ".join(lines)))

    # ─── set up ──────────────────────────────────────────────────────────

    async def _setup(self, ctx: Context, session: AsyncSession, *, look_again: bool) -> Response:
        from domovoi.printing import discovery

        result = await discovery.look(look_again=look_again)
        fresh = [f for f in result["found"] if f["prints"] and not f["already_added"]]
        if not fresh:
            if any(f["already_added"] for f in result["found"]):
                return self._reply(ctx, "Every printer I can find is already set up.")
            return self._reply(ctx, "I couldn't find a printer. Make sure it's switched on and on the same Wi-Fi, "
                                    "or add it by its address in Settings, Printers and scanners.")
        if len(fresh) == 1:
            f = fresh[0]
            return await self._ask(ctx, session, PRINTER_ADD,
                                   _said("I found %s. Add it?" % _a(f["make_and_model"])),
                                   {"key": f["key"], "model": f["make_and_model"]})
        names = [f["make_and_model"] for f in fresh[:3]]
        return await self._ask(
            ctx, session, PRINTER_PICK,
            _said("I found %s: %s. Which one?" % (_number_word(len(names)), _list_words([_a(n) for n in names]))),
            {"found": [{"key": f["key"], "model": f["make_and_model"]} for f in fresh[:3]]},
        )

    async def _add(self, key: str, ctx: Context) -> Response:
        from domovoi.printing import devices, discovery, jobs

        try:
            row = await discovery.add_found(key, name=None, room_id=ctx.satellite_room_id,
                                            room_label=self._room_label(ctx))
        except devices.DeviceError as e:
            return self._reply(ctx, _said(str(e)))
        text = "Added as %s." % devices.the(row["name"])
        if row.get("ipp_address"):
            try:
                await jobs.test_page(row["id"], self._who(ctx))
                text += " Printing a test page now."
            except jobs.PrintRefused as e:
                text += " " + str(e)
        return self._reply(ctx, _said(text))

    # ─── scanning ────────────────────────────────────────────────────────

    async def _scan(self, ctx: Context, session: AsyncSession) -> Response:
        from domovoi.printing import devices, escl, http11, scans

        try:
            printer, st = await scans.status(None, ctx.satellite_room_id)
        except devices.AmbiguousPrinter as e:
            return self._reply(ctx, _said("Which scanner? Choose a default in Settings, Printers and scanners. "
                                          "I can scan on %s." % " or ".join(devices.the(n) for n in e.names[:3])))
        except devices.DeviceError as e:
            if "no scanner" in str(e).lower():
                return self._reply(ctx, "There's no scanner set up. You can add one in Settings, Printers and "
                                        "scanners.")
            return self._reply(ctx, _said(str(e)))
        except (escl.EsclError, http11.HttpError):
            return self._reply(ctx, "The scanner isn't answering. Is it switched on?")
        name = scans._scanner_name(printer)
        if st.state and st.state not in ("Idle", "Ready"):
            return self._reply(ctx, _said("%s is busy. Try again in a minute." % devices.The(name)))
        sc = printer.get("scan_capabilities") or {}
        if sc.get("adf") and st.adf_loaded:
            try:
                s = await scans.start(printer["id"], source="adf", who=self._who(ctx).label, origin="voice",
                                      room_id=ctx.satellite_room_id)
                s = await scans.finish(s.id)
            except scans.ScanRefused as e:
                return self._reply(ctx, _said(str(e)))
            return await self._saved(s, ctx, session, prefix="Scanning the pages in the feeder. ")
        if not sc.get("platen"):
            return self._reply(ctx, "Put the pages in the feeder, then ask again.")
        return await self._ask(ctx, session, SCAN_READY, "Is the page face down on the glass?",
                               {"printer_id": printer["id"]})

    async def _scan_glass(self, data: dict, ctx: Context, session: AsyncSession) -> Response:
        from domovoi.printing import scans

        try:
            s = await scans.start(data.get("printer_id"), source="platen", who=self._who(ctx).label,
                                  origin="voice", room_id=ctx.satellite_room_id)
        except scans.ScanRefused as e:
            return self._reply(ctx, _said(str(e)))
        return await self._ask(ctx, session, SCAN_MORE,
                               "Got it. Another page? Put it on the glass and say yes, or say done.",
                               {"scan": s.id})

    async def _scan_more(self, data: dict, ctx: Context, session: AsyncSession) -> Response:
        from domovoi.printing import scans

        try:
            s = await scans.more(str(data.get("scan") or ""))
        except scans.ScanRefused as e:
            return self._reply(ctx, _said(str(e)))
        if s.error:
            return await self._ask(ctx, session, SCAN_MORE, _said(s.error + " Say done to save what I have."),
                                   {"scan": s.id})
        return await self._ask(ctx, session, SCAN_MORE,
                               "Got it. Another page? Put it on the glass and say yes, or say done.",
                               {"scan": s.id})

    async def _scan_done(self, data: dict, ctx: Context, session: AsyncSession) -> Response:
        from domovoi.printing import scans

        try:
            s = await scans.finish(str(data.get("scan") or ""))
        except scans.ScanRefused as e:
            return self._reply(ctx, _said(str(e)))
        return await self._saved(s, ctx, session)

    async def _saved(self, s, ctx: Context, session: AsyncSession, prefix: str = "") -> Response:
        from domovoi.printing import scans

        spoken = _spoken_scan_name(s.name or "")
        text = "%sSaved to Documents as %s, %d page%s." % (prefix, spoken, len(s.pages), "" if len(s.pages) == 1
                                                           else "s")
        if s.incomplete:
            text += " Some pages may be missing."
        if scans.reader() is not None and s.path:
            return await self._ask(ctx, session, SCAN_READ, text + " Want me to tell you what it says?",
                                   {"path": s.path})
        return self._reply(ctx, text)

    # ─── confirmations and choices ───────────────────────────────────────

    async def handle_confirmation(self, kind: str, data: dict, affirmative: bool, ctx: Context,
                                  session: AsyncSession) -> Response:
        if kind == PRINT_CONFIRM:
            return await self._submit(data, ctx) if affirmative else self._reply(ctx, "OK, not printing.")
        if kind == PRINTER_ADD:
            return await self._add(str(data.get("key") or ""), ctx) if affirmative else \
                self._reply(ctx, "OK, I won't add it.")
        if kind == SCAN_READY:
            return await self._scan_glass(data, ctx, session) if affirmative else \
                self._reply(ctx, "OK. Put the page face down on the glass, then say scan this again.")
        if kind == SCAN_MORE:
            return await (self._scan_more(data, ctx, session) if affirmative else
                          self._scan_done(data, ctx, session))
        if kind == SCAN_READ:
            if not affirmative:
                return self._reply(ctx, "OK. It's in Documents, under Scans.")
            from domovoi.printing import scans

            reader = scans.reader()
            if reader is None:
                return self._reply(ctx, "It's in Documents, under Scans.")
            try:
                said = await reader(str(data.get("path") or ""), {"room_id": ctx.satellite_room_id})
            except Exception as e:  # noqa: BLE001 — the reader belongs to another feature
                log.warning("printing: the scan reader failed: %s", e)
                return self._reply(ctx, "I couldn't read it just now. It's in Documents, under Scans.")
            return self._reply(ctx, said)
        if kind in (PRINT_PICK, PRINT_PRINTER, PRINTER_PICK):
            if not affirmative:
                return self._reply(ctx, "OK.")
            return self._reply(ctx, "Which one? Say its name.")
        return self._reply(ctx, "OK.")

    async def handle_choice_reply(self, kind: str, data: dict, transcript: str, ctx: Context,
                                  session: AsyncSession) -> Response | None:
        t = transcript.lower().strip()
        if kind == SCAN_MORE:
            if _DONE_RE.match(t):
                return await self._scan_done(data, ctx, session)
            if _MORE_RE.match(t):
                return await self._scan_more(data, ctx, session)
            return None
        if re.match(r"^(?:no|nope|cancel|never mind|nevermind|neither|none)\b", t):
            return self._reply(ctx, "OK.")
        if kind == PRINT_PICK:
            docs = [d for d in data.get("docs") or [] if isinstance(d, dict)]
            i = _pick_from(t, [d.get("title", "") for d in docs])
            if i is None:
                return None
            return await self._print("", dict(data.get("wanted") or {}), ctx, session, doc_rel=docs[i]["rel"])
        if kind == PRINT_PRINTER:
            from domovoi.printing import store

            names = [str(n) for n in data.get("printers") or []]
            i = _pick_from(t, names)
            if i is None:
                return None
            rows = {r["name"]: r for r in await store.list_printers()}
            row = rows.get(names[i])
            if row is None:
                return self._reply(ctx, "That printer isn't set up any more.")
            return await self._confirm_print(str(data.get("doc") or ""), row, dict(data.get("wanted") or {}),
                                             ctx, session)
        if kind == PRINTER_PICK:
            found = [f for f in data.get("found") or [] if isinstance(f, dict)]
            i = _pick_from(t, [f.get("model", "") for f in found])
            if i is None:
                return None
            return await self._add(str(found[i].get("key") or ""), ctx)
        return None


def _a(model: str) -> str:
    m = (model or "a printer").strip()
    if m.lower().startswith(("a ", "an ", "the ")):
        return m
    return ("an " if m[:1].lower() in "aeiou" or m[:2].upper() == "HP" else "a ") + m


def _spoken_scan_name(name: str) -> str:
    """ "Scan 2026-10-21 15.40.pdf" -> "Scan, 3:40 PM"."""
    m = re.match(r"^Scan \d{4}-\d{2}-\d{2} (\d{2})\.(\d{2})", name)
    if not m:
        return name.rsplit(".", 1)[0]
    h, mi = int(m.group(1)), int(m.group(2))
    return "Scan, %d:%02d %s" % ((h % 12) or 12, mi, "AM" if h < 12 else "PM")
