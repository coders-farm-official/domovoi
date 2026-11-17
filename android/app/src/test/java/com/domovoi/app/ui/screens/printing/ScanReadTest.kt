package com.domovoi.app.ui.screens.printing

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DEVICE_TOKEN_HEADER
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.io.IOException
import java.net.SocketTimeoutException
import java.util.concurrent.CopyOnWriteArrayList

/** "What does it say?" for a scan: the gate, the words, the following, the sheet's model and the requests. */
class ScanReadTest {

    private fun read(
        state: String,
        pages: Int = 0,
        done: Int = 0,
        readable: Boolean = false,
        voice: Boolean = false,
        error: String? = null,
    ) = ScanRead(
        id = "0123456789abcdef0123456789abcdef", path = "Scans/Scan 2026-10-21 15.40.pdf", state = state,
        pages = pages, pagesDone = done, waitingForVoice = voice, readable = readable,
        reading = state == "queued" || state == "running", error = error,
    )

    private fun saved(r: ScanRead?, readable: Boolean = r?.readable == true) =
        ScanJob(id = "s1", state = "done", pages = 2, name = "Scan 2026-10-21 15.40", path = r?.path, read = r, readable = readable)

    // --- the gate -----------------------------------------------------------

    @Test fun shownOnceReadAndFoundWords() {
        assertTrue(whatDoesItSayShown(saved(read("done", 2, 2, readable = true))))
    }

    @Test fun shownWhileQueuedOrRunning() {
        assertTrue(whatDoesItSayShown(saved(read("queued"))))
        assertTrue(whatDoesItSayShown(saved(read("running", 3, 1))))
        assertTrue(whatDoesItSayShown(saved(read("running", voice = true))))
    }

    @Test fun hiddenWhenNeverRead() {
        // An old scan, or a server that can't read scans: no reading at all.
        assertFalse(whatDoesItSayShown(saved(null)))
        assertFalse(whatDoesItSayShown(ScanJob(id = "s1", state = "scanning")))
    }

    @Test fun hiddenWhenTheReadFailedWasStoppedOrFoundNoWords() {
        assertFalse(whatDoesItSayShown(saved(read("failed", error = "That scan isn't in Documents any more."))))
        assertFalse(whatDoesItSayShown(saved(read("cancelled"))))
        assertFalse(whatDoesItSayShown(saved(read("done", 1, 1, readable = false))))
    }

    // --- the words ----------------------------------------------------------

    @Test fun labelCarriesTheProgressWhileReading() {
        assertEquals("What does it say?", whatDoesItSayLabel(saved(read("done", 2, 2, readable = true))))
        assertEquals("What does it say? (waiting to be read)", whatDoesItSayLabel(saved(read("queued"))))
        assertEquals("What does it say? (reading page 2 of 3)", whatDoesItSayLabel(saved(read("running", 3, 1))))
        assertEquals(
            "What does it say? (paused while someone talks to Domovoi)",
            whatDoesItSayLabel(saved(read("running", 3, 1, voice = true))),
        )
    }

    @Test fun progressPageNeverPassesTheCount() {
        assertEquals("reading page 3 of 3", scanReadProgress(read("running", 3, 3)))
        assertEquals("reading page 1 of 4", scanReadProgress(read("running", 4, 0)))
        assertEquals("reading…", scanReadProgress(read("running", 0, 0)))
        assertEquals("", scanReadProgress(null))
    }

    @Test fun scanLogLineReadsLikeTheDashboard() {
        val s = saved(read("done", 2, 2, readable = true)).copy(who = "Alex")
        assertEquals("scan · done · 2 pages · Scan 2026-10-21 15.40 · Alex", scanLogLine(s))
        assertEquals("scan · failed · 1 page", scanLogLine(ScanJob(state = "failed", pages = 1)))
    }

    // --- the shapes ---------------------------------------------------------

