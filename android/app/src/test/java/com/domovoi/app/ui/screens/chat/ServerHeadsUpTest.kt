package com.domovoi.app.ui.screens.chat

import com.domovoi.app.assistant.docs.HeadsUpPrefs
import com.domovoi.app.net.decode
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Test

/** The app's reading of a home-server chat's heads-up card (chat.py,
 *  documents plan D3): the card row's `doc.heads_up`, the card as drawn,
 *  and this phone's answers as a send carries them. */
class ServerHeadsUpTest {
    private val row = """
        {"id": 7, "role": "assistant", "content": "Domovoi isn't reliable at arithmetic.", "model": null,
         "doc": {"mode": "heads_up", "found": true, "sources": [], "notes": [],
                 "heads_up": {"kind": "calc", "text": "Domovoi isn't reliable at arithmetic.",
                              "options": [{"id": "calculate", "label": "calculate exactly"},
                                          {"id": "try_anyway", "label": "try anyway"}],
                              "never_label": "don't warn me about this again", "note": "n", "op": "sum",
                              "chosen": null}}}
    """.trimIndent()

    @Test fun aCardRowReadsAsTheCardItDraws() {
        val m = Json.parseToJsonElement(row).decode<MessageRow>()
        val hu = m.doc!!.heads_up!!
        assertEquals("heads_up", m.doc!!.mode)
        val v = hu.view()
        assertEquals("calc", v.kind)
        assertEquals(listOf("calculate" to "calculate exactly", "try_anyway" to "try anyway"), v.options)
        assertEquals("don't warn me about this again", v.neverLabel)
        assertEquals(null, v.chosen)
    }

    @Test fun thePhonesAnswersGoWithEverySend() {
        assertEquals("""{"off":false,"skip":["blurry","calc"]}""",
            headsUpBody(HeadsUpPrefs(off = false, skip = setOf("calc", "blurry"))).toString())
        assertEquals("no more heads-ups about exact sums, averages and dates on this phone", silencedText("calc"))
    }
}
