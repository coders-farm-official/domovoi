package com.domovoi.app.ui.screens.manual

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.FEATURE_AI
import com.domovoi.app.ui.screens.settings.SettingsSection
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.shell.onDeviceAssistant
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * The manual the app renders is the server's web/static/manual-content.json:
 * these read the repo's own copy, so a change to the file's shape that the
 * app can't follow fails here, not on a phone.
 */
class ManualContentTest {
    @get:Rule val tmp = TemporaryFolder()

    private val text: String = listOf("../../web/static/manual-content.json", "../web/static/manual-content.json")
        .map(::File).first { it.isFile }.readText()
    private val doc: ManualDoc = ManualDoc.parse(text)!!

    @Test fun theReposContentFileParses() {
        assertTrue(doc.schema.startsWith(MANUAL_SCHEMA_PREFIX))
        assertEquals("Start here", doc.start.title)
        assertTrue(doc.topics.size >= 7 && doc.pieces.size > 40)
        val ids = doc.pieces.map { it.id }.toSet()
        doc.topics.forEach { t -> t.pieces.forEach { assertTrue("${t.id} → $it", it in ids) } }
        assertNotNull(doc.topic(doc.centre.opens))
        assertEquals(doc.topics.size - 1, doc.ringTopics.size)
        assertEquals("search the manual", doc.label("search_placeholder"))
        assertEquals("admin", doc.label("admin", "text"))
        assertEquals("fallback", doc.label("nope", fallback = "fallback"))
    }

    @Test fun somethingElseIsNotAManual() {
        assertNull(ManualDoc.parse("""{"detail":"Not Found"}"""))
        assertNull(ManualDoc.parse("<html>"))
    }

    @Test fun everyRouteOpensAScreenTheAppHasOrTheDashboard() {
        val screens = Route.entries.map { it.name }.toSet()
        val tabs = SettingsSection.entries.map { it.key }.toSet()
        doc.routes.forEach { (id, r) ->
            if (r.app.dashboard) {
                assertNotNull("$id opens the dashboard at a page it has", r.dashboard)
            } else {
                assertTrue("$id: ${r.app.screen}", r.app.screen in screens)
                r.app.tab?.let { assertTrue("$id: tab $it", r.app.screen == "Settings" && it in tabs) }
            }
        }
    }

    /** The manual's link to Chat reads as plain text while the server's AI
     *  features are off (no link that bounces back to Home); on, or on an
     *  older server that has no switch, it is a link. */
    @Test fun aLinkToChatIsPlainTextWithTheServersAiOff() {
        val chat = doc.routes["chat"]!!
        val aiOff = Capabilities(features = mapOf(FEATURE_AI to false))
        // No on-device assistant here (after One Chat, Chat stays for it).
        onDeviceAssistant = { false }
        assertFalse(manualRouteOpens(chat, aiOff))
        assertTrue(manualRouteOpens(chat, Capabilities.EMPTY))
        assertTrue(manualRouteOpens(chat, Capabilities(features = mapOf(FEATURE_AI to true))))
        assertTrue(manualRouteOpens(doc.routes["music"]!!, aiOff))
        // The dashboard opens whatever the app shows; no screen and no
        // dashboard opens nothing.
        assertTrue(manualRouteOpens(ManualRoute(dashboard = "#x", app = ManualAppRoute(dashboard = true)), aiOff))
        assertFalse(manualRouteOpens(ManualRoute(), Capabilities.EMPTY))
    }

    /** What the language models answer ("are you sure?", "why is the sky
     *  blue", the weather and its "yes") is left out of the try-saying lists
     *  while the server's AI features are off; on, or an older server with
     *  no switch, every phrase shows. */
    @Test fun aiPhrasesAreLeftOutWithTheServersAiOff() {
        val say = (listOf(doc.start.blocks) + doc.pieces.map { it.blocks }).flatten().filter { it.type == "say" }
        val all = say.flatMap { it.phrases }
        val needsAi = all.filter { it.needs == "ai" }.map { it.text }.toSet()
        assertTrue("are you sure?" in needsAi && "why is the sky blue" in needsAi)
        assertFalse("set a timer for 10 minutes" in needsAi)
        val off = say.flatMap { shownPhrases(it.phrases, mapOf(FEATURE_AI to false)) }.map { it.text }
        assertFalse("are you sure?" in off || "why is the sky blue" in off)
        assertTrue("set a timer for 10 minutes" in off)
        assertEquals(all.size, say.flatMap { shownPhrases(it.phrases, emptyMap()) }.size)
        assertEquals(all.size, say.flatMap { shownPhrases(it.phrases, mapOf(FEATURE_AI to true)) }.size)
        assertEquals("ai", doc.handlerNeedsFeature["double_check"])
    }

    @Test fun inlineMarkupBecomesSpans() {
        val spans = parseInline("**Say** {wake}, see [Settings → Voices](route:settings/voices) {admin} *now* `:6369`.")
        assertEquals(
            listOf(
                Span.Bold("Say"), Span.Plain(" "), Span.Wake, Span.Plain(", see "),
                Span.Link("Settings → Voices", "route", "settings/voices"), Span.Plain(" "), Span.Tag("admin"),
                Span.Plain(" "), Span.Italic("now"), Span.Plain(" "), Span.Code(":6369"), Span.Plain("."),
            ),
            spans,
        )
        // Every text in the file reads without stray markup.
        val all = (doc.pieces.flatMap { it.blocks } + doc.start.blocks).flatMap { b ->
            listOf(b.text) + b.items.map { it.text } + b.rows.flatten()
        }
        all.filter { it.isNotEmpty() }.forEach { t ->
            val plain = parseInline(t).filterIsInstance<Span.Plain>().joinToString("") { it.text }
            assertFalse(t, plain.contains("**") || plain.contains("](") || plain.contains("{admin}"))
        }
    }