    @Test fun scanRowAndJobLogCarryTheReading() {
        val json = """
            {"jobs": [], "scans": [{"id": "ab12", "scanner_name": "office", "state": "done", "pages": 3,
              "name": "Scan 2026-10-21 15.40", "path": "Scans/Scan 2026-10-21 15.40.pdf", "incomplete": false,
              "error": null, "who": "Alex", "source": "app", "created_at": "2026-10-21T15:40:00Z",
              "finished_at": "2026-10-21T15:41:00Z", "readable": false,
              "read": {"id": "0123456789abcdef0123456789abcdef", "path": "Scans/Scan 2026-10-21 15.40.pdf",
                       "state": "running", "pages": 3, "pages_done": 1, "pages_guessed": 0, "pages_unread": 0,
                       "waiting_for_voice": false, "readable": false, "reading": true, "error": null,
                       "error_code": null, "created_at": "2026-10-21T15:41:00Z", "finished_at": null,
                       "a_future_key": 1}}]}
        """.trimIndent()
        val log: PrintJobsResponse = DomovoiJson.parseToJsonElement(json).decode()
        val s = log.scans.single()
        assertEquals("Alex", s.who)
        assertEquals("running", s.read?.state)
        assertEquals(1, s.read?.pagesDone)
        assertTrue(whatDoesItSayShown(s))
        assertEquals("What does it say? (reading page 2 of 3)", whatDoesItSayLabel(s))
        // An older server: no `read`, no `readable`.
        val old: ScanJob = DomovoiJson.parseToJsonElement("""{"id": "x", "state": "done"}""").decode()
        assertNull(old.read)
        assertFalse(whatDoesItSayShown(old))
    }

    // --- polling: start and stop by state -----------------------------------

    @Test fun scanSheetPollsWhileScanningSavingOrBeingRead() {
        assertTrue(scanSheetPolls(ScanJob(state = "scanning")))
        assertTrue(scanSheetPolls(ScanJob(state = "saving")))
        assertTrue(scanSheetPolls(saved(read("queued"))))
        assertTrue(scanSheetPolls(saved(read("running", 2, 1))))
        assertFalse(scanSheetPolls(null))
        assertFalse(scanSheetPolls(ScanJob(state = "waiting")))
        assertFalse(scanSheetPolls(saved(null)))
        assertFalse(scanSheetPolls(saved(read("done", 2, 2, readable = true))))
        assertFalse(scanSheetPolls(saved(read("failed"))))
        assertFalse(scanSheetPolls(ScanJob(state = "failed")))
        assertFalse(scanSheetPolls(ScanJob(state = "canceled")))
    }

    @Test fun theSheetPollsOnlyWhileTheReadIsQueuedOrRunning() {
        assertTrue(ScanReadView(read("queued")).polls)
        assertTrue(ScanReadView(read("running", 2, 1)).polls)
        assertFalse(ScanReadView(read("done", 2, 2, readable = true)).polls)
        assertFalse(ScanReadView(read("failed")).polls)
        assertFalse(ScanReadView(read("cancelled")).polls)
        assertFalse(ScanReadView(null).polls)
    }

    @Test fun followAsksAgainUntilTheReadEnds() = runBlocking {
        val answers = ArrayDeque(listOf(read("running", 2, 0), read("running", 2, 1), read("done", 2, 2, readable = true)))
        var pauses = 0
        val seen = mutableListOf<String>()
        val last = follow(read("queued"), { ScanReadView(it).polls }, { answers.removeFirst() }, { pauses++ }) {
            seen += ScanReadView(it).progress ?: it.state
        }
        assertEquals("done", last.state)
        assertEquals(3, pauses)
        assertEquals(listOf("reading page 1 of 2", "reading page 2 of 2", "done"), seen)
        assertTrue(answers.isEmpty())
    }

