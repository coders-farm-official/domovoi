package com.domovoi.app.ui.screens.printing

import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import com.domovoi.app.ui.components.Tone
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The printing/scanning shapes from the API contract, and the pure decisions on them. */
class PrintModelsTest {

    private val printerJson = """
        {
          "id": 3, "name": "office printer", "make_and_model": "Brother HL-L3220CDW series",
          "location": "upstairs", "address": "192.168.1.50", "uri": "ipp://192.168.1.50/ipp/print",
          "added_via": "multicast", "can_print": true, "can_scan": false, "color": true, "duplex": true,
          "is_default": true, "rooms": ["kitchen"], "two_sided_default": null, "color_default": false,
          "defaults": {"sides": "one-sided", "color": "color", "media": "na_letter_8.5x11in"},
          "choices": {
            "sides": ["one-sided", "two-sided-long-edge", "two-sided-short-edge"],
            "color": ["color", "monochrome"],
            "media": [{"id": "na_letter_8.5x11in", "label": "Letter"}, {"id": "iso_a4_210x297mm", "label": "A4"}],
            "copies_max": 20
          },
          "status": {"state": "out of paper", "tone": "warn", "words": ["The office printer is out of paper."],
                     "message": "", "checked_at": "2026-10-21T15:40:00Z"},
          "supplies": [{"name": "Black toner", "level": 60, "color": "#000000", "kind": "toner", "low": false}],
          "supplies_reported": true, "admin_url": "http://192.168.1.50/", "needs_attention": null,
          "identify": true, "scan": null, "active_job": null, "some_future_key": 1
        }
    """.trimIndent()

    private val printersJson = """
        {
          "printers": [$printerJson],
          "has_printer": true, "has_scanner": false, "default_printer_id": 3,
          "rooms": [{"id": "kitchen", "label": "Kitchen"}],
          "printable": {
            "exts": [".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".md", ".txt", ".csv", ".xlsx"],
            "pdf_first_exts": [".docx", ".odt", ".doc", ".rtf"],
            "pdf_first_message": "To print a Word file, save it as PDF in Word or Docs, then print the PDF."
          },
          "limits": {"max_pages_per_job": 100},
          "discovery": null
        }
    """.trimIndent()

    private fun printers(): PrintersResponse = DomovoiJson.parseToJsonElement(printersJson).decode()

    @Test fun printersParse() {
        val r = printers()
        assertTrue(r.hasPrinter)
        assertFalse(r.hasScanner)
        assertEquals(3L, r.defaultPrinterId)
        assertEquals("Kitchen", r.rooms.single().label)
        val p = r.printers.single()
        assertEquals("office printer", p.name)
        assertEquals("Brother HL-L3220CDW series", p.makeAndModel)
        assertNull(p.twoSidedDefault)
        assertEquals(false, p.colorDefault)
        assertEquals(20, p.choices.copiesMax)
        assertEquals("A4", p.choices.media[1].label)
        assertEquals("out of paper", p.status.state)
        assertEquals(Tone.Warn, toneOf(p.status.tone))
        assertEquals(60, p.supplies.single().level)
        assertTrue(p.suppliesReported)
        assertTrue(p.identify)
        assertNull(p.scan)
        assertNull(r.discovery)
    }

    @Test fun discoverParses() {
        val r: DiscoverResponse = DomovoiJson.parseToJsonElement(
            """
            {"found": [{"key": "opaque-string", "make_and_model": "HP OfficeJet Pro 8020", "location": "",
                        "address": "192.168.1.51", "prints": true, "scans": true, "color": true, "duplex": true,
                        "via": "multicast", "already_added": null},
                       {"key": "k2", "address": "192.168.1.52", "via": "sweep", "already_added": 3}],
             "diagnostics": {"ran_at": "2026-10-21T15:40:00Z", "found": 2, "by_multicast": 2, "by_unicast": 0,
                             "by_sweep": 0, "typed": 1, "sweep": "rate_limited", "sweep_retry_sec": 40,
                             "errors": ["multicast: couldn't listen on port 5353 (address in use)"]}}
            """.trimIndent(),
        ).decode()
        assertEquals(2, r.found.size)
        assertNull(r.found[0].alreadyAdded)
        assertEquals(3L, r.found[1].alreadyAdded)
        assertTrue(r.found[0].scans)
        val d = r.diagnostics!!
        assertEquals(
            "last look: found 2 printers — 2 by multicast, 0 by unicast, 0 by sweep; 1 added by address",
            diagnosticsLine(d),
        )
        assertEquals(
            listOf(
                "the network sweep runs at most once a minute — try again in 40 seconds",
                "multicast: couldn't listen on port 5353 (address in use)",
            ),
            diagnosticsNotes(d),
        )
    }

