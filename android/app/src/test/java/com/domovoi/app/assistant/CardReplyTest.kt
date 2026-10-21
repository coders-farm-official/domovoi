package com.domovoi.app.assistant

import com.domovoi.app.assistant.lookup.refusedHost
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * W1 review round 2: how a reply answers a pending lookup card (the
 * reviewer's yesno.txt, every line), and the phone's refusal of addresses
 * on this network however they are written.
 */
class CardReplyTest {
    @Test fun theReviewersCasesReadAsMeant() {
        val cases = listOf(
            "okay, never mind" to CardReply.No,
            "ok stop" to CardReply.No,
            "sure, cancel that" to CardReply.No,
            "okay don't" to CardReply.No,
            "yes play some jazz" to CardReply.Yes("play some jazz"),
            "okay what time is it" to CardReply.Unclear,
            "no wait yes" to CardReply.No,
            "nope" to CardReply.No,
            "yes" to CardReply.Yes(),
        )
        cases.forEach { (said, want) -> assertEquals(said, want, cardReply(said)) }
    }

    @Test fun clearYesesAndTheRest() {
        listOf("Yes.", "yes please", "OK", "sure, go ahead", "go ahead!", "look it up", "yeah do it")
            .forEach { assertEquals(it, CardReply.Yes(), cardReply(it)) }
        listOf("not now", "nah", "don't", "never mind", "Cancel.", "leave it").forEach { assertEquals(it, CardReply.No, cardReply(it)) }
        listOf("hmm", "maybe", "what", "okay so", "sure why").forEach { assertEquals(it, CardReply.Unclear, cardReply(it)) }
        assertEquals(null, cardReply("tell me about red pandas and where they live in the wild"))
        // Only a clear yes is a yes for the old helper.
        assertEquals(true, yesOrNo("yes"))
        assertEquals(null, yesOrNo("yes play some jazz"))
        assertEquals(false, yesOrNo("okay, never mind"))
    }

    @Test fun addressesOnThisNetworkAreRefusedHoweverTheyAreWritten() {
        val refused = listOf(
            "127.0.0.1", "10.0.0.5", "192.168.1.20", "172.16.0.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
            "[::1]", "::1", "fd00::1", "fe80::1", "::ffff:127.0.0.1", "[::ffff:192.168.1.1]",
            "2130706433", "0177.0.0.1", "0x7f.1", "0x7f000001", "127.1", "localhost", "router.localhost",
        )
        refused.forEach { assertTrue(it, refusedHost(it)) }
        listOf("93.184.216.34", "en.wikipedia.org", "1a.de", "cafe.be", "2606:4700:4700::1111")
            .forEach { assertFalse(it, refusedHost(it)) }
    }
}