    @Test fun followNeverAsksForAnEndedRead() = runBlocking {
        var fetched = 0
        val start = read("done", 1, 1, readable = true)
        assertEquals(start, follow(start, { ScanReadView(it).polls }, { fetched++; start }, {}) {})
        assertEquals(0, fetched)
    }

    @Test fun followSkipsAFailedAskAndStopsOnAFailedRead() = runBlocking {
        var calls = 0
        val seen = mutableListOf<String>()
        val last = follow(read("queued"), { ScanReadView(it).polls }, {
            calls++
            if (calls == 1) throw IOException("dropped")
            read("failed", error = "That scan couldn't be read.")
        }, {}) { seen += it.state }
        assertEquals(2, calls)
        assertEquals(listOf("failed"), seen)
        assertEquals("failed", last.state)
    }

    @Test fun followCarriesTheScanSheetFromSavingThroughTheRead() = runBlocking {
        val answers = ArrayDeque(listOf(saved(read("queued")), saved(read("done", 1, 1, readable = true))))
        val last = follow(ScanJob(id = "s1", state = "saving"), ::scanSheetPolls, { answers.removeFirst() }, {}) {}
        assertTrue(last.readable)
        assertTrue(answers.isEmpty())
    }

    // --- the sheet: summary, then the full text ------------------------------

    @Test fun summaryIsAskedForOnceTheReadIsDone() {
        val reading = ScanReadView(read("running", 2, 1))
        assertFalse(reading.wantsSummary)
        assertEquals("reading page 2 of 2", reading.progress)
        assertFalse(reading.offersFullText)
        val done = reading.withRead(read("done", 2, 2, readable = true))
        assertTrue(done.wantsSummary)
        assertTrue(done.summingUp)
        assertNull(done.progress)
        assertTrue(done.offersFullText)
    }

    @Test fun aSummaryShowsAsMarkdownThenTheFullTextOnRequest() {
        val v = ScanReadView(read("done", 2, 2, readable = true)).withSummary(
            ScanReadSummary(
                ai = true, content = "**From:** the water company\n\nPay by 3 November.", said = "It's from the water company.",
                notes = listOf(ScanReadNote("scan_pages_guessed", "Page 2 was hard to read; it shows what it might say.")),
            ),
        )
        assertFalse(v.wantsSummary)
        assertEquals("**From:** the water company\n\nPay by 3 November.", v.markdown)
        assertNull(v.plainSummary)
        assertNull(v.aiOffNote)
        assertEquals(listOf("Page 2 was hard to read; it shows what it might say."), v.notes)
        assertTrue(v.offersFullText)
        val all = v.withFullText(ScanReadText(text = "Dear Alex,\n\nYour bill is ready."))
        assertEquals("Dear Alex,\n\nYour bill is ready.", all.fullText)
        assertFalse(all.offersFullText)
        assertEquals(v.markdown, all.markdown)
    }

    @Test fun withAiOffTheStartOfTheTextShowsWithItsNote() {
        val v = ScanReadView(read("done", 1, 1, readable = true))
            .withSummary(ScanReadSummary(ai = false, content = null, said = "Here's how it starts: Dear Alex"))
        assertNull(v.markdown)
        assertEquals("Here's how it starts: Dear Alex", v.plainSummary)
        assertEquals("AI features are off, so there's no summary: this is the start of the text.", v.aiOffNote)
    }

    @Test fun aFailedModelShowsTheSaidWordsWithoutTheAiOffNote() {
        val v = ScanReadView(read("done", 1, 1, readable = true))
            .withSummary(ScanReadSummary(ai = true, content = null, said = "Here's how it starts: Dear Alex", failed = true))
        assertEquals("Here's how it starts: Dear Alex", v.plainSummary)
        assertNull(v.aiOffNote)
    }

