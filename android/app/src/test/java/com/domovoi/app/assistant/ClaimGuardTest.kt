package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The phone model's claims (Fold check 2026-10-21: "Play Outcast" got
 * "Playing Outcast now." and nothing played): a sentence saying it did what
 * a phone chat's model can't do is replaced; an honest one is left alone.
 */
class ClaimGuardTest {
    private val claims: List<Pair<String, ClaimGuard.Claim>> = listOf(
        "Playing Outcast now." to ClaimGuard.Claim.PLAY,
        "Now playing Hey Ya by OutKast." to ClaimGuard.Claim.PLAY,
        "I've started playing your jazz playlist." to ClaimGuard.Claim.PLAY,
        "Sure, I'll play some relaxing music for you." to ClaimGuard.Claim.PLAY,
        "I paused the music." to ClaimGuard.Claim.PLAY,
        "Okay, pausing the song." to ClaimGuard.Claim.PLAY,
        "I skipped to the next track." to ClaimGuard.Claim.PLAY,
        "Your song is playing." to ClaimGuard.Claim.PLAY,
        "I've set a timer for 5 minutes." to ClaimGuard.Claim.CLOCK,
        "Done! I set an alarm for 7 AM." to ClaimGuard.Claim.CLOCK,
        "Setting a timer for ten minutes." to ClaimGuard.Claim.CLOCK,
        "Your alarm is set for 6:45 tomorrow." to ClaimGuard.Claim.CLOCK,
        "Timer set for 2 minutes." to ClaimGuard.Claim.CLOCK,
        "I'll remind you in five minutes to check Dome of War." to ClaimGuard.Claim.CLOCK,
        "Reminder set: check the oven." to ClaimGuard.Claim.CLOCK,
        "I've added a reminder for tomorrow morning." to ClaimGuard.Claim.CLOCK,
        "Calling Mom now." to ClaimGuard.Claim.CONTACT,
        "I've texted Sam that you're running late." to ClaimGuard.Claim.CONTACT,
        "Your message has been sent." to ClaimGuard.Claim.CONTACT,
        "I sent the email to your boss." to ClaimGuard.Claim.CONTACT,
        "I'm calling the pharmacy." to ClaimGuard.Claim.CONTACT,
        "I saved that to your notes." to ClaimGuard.Claim.OTHER,
        "I've printed the document." to ClaimGuard.Claim.OTHER,
        "Okay, I turned on the flashlight." to ClaimGuard.Claim.OTHER,
        "I'll save it for you." to ClaimGuard.Claim.OTHER,
        "**Playing Outcast now.**" to ClaimGuard.Claim.PLAY,
    )

    private val honest: List<String> = listOf(
        "I can't play music from this chat.",
        "OutKast is an American hip hop duo from Atlanta.",
        "Playing music can help you relax.",
        "You can set a timer by saying \"set a timer for 10 minutes\".",
        "To play a song, say \"play\" and its name.",
        "The band played their first show in 1992.",
        "He called her the next morning.",
        "Would you like me to set a timer?",
        "Do you want to hear a joke?",
        "I don't have a way to send messages.",
        "Setting a timer is a good way to remember the oven.",
        "Calling a plumber is usually the fastest fix.",
        "I'll play devil's advocate for a moment.",
        "I'll start with the basics.",
        "I'll explain how alarms work.",
        "I'll skip the details and give you the short version.",
        "I'll put on my thinking cap.",
        "I'll call it a day on this topic.",
        "Let's say you set an alarm for 7.",
        "Here is a recipe you could save for later.",
        "Saving money is easier with a budget.",
        "If you text her, keep it short.",
        "Hey Ya! was released in 2003.",
        "I'd love to play that for you, but I can't from this chat.",
        "Sure! Here's a joke: why did the scarecrow win an award?",
        "I paused to think about it, and the answer is 42.",
        "Your timer question is a good one.",
    )

    @Test fun claimsAreCaught() {
        val missed = claims.filter { (s, k) -> ClaimGuard.claimIn(s) != k }.map { (s, k) -> "\"$s\": ${ClaimGuard.claimIn(s)} (want $k)" }
        println("claims caught: ${claims.size - missed.size}/${claims.size}")
        assertTrue(missed.joinToString("\n"), missed.isEmpty())
        assertTrue(claims.size >= 20)
    }

    @Test fun honestRepliesAreLeftAlone() {
        val hit = honest.filter { ClaimGuard.claimIn(it) != null }.map { "\"$it\": ${ClaimGuard.claimIn(it)}" }
        println("honest replies left alone: ${honest.size - hit.size}/${honest.size}")
        assertTrue(hit.joinToString("\n"), hit.isEmpty())
        assertTrue(honest.size >= 20)
    }

    @Test fun theStreamIsCheckedSentenceBySentence() {
        val g = ClaimGuard()
        val out = StringBuilder()
        for (d in listOf("Playing Out", "cast now. ", "It's from their 2003 album", " Speakerboxxx.")) out.append(g.push(d))
        out.append(g.finish())
        assertEquals("${ClaimGuard.PLAY_LINE} It's from their 2003 album Speakerboxxx.", out.toString())
        assertTrue(g.replaced)
        // A second claim of the same kind adds nothing.
        val h = ClaimGuard()
        val twice = h.push("I've set a timer for 5 minutes. ") + h.push("Your timer is set. ") + h.push("Enjoy!") + h.finish()
        assertEquals("${ClaimGuard.CLOCK_LINE} Enjoy!", twice)
        // An honest reply comes back exactly as written.
        val k = ClaimGuard()
        val plain = "OutKast is a duo.\n\nThey are from **Atlanta**."
        assertEquals(plain, k.push(plain) + k.finish())
        assertFalse(k.replaced)
    }
}