    @Test fun theWakeWordIsHeyJarvisUntilTheRoomsSayOtherwise() {
        val ww = doc.wakeWord
        assertEquals(WakeInfo("Hey Jarvis", emptyList()), wakeInfo(ww, null))
        assertEquals(WakeInfo("Hey Jarvis", emptyList()), wakeInfo(ww, emptyList()))
        val mixed = listOf(
            ManualRoom("kitchen", "Kitchen", "online", "hey_jarvis"),
            ManualRoom("office", null, "online", "hey_domovoi", "hey domovoi"),
            ManualRoom("garage", null, "offline", null),
        )
        val info = wakeInfo(ww, mixed)
        assertEquals("Hey Jarvis", info.lead)
        assertEquals(listOf("Hey Domovoi" to listOf("office")), info.others)
        assertEquals("Some rooms answer to another word: “Hey Domovoi” in office.", wakeNote(ww, info))
        // No room answers to the default: name the word they do answer to.
        val trained = listOf(ManualRoom("a", null, "online", "hello_domo"), ManualRoom("b", null, "online", "hello_domo"))
        assertEquals(WakeInfo("Hello Domo", emptyList()), wakeInfo(ww, trained))
        assertNull(wakeNote(ww, wakeInfo(ww, trained)))
    }

    @Test fun moreDetailOpensTheDocsOfTheServersVersion() {
        assertEquals("v1.0.0", docRef("1.0.0"))
        assertEquals("v1.0.0-rc2", docRef("1.0.0-rc2"))
        assertEquals("main", docRef("1.0.0 + 14 (abc1234)"))
        assertEquals("main", docRef("1.0.0rc2 (abc1234)"))
        assertEquals("main", docRef(null))
        assertEquals(
            "https://github.com/coders-farm-official/domovoi/blob/v1.0.0/docs/FAQ.md#where-is-my-data",
            docUrl(doc, "1.0.0", "doc:docs/FAQ.md#where-is-my-data"),
        )
        assertEquals("http://192.168.1.20:6369/#settings/voices", dashboardUrl("http://192.168.1.20:6369/", "#settings/voices"))
        assertNull(dashboardUrl("", "#manual"))
    }

    @Test fun theAppsChecklistIsTheRoomLinesAndPairingThisPhone() {
        val none = appChecklist(doc, ManualSignals())
        assertEquals(5, none.size)
        assertTrue(none.last().first.contains("Pair this phone"))
        assertEquals(true, none.last().second)
        assertTrue(none.dropLast(1).all { it.second == null })
        val some = appChecklist(doc, ManualSignals(rooms = listOf(ManualRoom("kitchen")), roomHeard = false, people = 0, tracks = 12))
        assertEquals(listOf(true, false, false, true), some.take(4).map { it.second })
    }

    @Test fun chatModeIsListedOnlyWhileItIsOn() {
        val names = listOf("timer", "chat_mode", "music")
        assertEquals(listOf("timer", "music"), listedHandlers(doc, names, emptyMap()))
        assertEquals(names, listedHandlers(doc, names, mapOf("chat" to true)))
    }

    @Test fun searchFindsAPieceByItsWords() {
        val visible = doc.pieces.map { it.id }.toSet()
        assertEquals("wake-word", searchManual(doc, visible, "wake")[0].id)
        assertEquals("timers", searchManual(doc, visible, "remind me")[0].id)
        assertTrue(searchManual(doc, visible, "zzzz").isEmpty())
        assertTrue(searchManual(doc, visible - "radio", "radio").none { it.id == "radio" })
    }

    @Test fun homesHintIsStartHeresFirstPhraseAndTheManualListsPhrasesPerHandler() {
        assertEquals("set a timer for 10 minutes", startPhrase(doc))
        val by = phrasesByHandler(doc)
        assertTrue("set a timer for 10 minutes" in by["timer"].orEmpty())
        assertTrue(by.values.flatten().none { "…" in it })
        // Pending until wf/v1-voice-fixes; batch 14 merged it and dropped the mark.
        assertTrue("remind me at 6 pm to take the bins out" in by["reminder"].orEmpty())
        assertTrue("what can you do" in by["help"].orEmpty())
    }

    @Test fun theRepositoryKeepsACopyPerServer() = runBlocking {
        val server = MockWebServer().also { it.start() }
        try {
            val api = ApiClient(baseUrlProvider = { server.url("/").toString().trimEnd('/') })
            val repo = ManualRepository(api, tmp.newFolder("manual"))
            server.enqueue(MockResponse().setBody(text))
            val first = repo.load()
            assertTrue(first is ManualRepository.Result.Ready && !first.cached)
            assertEquals(MANUAL_PATH, server.takeRequest().path)
            // The server can't be read: the copy from before.
            server.enqueue(MockResponse().setResponseCode(502))
            val again = ManualRepository(api, File(tmp.root, "manual")).load()
            assertTrue(again is ManualRepository.Result.Ready && again.cached)
            // A server older than the manual.
            server.enqueue(MockResponse().setResponseCode(404))
            assertEquals(ManualRepository.Result.NoManual, repo.load())
        } finally {
            server.shutdown()
        }
    }
}
