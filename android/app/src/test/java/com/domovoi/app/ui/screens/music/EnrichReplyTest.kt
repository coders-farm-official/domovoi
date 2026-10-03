package com.domovoi.app.ui.screens.music

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import org.junit.Assert.assertEquals
import org.junit.Test

/** The "enrich" toast follows the core's answer (fix B3). */
class EnrichReplyTest {

    private fun reply(json: String) = enrichReplyText(Json.parseToJsonElement(json))

    @Test fun aQueuedSweepSaysStarted() {
        assertEquals("enrich started", reply("""{"queued": true, "worker": "library_enricher"}"""))
    }

    @Test fun anOlderCoreOrAnOddBodyKeepsTheOldToast() {
        assertEquals("enrich started", reply("""{"ok": true}"""))
        assertEquals("enrich started", enrichReplyText(JsonNull))
        assertEquals("enrich started", enrichReplyText(null))
    }

    @Test fun aSweepThatDidNotStartSaysWhy() {
        assertEquals(
            "song recognition needs a free AcoustID key or the Shazam add-on",
            reply("""{"queued": false, "reason": "no_provider"}"""),
        )
        assertEquals("no internet right now — try again when it's back",
            reply("""{"queued": false, "reason": "offline"}"""))
        assertEquals("song recognition is turned off on the server",
            reply("""{"queued": false, "reason": "disabled"}"""))
        assertEquals("already identifying songs", reply("""{"queued": false, "reason": "running"}"""))
        assertEquals("enrich didn't start (later)", reply("""{"queued": false, "reason": "later"}"""))
        assertEquals("enrich didn't start (unknown reason)", reply("""{"queued": false}"""))
    }
}