    @Test fun diagnosticsLineSingularAndNoTyped() {
        val d = DiscoveryDiagnostics(found = 1, byMulticast = 0, byUnicast = 1, bySweep = 0, typed = 0, sweep = "ran")
        assertEquals("last look: found 1 printer — 0 by multicast, 1 by unicast, 0 by sweep", diagnosticsLine(d))
        assertTrue(diagnosticsNotes(d).isEmpty())
    }

    @Test fun previewParses() {
        val p: PrintPreview = DomovoiJson.parseToJsonElement(
            """
            {"printer_id": 3, "printer_name": "office printer", "title": "Cover letter", "kind": "pdf",
             "pages_total": 3, "pages": [1, 2, 3], "copies": 1, "sheets": 2,
             "options": {"pages": null, "copies": 1, "sides": "two-sided-long-edge", "color": "color", "media": "na_letter_8.5x11in"},
             "summary": "3 pages · 2 sheets of paper · two-sided",
             "thumbs": [{"page": 1, "src": "data:image/png;base64,iVBORw0KGgo="}],
             "notes": ["Printed as a plain table."],
             "adjustments": ["This printer can't print two-sided, so it will print one-sided."],
             "over_limit": "That's 240 pages, more than this house allows in one job (100). An admin can change the limit.",
             "choices": {"sides": ["one-sided"], "color": ["color", "monochrome"], "media": [], "copies_max": 20}}
            """.trimIndent(),
        ).decode()
        assertEquals(3, p.pagesTotal)
        assertEquals("two-sided-long-edge", p.options.sides)
        assertNull(p.options.pages)
        assertEquals("iVBORw0KGgo=", dataUriBase64(p.thumbs.single().src))
        assertTrue(p.overLimit!!.startsWith("That's 240 pages"))
        val c = printControls(p.choices, printerCount = 1, pagesTotal = p.pagesTotal)
        assertEquals(PrintControls(printer = false, pages = true, copies = true, sides = false, color = true, media = false), c)
    }

    @Test fun jobAndScanParse() {
        val j: PrintJob = DomovoiJson.parseToJsonElement(
            """
            {"id": 41, "printer_id": 3, "printer_name": "office printer", "title": "Cover letter",
             "pages": 3, "copies": 1, "sheets": 2, "options": {},
             "state": "printing", "pages_done": 1, "progress": "printing · 1 of 3",
             "error": null, "who": "Kitchen tablet", "source": "app",
             "created_at": "2026-10-21T15:40:00Z", "finished_at": null, "can_cancel": true}
            """.trimIndent(),
        ).decode()
        assertEquals(41L, j.id)
        assertFalse(j.finished)
        assertTrue(j.canCancel)
        val jobs: PrintJobsResponse = DomovoiJson.parseToJsonElement("""{"jobs": [{"id": 1, "state": "done"}]}""").decode()
        assertTrue(jobs.jobs.single().finished)

        val s: ScanJob = DomovoiJson.parseToJsonElement(
            """
            {"id": "b1c2", "printer_id": 3, "scanner_name": "office printer", "state": "waiting",
             "source_used": "platen", "pages": 2, "thumbs": [{"page": 1, "src": "data:image/jpeg;base64,/9j/4AAQ"}],
             "can_scan_more": true, "name": null, "library_id": "core:documents", "path": null,
             "incomplete": false, "error": null, "readable": false}
            """.trimIndent(),
        ).decode()
        assertEquals("b1c2", s.id)
        assertFalse(s.busy)
        assertFalse(s.finished)
        assertTrue(s.canScanMore)
        assertEquals("/9j/4AAQ", dataUriBase64(s.thumbs.single().src))
    }

    @Test fun printOfferDecision() {
        val r = printers()
        assertEquals(PrintOffer.Print, printOffer("Cover letter.PDF", false, r))
        assertEquals(PrintOffer.Print, printOffer("budget.xlsx", false, r))
        assertEquals(PrintOffer.Print, printOffer("notes.md", false, r))
        assertEquals(
            PrintOffer.PdfFirst("To print a Word file, save it as PDF in Word or Docs, then print the PDF."),
            printOffer("letter.docx", false, r),
        )
        assertEquals(PrintOffer.None, printOffer("song.mp3", false, r))
        assertEquals(PrintOffer.None, printOffer("drawing.svg", false, r))
        assertEquals(PrintOffer.None, printOffer("README", false, r))
        assertEquals(PrintOffer.None, printOffer("Letters.pdf", isDir = true, printers = r))
        // No printer saved, or not known yet: nothing, not even the PDF line.
        assertEquals(PrintOffer.None, printOffer("a.pdf", false, r.copy(hasPrinter = false)))
        assertEquals(PrintOffer.None, printOffer("letter.docx", false, r.copy(hasPrinter = false)))
        assertEquals(PrintOffer.None, printOffer("a.pdf", false, null))
    }

