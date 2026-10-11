package com.domovoi.app.alerts

import com.domovoi.app.net.DomovoiJson
import kotlinx.serialization.json.JsonElement
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * The container guard's pure half: what the phone makes of the household
 * list and the open summary (whatever the server leaves out or adds), the
 * shell's banner (a shared screen's included), the details screen's words,
 * what a notification says, and which incidents a phone notifies about.
 */
class ContainerIncidentsTest {
    private fun bookIncident(id: Long, open: Boolean = true) = ContainerIncident(
        id = id, openedAt = "2026-10-11T14:00:0${id % 10}+00:00", open = open,
    )

    @Test fun planWithdrawalsTakesBackWhatIsNoLongerOpenOnce() {
        val book = listOf(bookIncident(1), bookIncident(2), bookIncident(3)).map { incidentMark(it) }
        val plan = planWithdrawals(listOf(bookIncident(1), bookIncident(2, open = false)), book, emptyList())
        assertEquals(listOf(2L, 3L), plan.withdraw)
        assertEquals(book.drop(1), plan.withdrawn)
        // Withdrawn already: not again, even when a stale list shows it open.
        val again = planWithdrawals(listOf(bookIncident(1), bookIncident(2, open = false)), book, plan.withdrawn)
        assertEquals(emptyList<Long>(), again.withdraw)
        assertEquals(plan.withdrawn, again.withdrawn)
        // A mark that left the book is forgotten as withdrawn too.
        assertEquals(listOf(book[2]), planWithdrawals(emptyList(), book.drop(2), plan.withdrawn).withdrawn)
        assertEquals(7L, markId("7|123"))
        assertEquals(null, markId("garbage"))
    }


    private fun json(text: String): JsonElement = DomovoiJson.parseToJsonElement(text)

    private val full = """
        {"incidents":[
          {"id":12,"class":"tamper","kind":"image","severity":"error","label":"the web search helper",
           "summary":"The web search helper is running a different image than the one Domovoi expects.",
           "owner":"core","container":"domovoi-searxng","opened_at":"2026-10-11T14:02:03.123456+00:00",
           "last_seen_at":"2026-10-11T14:05:03+00:00","resolved_at":null,"resolution":null,
           "acknowledged":false,"acknowledged_at":null,"cleared_at":null,"open":true,"freezes":true,
           "can_acknowledge":false,"can_clear":true},
          {"id":9,"class":"failure","kind":"stopped","severity":"warn","label":"the speech engine",
           "summary":"The speech engine stopped.","owner":"core","container":"domovoi-tts",
           "opened_at":"2026-10-11T09:00:00+00:00","last_seen_at":"2026-10-11T09:10:00+00:00",
           "resolved_at":"2026-10-11T09:12:00+00:00","resolution":"recovered","acknowledged":false,
           "acknowledged_at":null,"cleared_at":null,"open":false,"freezes":false,
           "can_acknowledge":false,"can_clear":false}],
         "banner":{"show":true,"tone":"tamper","text":"The web search helper is running a different image than the one Domovoi expects. Plugin installs, updates and satellite upgrades are paused until an admin reviews it.","count":1,"incident_ids":[12],"frozen":true},
         "frozen":true,"attention":true}
    """.trimIndent()

    private fun inc(
        id: Long,
        cls: String = CLASS_FAILURE,
        open: Boolean = true,
        acknowledged: Boolean = false,
        openedAt: String? = "2026-10-11T14:00:00Z",
        severity: String = "error",
    ) = ContainerIncident(
        id = id, cls = cls, kind = if (cls == CLASS_TAMPER) "image" else "stopped", severity = severity,
        summary = "incident $id", container = "domovoi-$id", openedAt = openedAt,
        resolvedAt = if (open) null else "2026-10-11T15:00:00Z", acknowledged = acknowledged, open = open,
    )

    // ---- parsing ----------------------------------------------------------------

    @Test fun theFullPayloadParses() {
        val p = parseIncidents(json(full))!!
        assertEquals(listOf(12L, 9L), p.incidents.map { it.id })
        val t = p.incidents[0]
        assertTrue(t.tamper)
        assertEquals("image", t.kind)
        assertEquals("error", t.severity)
        assertEquals("domovoi-searxng", t.container)
        assertEquals("the web search helper", t.label)
        assertTrue(t.open)
        assertTrue(t.freezes)
        assertFalse(t.acknowledged)
        assertNull(t.resolvedAt)
        val f = p.incidents[1]
        assertFalse(f.tamper)
        assertFalse(f.open)
        assertEquals("recovered", f.resolution)
        assertEquals("2026-10-11T09:12:00+00:00", f.resolvedAt)
        assertTrue(p.banner.show)
        assertEquals("tamper", p.banner.tone)
        assertEquals(listOf(12L), p.banner.incidentIds)
        assertEquals(1, p.banner.count)
        assertTrue(p.banner.frozen)
        assertTrue(p.frozen)
        assertTrue(p.attention)
    }

