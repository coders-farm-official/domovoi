package com.domovoi.app.assistant

/**
 * Keeps a phone chat's model reply from claiming an action it can't do
 * (Fold check 2026-10-21: "Play Outcast" got "Playing Outcast now." from
 * the model, and nothing played). In a phone chat every action is the
 * app's own (the patterns and their cards, never the model: Gemma 4 E2B has
 * no action tools), so a model sentence saying it played, paused, set a
 * timer or alarm, sent, called, texted, saved or printed something is never
 * true. That sentence is replaced with an honest line, or a pointer to the
 * words that do it, before it is shown or spoken.
 *
 * Only on a turn that asked to act ([asksToAct]: play, set, remind, call,
 * send, text, print, save, an alarm or a timer); a story or an explanation
 * is never touched. Text inside quotes is never read as a claim, and
 * neither is a sentence about someone else, or about the past ("the
 * message was sent by telegraph").
 *
 * Streamed: [push] each delta and get back whole sentences, checked;
 * [finish] the rest. A reply with no claim reads exactly as it was written.
 */
class ClaimGuard {
    private val pending = StringBuilder()
    private val said = mutableSetOf<Claim>()

    /** Whether any sentence was replaced. */
    val replaced: Boolean get() = said.isNotEmpty()

    /** Text pushed and not yet given back (an unfinished sentence). */
    val holding: Boolean get() = pending.isNotEmpty()

    fun push(delta: String): String {
        pending.append(delta)
        val out = StringBuilder()
        while (true) {
            // The spacing after a sentence goes with it: the voice's own
            // splitter waits for it before it reads the sentence out.
            val end = SentenceSplitter.nextBoundary(pending)?.let { e ->
                var j = e
                while (j < pending.length && pending[j].isWhitespace()) j++
                j
            } ?: break
            out.append(checked(pending.substring(0, end)))
            pending.delete(0, end)
        }
        return out.toString()
    }

    fun finish(): String {
        val whole = pending.toString()
        pending.setLength(0)
        return if (whole.isEmpty()) "" else checked(whole)
    }

    /** [raw] (one sentence, with its spacing) or its honest line; a second
     *  claim of the same kind adds nothing. */
    private fun checked(raw: String): String {
        val claim = claimIn(raw) ?: return raw
        val lead = raw.takeWhile { it.isWhitespace() }
        val tail = raw.takeLastWhile { it.isWhitespace() }
        if (!said.add(claim)) return lead.takeIf { it.contains('\n') }.orEmpty()
        return lead + claim.line + tail
    }

    enum class Claim(val line: String) {
        PLAY(PLAY_LINE),
        CLOCK(CLOCK_LINE),
        CONTACT(CONTACT_LINE),
        OTHER(CANT_LINE),
    }