    @Test fun controlsShowOnlyWhenThereIsAChoice() {
        val full = printers().printers.single().choices
        assertEquals(
            PrintControls(printer = true, pages = true, copies = true, sides = true, color = true, media = true),
            printControls(full, printerCount = 2, pagesTotal = 3),
        )
        val bare = PrintChoices(sides = listOf("one-sided"), color = listOf("monochrome"),
            media = listOf(PrintMedia("iso_a4_210x297mm", "A4")), copiesMax = 1)
        assertEquals(
            PrintControls(printer = false, pages = false, copies = false, sides = false, color = false, media = false),
            printControls(bare, printerCount = 1, pagesTotal = 1),
        )
        // Pages unknown yet: the field shows.
        assertTrue(printControls(null, 1, null).pages)
    }

    @Test fun scanControls() {
        val caps = ScanCaps(sources = listOf("platen", "adf", "adf_duplex"), color = listOf("color", "grey"), resolutions = listOf(150, 600))
        val c = scanControls(caps, scannerCount = 1)
        assertFalse(c.scanner)
        assertEquals(listOf("auto", "platen", "adf", "adf_duplex"), c.sources)
        assertEquals(listOf("color", "grey"), c.colors)
        assertEquals(150, scanResolution(caps))
        assertEquals(300, scanResolution(caps.copy(resolutions = listOf(150, 300, 600))))
        val glassOnly = scanControls(ScanCaps(sources = listOf("platen"), color = listOf("grey")), scannerCount = 2)
        assertTrue(glassOnly.scanner)
        assertTrue(glassOnly.sources.isEmpty())
        assertTrue(glassOnly.colors.isEmpty())
        assertNull(scanResolution(null))
    }

    @Test fun whatDoesItSayOnlyWhenReadable() {
        val done = ScanJob(id = "x", state = "done", name = "Scan 2026-10-21 15.40.pdf", path = "Scans/Scan 2026-10-21 15.40.pdf")
        assertFalse(offersWhatDoesItSay(done))
        assertTrue(offersWhatDoesItSay(done.copy(readable = true)))
        assertFalse(offersWhatDoesItSay(done.copy(readable = true, state = "waiting")))
        assertEquals("Saved to Documents as Scan 2026-10-21 15.40.pdf", scanEndMessage(done))
        assertEquals("Scan canceled; nothing was saved.", scanEndMessage(done.copy(state = "canceled")))
        assertEquals("The feeder is jammed.", scanEndMessage(done.copy(state = "failed", error = "The feeder is jammed.")))
    }

    @Test fun jobEndMessages() {
        val j = PrintJob(id = 1, title = "Cover letter", pages = 2, state = "done")
        assertEquals("Printed: Cover letter (2 pages)", jobEndMessage(j))
        assertEquals("Printed: Cover letter (1 page)", jobEndMessage(j.copy(pages = 1)))
        assertEquals("The office printer is out of paper.", jobEndMessage(j.copy(state = "failed", error = "The office printer is out of paper.")))
        assertEquals("Canceled: Cover letter", jobEndMessage(j.copy(state = "canceled")))
    }

    @Test fun wordsAndColours() {
        assertEquals("two-sided (long edge)", sidesLabel("two-sided-long-edge"))
        assertEquals("black and white", colorLabel("monochrome"))
        assertEquals("colour", colorLabel("color"))
        assertEquals("glass", sourceLabel("platen"))
        assertEquals("follow the printer", twoSidedDefaultLabel(null))
        assertEquals("black and white", colorDefaultLabel(false))
        assertEquals(0xFF000000L, supplyArgb("#000000"))
        assertEquals(0xFFFF00FFL, supplyArgb("#f0f"))
        assertNull(supplyArgb("#zzzzzz"))
        assertNull(supplyArgb(null))
        assertEquals(0.6f, supplyFraction(PrinterSupply(level = 60))!!, 0.0001f)
        assertNull(supplyFraction(PrinterSupply(level = -2)))
        assertNull(dataUriBase64("http://192.168.1.50/thumb.png"))
        assertNull(dataUriBase64("data:image/png,raw"))
    }

    @Test fun printBodyKeepsNullPrinterAndDropsBlankOptions() {
        val b = printBody("core:documents", "Letters/Cover letter.pdf", null, PrintOptions(pages = "  ", copies = 2, sides = "one-sided"))
        assertEquals("core:documents", b["library_id"]!!.jsonPrimitive.content)
        assertEquals(JsonNull, b["printer_id"])
        val o = b["options"]!!.jsonObject
        assertEquals(setOf("copies", "sides"), o.keys)
    }

    @Test fun serverDetailIsTheServersWords() {
        val e = ApiException(400, "400 Bad Request", body = """{"detail": "That address isn't on your home network."}""")
        assertEquals("That address isn't on your home network.", serverDetail(e))
        assertEquals("That address isn't on your home network.", printFailureText("add", e))
        assertNull(serverDetail(ApiException(500, "500", body = "oops")))
    }
}