    @Test fun aFailedReadShowsItsOwnSentence() {
        assertEquals(
            "That scan isn't in Documents any more.",
            ScanReadView(read("failed", error = "That scan isn't in Documents any more.")).endedLine,
        )
        assertEquals("This scan couldn't be read.", ScanReadView(read("failed")).endedLine)
        val v = ScanReadView(read("failed"))
        assertFalse(v.wantsSummary)
        assertFalse(v.offersFullText)
        assertNull(ScanReadView(read("done", 1, 1, readable = true)).endedLine)
    }

    // --- errors: the server's detail ------------------------------------------

    @Test fun errorsShowTheServersDetailElseTheFallback() {
        val still = ApiException(409, "409 Conflict", body = """{"detail":"Domovoi is still reading this scan."}""")
        val v = ScanReadView(read("done", 1, 1, readable = true)).withError(still, SCAN_READ_SUMMARY_FAILED)
        assertEquals("Domovoi is still reading this scan.", v.error)
        // A failed summary is not asked for again in a loop.
        assertFalse(v.wantsSummary)
        assertFalse(v.summingUp)
        assertEquals(
            "Domovoi couldn't sum it up just now.",
            ScanReadView(read("done")).withError(IOException("reset"), SCAN_READ_SUMMARY_FAILED).error,
        )
        assertEquals(
            "The text couldn't be loaded.",
            ScanReadView(read("done")).withError(ApiException(502, "502", body = "<html>"), SCAN_READ_TEXT_FAILED).error,
        )
    }

    @Test fun aTimedOutSummaryIsAskedAgainAndOtherFailuresAreNot() = runBlocking {
        var n = 0
        assertEquals("ok", askAgainOnTimeout { if (++n < 3) throw SocketTimeoutException("slow") else "ok" })
        assertEquals(3, n)
        n = 0
        try {
            askAgainOnTimeout { n++; throw SocketTimeoutException("slow") }
            fail("expected a timeout")
        } catch (_: SocketTimeoutException) {}
        assertEquals(3, n)
        n = 0
        try {
            askAgainOnTimeout { n++; throw IOException("refused") }
            fail("expected the failure")
        } catch (_: IOException) {}
        assertEquals(1, n)
    }

    // --- the requests, against a fake web backend -----------------------------

    private lateinit var server: MockWebServer
    private lateinit var io: PrintIo
    private val seen = CopyOnWriteArrayList<RecordedRequest>()
    private val answers = mutableMapOf<String, MockResponse>()