    @Test fun nullsAndMissingFieldsAreDerivedNotFatal() {
        val p = parseIncidents(
            json(
                """{"incidents":[
                     {"id":3,"class":null,"kind":null,"summary":null,"container":null,"opened_at":null,
                      "resolved_at":null,"acknowledged_at":"2026-10-11T10:00:00Z"},
                     {"id":"4","class":"tamper","resolved_at":"2026-10-11T11:00:00Z","resolution":"cleared"}],
                    "banner":null}""",
            ),
        )!!
        val a = p.incidents[0]
        assertEquals(CLASS_FAILURE, a.cls)
        assertTrue("no `open`: open while unresolved", a.open)
        assertTrue("no `acknowledged`: from acknowledged_at", a.acknowledged)
        assertNull(a.summary)
        assertNull(a.container)
        val b = p.incidents[1]
        assertEquals("a string id still reads", 4L, b.id)
        assertTrue(b.tamper)
        assertFalse(b.open)
        assertFalse("a resolved tamper incident holds nothing back", b.freezes)
        // No banner from the server: derived by its own rule. The only open
        // one is an acknowledged failure, so nothing shows.
        assertFalse(p.banner.show)
        assertFalse(p.frozen)
        assertFalse(p.attention)
    }

    @Test fun aMissingBannerIsDerivedFromWhatIsOpen() {
        val p = parseIncidents(
            json(
                """{"incidents":[
                     {"id":7,"class":"tamper","summary":"Unknown container.","open":true},
                     {"id":6,"class":"failure","severity":"warn","summary":"Stopped.","open":true}]}""",
            ),
        )!!
        assertTrue(p.banner.show)
        assertEquals("tamper", p.banner.tone)
        assertEquals(listOf(7L, 6L), p.banner.incidentIds)
        assertTrue(p.banner.text.startsWith("2 problems with Domovoi's containers need attention."))
        assertTrue(p.banner.text.endsWith("paused until an admin reviews it."))
        assertTrue(p.frozen)
        assertTrue(p.attention)
    }

    @Test fun unknownFieldsAreIgnored() {
        val p = parseIncidents(
            json(
                """{"incidents":[{"id":1,"class":"failure","open":true,"expected":{"image":"x"},
                     "found":{"a":[1,2]},"brand_new":true}],
                    "banner":{"show":true,"tone":"warn","text":"t","later":{"x":1}},
                    "frozen":false,"attention":true,"extra":[1,2,3]}""",
            ),
        )!!
        assertEquals(1, p.incidents.size)
        assertEquals("warn", p.banner.tone)
        assertEquals("t", p.banner.text)
    }

    @Test fun malformedPayloadsAreNotPayloads() {
        assertNull(parseIncidents(null))
        assertNull(parseIncidents(json("[]")))
        assertNull(parseIncidents(json("\"nope\"")))
        assertNull("no incidents array (the web's test too)", parseIncidents(json("""{"banner":{"show":true}}""")))
        assertNull(parseIncidents(json("""{"incidents":{"id":1}}""")))
        // Incidents without a usable id are skipped, the rest kept.
        val p = parseIncidents(json("""{"incidents":[{"class":"tamper"},{"id":"x"},7,null,{"id":2}]}"""))!!
        assertEquals(listOf(2L), p.incidents.map { it.id })
        // A banner of the wrong shape is derived instead (the server's rule:
        // red only for severity "error").
        val q = parseIncidents(json("""{"incidents":[{"id":2,"open":true,"severity":"error"}],"banner":"red"}"""))!!
        assertTrue(q.banner.show)
        assertEquals("error", q.banner.tone)
        val r = parseIncidents(json("""{"incidents":[{"id":2,"open":true}],"banner":"red"}"""))!!
        assertEquals("warn", r.banner.tone)
    }

    @Test fun theSummaryParses() {
        val s = parseSummary(json("""{"attention":true,"tone":"err","text":"Something needs an admin's attention."}"""))!!
        assertTrue(s.attention)
        assertEquals("err", s.tone)
        assertEquals(ADMIN_ATTENTION_TEXT, s.text)
        val quiet = parseSummary(json("""{"attention":false,"tone":null,"text":""}"""))!!
        assertFalse(quiet.attention)
        assertNull(quiet.tone)
        assertEquals("", quiet.text)
        assertEquals("attention with no words says the guest line", ADMIN_ATTENTION_TEXT,
            parseSummary(json("""{"attention":true}"""))!!.text)
        assertNull(parseSummary(json("[]")))
        assertNull(parseSummary(null))
    }

    // ---- the shell's banner -----------------------------------------------------