    companion object {
        const val CANT_LINE = "I can't do that from this chat."
        const val PLAY_LINE = "I can't play that from this chat. To play music saved on this phone, say \"play\" and the song, artist or album."
        const val CLOCK_LINE = "I can't set that from this chat. Say \"set a timer for 10 minutes\" or \"set an alarm for 7 am\" and I'll ask before sending it to the Clock app."
        const val CONTACT_LINE = "I can't do that from this chat. Say \"call\" or \"text\" and a name, and I'll open the app for you to finish it."

        /** A question that asks the phone to do something: only then can a
         *  reply claim to have done it (review round 1). */
        private val ACT = Regex(
            "\\b(?:play|put on|listen to|shuffle|pause|resume|skip|set|start|remind|reminder|alarm|alarms|timer|timers|wake me|" +
                "call|ring|dial|phone|send|text|message|email|e mail|print|save|turn on|turn off|order|book|share)\\b",
        )

        fun asksToAct(question: String?): Boolean = ACT.containsMatchIn(plain(question ?: ""))

        private const val I = "(?:i|i've|i have|i just|i've just|i have just|i'll|i will|i'm|i am|i'm now|i am now|i've now|" +
            "i'll now|i'll go ahead and|i went ahead and|i've gone ahead and|i have gone ahead and|let me|lemme|allow me to)"
        private const val LEAD = "(?:(?:ok|okay|sure|sure thing|done|great|alright|all right|there you go|here you go|got it|" +
            "you got it|no problem|perfect|absolutely|of course|right away)(?: )?)*"

        /** One kind of claim: its verbs, and what must follow them for the
         *  sentence to be a claim ("play devil's advocate" isn't music). */
        private class Rule(val claim: Claim, verbs: String, after: String) {
            val first = Regex("^$LEAD$I (?:$verbs)\\b(?<rest>$after)")
            val gerund = Regex("^$LEAD(?:now )?(?:$verbs)\\b(?<rest>$after)")
        }

        private const val NOT_MUSIC = "(?! (?:devil's advocate|a game|games|along|it safe|it cool|it by ear|the role|a role|a part|the part|a trick|tricks|a prank|with|nice|dead|catch|chess|cards|trivia|twenty questions|20 questions|hide and seek|pretend|the victim|the fool|hooky|tag|fair|by the rules|the long game|word games|a riddle|the piano|the guitar))"
        private const val MEDIA = "(?:the|your|it|that|this|music|some|playback|song|track|audio|player|album|playlist|radio)"
        private const val CLOCK = ".*\\b(?:timer|alarm|reminder|countdown)s?\\b.*"
        private const val THING = "(?:it|that|this|them|those|the|your|a|an)? ?(?:it|that|this|them|file|document|photo|picture|pdf|note|notes|page|app|settings|wifi|wi fi|bluetooth|flashlight|torch|light|lights|tv|order|ticket|tickets|table|flight|draft|recording|video)\\b.*"

        private val RULES = listOf(
            Rule(Claim.CLOCK, "set|setting|set up|setting up|started|starting|created|creating|added|adding|scheduled|scheduling|start|create|add|schedule", CLOCK),
            Rule(Claim.CLOCK, "remind|reminding", " you\\b.*"),
            Rule(Claim.PLAY, "played|play|playing|now playing", "$NOT_MUSIC.+"),
            Rule(Claim.PLAY, "paused|pause|pausing|resumed|resume|resuming", " $MEDIA\\b.*"),
            Rule(Claim.PLAY, "skipped|skip|skipping", " (?:to the next|to the previous|the song|the track|this song|that song|this track|ahead|forward|it|this one|that one)\\b.*"),
            Rule(Claim.PLAY, "stopped|stop|stopping", " (?:the music|the song|playback|the audio|the player|your music|the track)\\b.*"),
            Rule(Claim.PLAY, "queued|queue|queueing|queuing|shuffled|shuffle|shuffling", " .+"),
            Rule(Claim.PLAY, "put on|putting on", " (?:some |a bit of |a little |the |your )(?!thinking cap|brave face|coat|jacket|show|weight|kettle|shoes).+"),
            Rule(Claim.PLAY, "put on|putting on|started playing|start playing|starting playing|started|start|starting", " (?:some |your |the |a )?(?:music|songs?|tracks?|album|playlist|radio|tunes|jazz)\\b.*"),
            Rule(Claim.CONTACT, "called|call|calling|dialed|dialled|dial|dialing|dialling|rang|ringing", " (?!it\\b|this\\b|that\\b|a day\\b|out\\b|back to\\b|all\\b).+"),
            Rule(Claim.CONTACT, "texted|text|texting|messaged|message|messaging|emailed|email|emailing|sent|send|sending|forwarded|forward|forwarding", " .+"),
            Rule(Claim.CONTACT, "shared|share|sharing", " (?:it|that|this|the|your)\\b.*"),
            Rule(Claim.CONTACT, "let|told|tell|informed|inform|notified|notify", " (?:\\S+ ){1,3}(?:know|that)\\b.*"),
            Rule(Claim.OTHER, "saved|save|saving|printed|print|printing|downloaded|download|downloading|deleted|delete|deleting|booked|book|booking|ordered|order|ordering|bought|buy|buying|turned on|turn on|turning on|turned off|turn off|turning off|switched on|switched off|opened|open|opening|launched|launch|launching|recorded|record|recording", " $THING"),
        )

        private const val CLOCK_OBJ = "timer|alarm|reminder|countdown"
        private const val CONTACT_OBJ = "message|text|email|call"
        private const val MUSIC_OBJ = "song|music|playlist|album|track|tunes|radio"

        /** "Your timer is set.", "Your 10 minute timer is set.", "Alarm's set
         *  for 7.", "The message has been sent." Present or perfect only (a
         *  sentence about the past is a story), never "set by …" (how). */
        private val PASSIVE = Regex(
            "^$LEAD(?:your |the |a |an )?(?:[a-z0-9]+ ){0,2}?(?<obj>$CLOCK_OBJ|$CONTACT_OBJ|$MUSIC_OBJ|file|document|note|order|booking|print job|photo)" +
                "(?:s|'s)? (?:is |are |has been |have been |now |is now |'s )?(?:all |now )?" +
                "(?<v>set|sent|saved|scheduled|created|started|playing|paused|printed|placed|booked|queued|running|going|ticking|counting down|ready|on its way|on the way|in place|on)" +
                "\\b(?! by\\b)(?<rest>.*)$",
        )

        /** "OutKast is now playing.", "Hey Ya by OutKast is playing now." */
        private val NOW_PLAYING = Regex("^.{1,80} (?:is|are) (?:now playing|playing now|playing for you|now on)$")

        /** "Here's Hey Ya by OutKast.", "Enjoy Hey Ya by OutKast!" */
        private val HERE_BY = Regex("^$LEAD(?:here's|here is|here comes|enjoy|now enjoy|coming up) .{1,60} by [a-z0-9].{0,40}$")

        /** "Coming right up: Hey Ya!" */
        private val COMING_UP = Regex("^$LEAD(?:coming (?:right )?up|on its way|right away|starting now)\\b.*$")

        /** "Okay, 10 minutes on the clock!" */
        private val ON_THE_CLOCK = Regex("\\b(?:\\d+|a|an|one|two|three|four|five|ten|fifteen|twenty|thirty|forty five|sixty) (?:seconds?|minutes?|hours?) (?:on the clock|starting now|and counting)\\b")

        /** "Mom is on her way to the phone.", "Sam is on the line." */
        private val ON_THE_PHONE = Regex("\\b(?:on (?:her|his|their|its) way to the phone|is on the line|are on the line|is ringing now|ringing (?:her|him|them) now)\\b")

        /** Words that make a sentence a refusal, a suggestion, a question or
         *  a how-to, not a claim. */
        private val HONEST = Regex("\\b(?:can't|cannot|can not|couldn't|could not|won't|unable|not able|don't|do not|didn't|did not|isn't|aren't|wasn't|haven't|have not|hasn't|no way|if you|you can|you could|you might|you may|you'd|you'll need|try saying|just say|ask me|would you|do you want|shall i|should i|want me to|like me to|how to|if i could|i wish|i'd love to|i would|i could|pretend|imagine|let's say|in this story|in the story|once upon)\\b")

        /** "Playing music can help you relax.": a gerund opening a statement. */
        private val STATEMENT = Regex("\\b(?:is|are|was|were|can|could|will|would|helps?|makes?|means?|requires?|involves?|has|have|takes?|gives?|lets?|keeps?|feels?|might|may|should|seems?|tends?|isn't|is a|is an|was a)\\b")

        /** Quoted words are someone's words, not the reply's (review round 1). */
        private val QUOTED = Regex("\"[^\"]*(?:\"|$)|“[^”]*(?:”|$)")

        internal fun plain(s: String): String = SentenceSplitter.speakable(s).lowercase()
            .replace(Regex("[’‘`´]"), "'")
            .replace(Regex("[^a-z0-9' ]+"), " ").replace(Regex("\\s+"), " ").trim()

        /** What [sentence] claims to have done (or to be doing), or null. */
        fun claimIn(sentence: String): Claim? {
            if (sentence.trimEnd().trimEnd('"', '\'', ')', '”').endsWith("?")) return null
            val unquoted = QUOTED.replace(sentence, " ")
            val t = plain(unquoted)
            if (t.isEmpty()) return null
            if (HONEST.containsMatchIn(t)) return null
            for (r in RULES) if (r.first.matches(t)) return r.claim
            for (r in RULES) {
                val m = r.gerund.matchEntire(t) ?: continue
                val rest = m.groups["rest"]!!.value
                if (STATEMENT.containsMatchIn(rest)) continue
                return r.claim
            }
            PASSIVE.matchEntire(t)?.takeIf { m ->
                // "Music is on!", never "the song is on the album".
                m.groups["v"]!!.value != "on" || m.groups["rest"]!!.value.isBlank()
            }?.let { m ->
                val obj = m.groups["obj"]!!.value
                return when {
                    Regex("^(?:$CLOCK_OBJ)$").matches(obj) -> Claim.CLOCK
                    Regex("^(?:$CONTACT_OBJ)$").matches(obj) -> Claim.CONTACT
                    Regex("^(?:$MUSIC_OBJ)$").matches(obj) -> Claim.PLAY
                    else -> Claim.OTHER
                }
            }
            if (NOW_PLAYING.matches(t) || HERE_BY.matches(t)) return Claim.PLAY
            if (ON_THE_CLOCK.containsMatchIn(t)) return Claim.CLOCK
            if (ON_THE_PHONE.containsMatchIn(t)) return Claim.CONTACT
            if (COMING_UP.matches(t)) return Claim.OTHER
            return null
        }
    }
}
