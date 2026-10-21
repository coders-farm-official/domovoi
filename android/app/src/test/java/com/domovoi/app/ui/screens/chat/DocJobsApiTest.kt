package com.domovoi.app.ui.screens.chat

import okio.Buffer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The app's reading of long-document jobs (web/backend/api/chat_doc_jobs.py,
 *  documents plan D2): the SSE frames, the job's views and result, the
 *  stopped reply's job, and the words on the buttons. */
class DocJobsApiTest {
    private val stream = """
        event: job
        data: {"id":"${"a".repeat(32)}","state":"running","waiting_for_voice":false,"part":1,"parts":3,"read":0,"eta_s":null,"estimate_s":null,"line":"reading part 1 of 3","private":true,"thread_id":null,"covers":"nothing yet"}

        event: progress
        data: {"id":"${"a".repeat(32)}","state":"running","waiting_for_voice":true,"part":2,"parts":3,"read":1,"eta_s":40.5,"line":"waiting for a voice question to finish · part 2 of 3 next"}

        event: done
        data: {"id":"${"a".repeat(32)}","state":"done","part":3,"parts":3,"read":3,"line":"","result":{"content":"- Rent [p. 1]","doc":{"mode":"summary","found":true,"sources":[{"token":"","name":"lease.pdf","label":"p. 1","page":1,"text":"Rent is due.","match":"Rent is due.","cited":true}],"notes":[{"code":"read_in_parts","text":"Read in 3 parts. Points that span parts may be missed."}],"job":{"id":"${"a".repeat(32)}","state":"done","parts":3,"read":3}}}}

    """.trimIndent() + "\n"

    @Test fun aJobStreamReadsAsItsViews() {
        val seen = ArrayList<Pair<String, ServerJobView>>()
        readSseFrames(Buffer().writeUtf8(stream)) { ev, data -> jobEventView(ev, data)?.let { seen += ev to it } }
        assertEquals(listOf("job", "progress", "done"), seen.map { it.first })
        val (_, first) = seen[0]
        assertTrue(first.live && first.private && first.read == 0 && first.eta_s == null)
        assertTrue(seen[1].second.waiting_for_voice)
        assertEquals(40.5, seen[1].second.eta_s!!, 1e-9)
        val done = seen[2].second
        assertFalse(done.live)
        assertEquals("- Rent [p. 1]", done.result!!.content)
        assertEquals(3, done.result.doc!!.job!!.read)
        val phone = phoneSourcesOf(done.result.doc)
        assertEquals("p. 1", phone.single().label)
        assertEquals(1, phone.single().page)
        assertTrue(phone.single().cited)
    }

    @Test fun otherEventsAreNotViews() {
        assertNull(jobEventView("delta", """{"text":"x"}"""))
        assertNull(jobEventView("job", "not json"))
    }

    @Test fun theButtonsSayWhatTheyDo() {
        assertEquals("carry on (2 parts left)", carryOnText(2))
        assertEquals("carry on (1 part left)", carryOnText(1))
    }
}