    private fun household(json: String) =
        IncidentState("k", IncidentTier.HOUSEHOLD, loaded = true, payload = parseIncidents(json(json)))

    @Test fun theHouseholdBannerIsTheServersAsGiven() {
        val b = shellBanner(household(full), shared = false)
        assertTrue(b.show)
        assertEquals(BannerTone.TAMPER, b.tone)
        assertTrue(b.tone!!.red)
        assertTrue(b.text.startsWith("The web search helper is running a different image"))
        assertTrue(b.frozen)
    }

    @Test fun aSharedScreenSaysOnlyThatAnAdminIsNeeded() {
        val b = shellBanner(household(full), shared = true)
        assertTrue(b.show)
        assertEquals(BannerTone.TAMPER, b.tone)
        assertEquals(ADMIN_ATTENTION_TEXT, b.text)
    }

    @Test fun bannerTonesMapRedAndAmber() {
        fun tone(t: String?) = shellBanner(
            household("""{"incidents":[],"banner":{"show":true,"tone":${t?.let { "\"$it\"" } ?: "null"},"text":"x"}}"""),
            shared = false,
        ).tone
        assertEquals(BannerTone.ERROR, tone("error"))
        assertEquals(BannerTone.WARN, tone("warn"))
        assertFalse(BannerTone.WARN.red)
        assertTrue(BannerTone.ERROR.red)
        assertEquals("an unknown tone is red, not hidden", BannerTone.ERROR, tone("purple"))
        assertEquals(BannerTone.ERROR, tone(null))
    }

    @Test fun nothingShowsWhenTheServerSaysSo() {
        assertFalse(shellBanner(household("""{"incidents":[],"banner":{"show":false}}"""), false).show)
        assertFalse(shellBanner(IncidentState(), shared = false).show)
        assertFalse(shellBanner(IncidentState("k", IncidentTier.NONE, loaded = true), shared = true).show)
    }

    @Test fun theOpenTierShowsTheSummaryOnly() {
        fun open(text: String) =
            IncidentState("k", IncidentTier.OPEN, loaded = true, summary = parseSummary(json(text)))
        val red = shellBanner(open("""{"attention":true,"tone":"err","text":"Something needs an admin's attention."}"""), false)
        assertTrue(red.show)
        assertEquals(BannerTone.ERROR, red.tone)
        assertEquals(ADMIN_ATTENTION_TEXT, red.text)
        assertFalse(red.frozen)
        val amber = shellBanner(open("""{"attention":true,"tone":"warn","text":"x"}"""), shared = true)
        assertEquals(BannerTone.WARN, amber.tone)
        assertEquals(ADMIN_ATTENTION_TEXT, amber.text)
        assertFalse(shellBanner(open("""{"attention":false}"""), false).show)
    }

    // ---- the details screen's words ---------------------------------------------

    @Test fun theMetaLineSaysWhatWhereAndWhen() {
        val rel: (String?) -> String = { if (it == null) "-" else "2m ago" }
        val t = parseIncidents(json(full))!!.incidents[0]
        assertEquals("#12 · tampering · different image · domovoi-searxng · opened 2m ago", incidentMeta(t, rel))
        val acked = inc(5, acknowledged = true)
        assertEquals("#5 · failure · stopped · domovoi-5 · opened 2m ago · acknowledged", incidentMeta(acked, rel))
        val done = inc(9, open = false).copy(resolution = "recovered")
        assertEquals("#9 · failure · stopped · domovoi-9 · opened 2m ago · resolved 2m ago", incidentMeta(done, rel))
        val cleared = inc(4, cls = CLASS_TAMPER, open = false).copy(resolution = "cleared", container = null)
        assertEquals("#4 · tampering · different image · opened 2m ago · cleared 2m ago", incidentMeta(cleared, rel))
    }

    @Test fun everyKindHasWords() {
        assertEquals("failed", kindWords("oneshot_failed"))
        assertEquals("docker down", kindWords("daemon"))
        assertEquals("different image", kindWords("image"))
        assertEquals("unknown container", kindWords("unknown"))
        assertEquals("risky settings", kindWords("config"))
        assertEquals("brand new", kindWords("brand_new"))
        assertEquals("problem", kindWords(null))
    }

    // ---- what a notification says -----------------------------------------------

    @Test fun aNotificationSaysTheClassAndTheSummary() {
        val t = parseIncidents(json(full))!!.incidents[0]
        val c = incidentContent(t, shared = false)
        assertEquals("Signs of tampering", c.title)
        assertEquals(t.summary, c.text)
        assertEquals(INCIDENT_PUBLIC_TITLE, c.publicTitle)
        assertEquals("Domovoi needs an admin's attention", c.publicTitle)
        assertEquals(Instant.parse("2026-10-11T14:02:03.123456Z").toEpochMilli(), c.whenMs)
        assertEquals(12L, c.incidentId)
        assertEquals("A container failed", incidentContent(inc(3), shared = false).title)
    }