    @Before fun up() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                seen += request
                val key = "${request.method} ${request.requestUrl?.encodedPath}"
                return answers[key] ?: MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}""")
            }
        }
        server.start()
        val url = server.url("/").toString().trimEnd('/')
        io = PrintIo(ApiClient(baseUrlProvider = { url }, deviceTokenProvider = { "household-token-1234" }))
    }

    @After fun down() = server.shutdown()

    private val readJson = """{"id":"0123456789abcdef0123456789abcdef","path":"Scans/Letters & bills/Scan +1 2026-10-21.pdf",
        "state":"queued","pages":0,"pages_done":0,"pages_guessed":0,"pages_unread":0,"waiting_for_voice":false,
        "readable":false,"reading":true,"error":null,"error_code":null,"created_at":null,"finished_at":null}"""

    @Test fun nestedPathsWithSpacesAreOneEscapedQueryValue() {
        assertEquals(
            "/api/scan/reads?path=Scans%2FScan%202026-10-21%2015.40.pdf",
            scanReadLookupPath("Scans/Scan 2026-10-21 15.40.pdf"),
        )
        assertEquals(
            "/api/scan/reads?path=Scans%2FLetters%20%26%20bills%2FScan%20%2B1.pdf",
            scanReadLookupPath("Scans/Letters & bills/Scan +1.pdf"),
        )
        assertEquals("/api/scan/reads/0123456789abcdef0123456789abcdef", scanReadPath("0123456789abcdef0123456789abcdef"))
        assertEquals("/api/scan/reads/abc/summary", scanReadSummaryPath("abc"))
        assertEquals("/api/scan/reads/abc/text", scanReadTextPath("abc"))
    }

    @Test fun lookupSendsThePathTheServerReadsBack() = runBlocking {
        val path = "Scans/Letters & bills/Scan +1 2026-10-21.pdf"
        answers["GET /api/scan/reads"] = MockResponse().setBody("""{"read":$readJson,"can_read":true}""")
        val r = io.scanReadFor(path)
        assertTrue(r.canRead)
        assertEquals("queued", r.read?.state)
        val req = seen.single()
        // The server decodes one query value: the slashes, spaces, & and + survive.
        assertEquals(path, req.requestUrl?.queryParameter("path"))
        assertEquals("household-token-1234", req.getHeader(DEVICE_TOKEN_HEADER))
    }

    @Test fun startingAReadPostsThePath() = runBlocking {
        val path = "Scans/Scan 2026-10-21 15.40.pdf"
        answers["POST /api/scan/reads"] = MockResponse().setResponseCode(201).setBody(readJson)
        val r = io.startScanRead(path)
        assertEquals("0123456789abcdef0123456789abcdef", r.id)
        assertTrue(r.reading)
        val req = seen.single()
        assertEquals("POST", req.method)
        assertEquals("/api/scan/reads", req.path)
        val body = DomovoiJson.parseToJsonElement(req.body.readUtf8()).jsonObject
        assertEquals(path, body["path"]?.jsonPrimitive?.content)
        assertEquals(setOf("path"), body.keys)
    }

    @Test fun startingAReadThatTheServerRefusesShowsItsWords() = runBlocking {
        answers["POST /api/scan/reads"] = MockResponse().setResponseCode(409)
            .setBody("""{"detail":"This server can't read scans yet."}""")
        try {
            io.startScanRead("Scans/Scan.pdf")
            fail("expected a refusal")
        } catch (e: ApiException) {
            assertEquals("This server can't read scans yet.", serverDetail(e))
        }
    }

    @Test fun progressSummaryAndTextUseTheReadId() = runBlocking {
        val id = "0123456789abcdef0123456789abcdef"
        answers["GET /api/scan/reads/$id"] = MockResponse().setBody(readJson.replace("\"queued\"", "\"running\""))
        answers["GET /api/scan/reads/$id/summary"] = MockResponse().setBody(
            """{"read":null,"ai":true,"content":"From the bank.","said":"It's from the bank.",
               "notes":[{"code":"letter_checked","text":"Dates and amounts are quoted from the letter."}]}""",
        )
        answers["GET /api/scan/reads/$id/text"] = MockResponse().setBody(
            """{"read":null,"pages":[{"page":1,"method":"ocr","confidence":91.5,"text":"Dear Alex"}],"text":"Dear Alex"}""",
        )
        assertEquals("running", io.scanRead(id).state)
        val s = io.scanReadSummary(id)
        assertEquals("From the bank.", s.content)
        assertEquals("letter_checked", s.notes.single().code)
        val t = io.scanReadText(id)
        assertEquals("Dear Alex", t.text)
        assertEquals("ocr", t.pages.single().method)
        assertEquals(
            listOf("/api/scan/reads/$id", "/api/scan/reads/$id/summary", "/api/scan/reads/$id/text"),
            seen.map { it.path },
        )
    }

    @Test fun aSummaryAskedForTooSoonCarriesTheServersDetail() = runBlocking {
        val id = "0123456789abcdef0123456789abcdef"
        answers["GET /api/scan/reads/$id/summary"] = MockResponse().setResponseCode(409)
            .setBody("""{"detail":"Domovoi is still reading this scan."}""")
        val err = runCatching { io.scanReadSummary(id) }.exceptionOrNull()
        assertTrue(err is ApiException)
        assertEquals(
            "Domovoi is still reading this scan.",
            ScanReadView(read("done")).withError(err!!, SCAN_READ_SUMMARY_FAILED).error,
        )
    }
}