    @Test fun aSharedScreensNotificationIsThePublicOne() {
        val c = incidentContent(parseIncidents(json(full))!!.incidents[0], shared = true)
        assertEquals(INCIDENT_PUBLIC_TITLE, c.title)
        assertNull(c.text)
    }

    @Test fun anIncidentWithoutASummaryStillSaysSomething() {
        val c = incidentContent(ContainerIncident(id = 1, label = "the database", kind = "missing"), shared = false)
        assertEquals("the database · missing", c.text)
        assertEquals("container_incident:abcd1234", incidentTag("abcd1234"))
    }

    // ---- which incidents post ---------------------------------------------------

    @Test fun firstContactPostsWhatTheBannerShows() {
        val list = listOf(
            inc(12, cls = CLASS_TAMPER),
            inc(11, acknowledged = true),
            inc(10),
            inc(9, open = false),
            inc(8, cls = CLASS_TAMPER, open = false),
        )
        val plan = planNotifications(list, notified = emptyList())
        assertEquals(listOf(10L, 12L), plan.post.map { it.id })
        assertEquals(plan.post.map { incidentMark(it) }, plan.book)
    }

    @Test fun anIncidentThatStaysOpenIsNotPostedAgain() {
        val first = planNotifications(listOf(inc(12, cls = CLASS_TAMPER)), emptyList())
        val again = planNotifications(listOf(inc(12, cls = CLASS_TAMPER)), first.book)
        assertEquals(emptyList<ContainerIncident>(), again.post)
        assertEquals(first.book, again.book)
    }

    @Test fun aNewIdPosts() {
        val first = planNotifications(listOf(inc(12, cls = CLASS_TAMPER)), emptyList())
        val next = planNotifications(listOf(inc(13), inc(12, cls = CLASS_TAMPER)), first.book)
        assertEquals(listOf(13L), next.post.map { it.id })
        assertEquals(2, next.book.size)
    }

    @Test fun aResolvedIncidentNeverPosts() {
        assertEquals(emptyList<ContainerIncident>(), planNotifications(listOf(inc(5, open = false)), emptyList()).post)
        assertEquals(
            emptyList<ContainerIncident>(),
            planNotifications(listOf(inc(6, cls = CLASS_TAMPER, open = false)), emptyList()).post,
        )
    }

    @Test fun anAcknowledgedFailurePostsOnlyIfItsAcknowledgementGoes() {
        val quiet = planNotifications(listOf(inc(5, acknowledged = true)), emptyList())
        assertEquals(emptyList<ContainerIncident>(), quiet.post)
        assertEquals("not remembered", emptyList<String>(), quiet.book)
        // The problem changed, the server dropped the acknowledgement: once.
        val loud = planNotifications(listOf(inc(5)), quiet.book)
        assertEquals(listOf(5L), loud.post.map { it.id })
        assertEquals(emptyList<ContainerIncident>(), planNotifications(listOf(inc(5)), loud.book).post)
    }

    @Test fun aRestartedIdIsANewIncident() {
        val old = planNotifications(listOf(inc(3, openedAt = "2026-10-01T10:00:00Z")), emptyList())
        // A rebuilt database on the same address numbers from 1 again.
        val rebuilt = planNotifications(listOf(inc(3, openedAt = "2026-10-11T10:00:00+00:00")), old.book)
        assertEquals(listOf(3L), rebuilt.post.map { it.id })
        // The same instant written another way is the same incident.
        val same = planNotifications(listOf(inc(3, openedAt = "2026-10-01T10:00:00.000+00:00")), old.book)
        assertEquals(emptyList<ContainerIncident>(), same.post)
    }

    @Test fun theBookKeepsTheNewestTwoHundred() {
        var book = emptyList<String>()
        for (i in 1..230) book = planNotifications(listOf(inc(i.toLong())), book).book
        assertEquals(NOTIFIED_MAX, book.size)
        assertEquals(incidentMark(inc(31)), book.first())
        assertEquals(incidentMark(inc(230)), book.last())
        // Fell out of the book long after it was posted: an incident open
        // that long would post again, the price of a bounded book.
        assertEquals(listOf(30L), planNotifications(listOf(inc(30)), book).post.map { it.id })
    }

    @Test fun theBookSurvivesItsEncodingAndCorruption() {
        val book = mapOf("aaaa1111" to listOf("1|5"), "bbbb2222" to listOf("1|5", "2|6"))
        assertEquals(book, decodeNotified(encodeNotified(book)))
        assertEquals(emptyMap<String, List<String>>(), decodeNotified("{not json"))
        assertEquals(emptyMap<String, List<String>>(), decodeNotified(null))
        assertEquals(emptyMap<String, List<String>>(), decodeNotified("[]"))
    }
}
