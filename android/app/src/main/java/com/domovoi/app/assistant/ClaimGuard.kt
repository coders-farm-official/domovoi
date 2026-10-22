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
 * Streamed: [push] each delta and get back what is safe to show (whole
 * sentences, checked); [finish] the rest. A reply with no claim reads
 * exactly as the model wrote it.
 */
class ClaimGuard {
    private val pending = StringBuilder()
    private val said = mutableSetOf<Claim>()

    /** Whether any sentence was replaced. */
    val replaced: Boolean get() = said.isNotEmpty()

    /** Text pushed and not yet given back (an unfinished sentence). */
    val holding: Boolean get() = pending.isNotEmpty()

    /** The sentence being written can't be a claim (its first word says
     *  so): it streams through as it comes. */
    private var free = false
    /** How much of [pending] has already been given back (a free sentence). */
    private var given = 0

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
            }
            if (end == null) {
                if (!free && cannotClaim(pending)) free = true
                if (free) {
                    out.append(pending, given, pending.length)
                    given = pending.length
                }
                break
            }
            if (free) out.append(pending, given, end) else out.append(checked(pending.substring(0, end)))
            pending.delete(0, end)
            given = 0
            free = false
        }
        return out.toString()
    }

    fun finish(): String {
        val rest = pending.substring(given)
        val whole = pending.toString()
        val wasFree = free
        pending.setLength(0)
        given = 0
        free = false
        return when {
            whole.isEmpty() -> ""
            wasFree -> rest
            else -> checked(whole)
        }
    }

    /** Whether a sentence beginning [start] can't be a claim: its first
     *  word (or the part of it written so far) begins none of the words a
     *  claim starts with. Most sentences stream at once this way. */
    private fun cannotClaim(start: CharSequence): Boolean {
        val t = start.toString().trimStart()
        val words = SentenceSplitter.speakable(t).lowercase().replace(Regex("[’‘`´]"), "'")
            .split(Regex("[^a-z0-9']+")).filter { it.isNotEmpty() }
        val first = words.firstOrNull() ?: return false
        val complete = words.size > 1 || (t.isNotEmpty() && !t.last().isLetterOrDigit() && t.last() != '\'')
        return if (complete) first !in CLAIM_STARTS else CLAIM_STARTS.none { it.startsWith(first) }
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
        /** The first words a claim can open with (see [claimIn]'s forms). */
        private val CLAIM_STARTS: Set<String> = (
            "i i've i'll i'm i'd i've now ok okay sure done great alright all right there here got you no now your the a an my " +
                "playing pausing resuming skipping stopping queueing queuing shuffling putting starting calling dialing dialling ringing " +
                "texting messaging emailing sending forwarding sharing saving printing downloading deleting booking ordering buying " +
                "turning switching opening launching recording setting creating adding scheduling reminding " +
                "timer timers alarm alarms reminder reminders countdown message messages text texts email emails call calls song songs " +
                "music playlist playlists album albums track tracks file files document documents note notes order orders booking print photo photos"
            ).split(" ").toSet()

        const val CANT_LINE = "I can't do that from this chat."
        const val PLAY_LINE = "I can't play that from this chat. To play music saved on this phone, say \"play\" and the song, artist or album."
        const val CLOCK_LINE = "I can't set that from this chat. Say \"set a timer for 10 minutes\" or \"set an alarm for 7 am\" and I'll ask before sending it to the Clock app."
        const val CONTACT_LINE = "I can't do that from this chat. Say \"call\" or \"text\" and a name, and I'll open the app for you to finish it."

        private const val I = "(?:i|i've|i have|i just|i've just|i have just|i'll|i will|i'm|i am|i'm now|i am now|i've now|i'll now|i'll go ahead and|i went ahead and)"
        private const val LEAD = "(?:(?:ok|okay|sure|done|great|alright|all right|there you go|here you go|got it|you got it|no problem)(?: )?)*"

        /** One kind of claim: its verbs, and what must follow them for the
         *  sentence to be a claim ("play devil's advocate" isn't music). */
        private class Rule(val claim: Claim, verbs: String, after: String) {
            val first = Regex("^$LEAD$I (?:$verbs)\\b(?<rest>$after)")
            val gerund = Regex("^$LEAD(?:now )?(?:$verbs)\\b(?<rest>$after)")
        }

        private const val NOT_MUSIC = "(?! (?:devil's advocate|a game|games|along|it safe|it cool|it by ear|the role|a role|a part|the part|a trick|tricks|a prank|with|nice|dead|catch|chess|cards|trivia|twenty questions|20 questions|hide and seek|pretend|the victim|the fool|hooky|tag|fair|by the rules|the long game|word games|a riddle))"
        private const val MEDIA = "(?:the|your|it|that|this|music|some|playback|song|track|audio|player|album|playlist|radio)"
        private const val CLOCK = ".*\\b(?:timer|alarm|reminder|countdown)s?\\b.*"
        private const val THING = "(?:it|that|this|them|those|the|your|a|an)? ?(?:it|that|this|them|file|document|photo|picture|pdf|note|notes|page|app|settings|wifi|wi fi|bluetooth|flashlight|torch|light|lights|tv|order|ticket|tickets|table|flight|copy|draft|letter|list|recording|song|video)\\b.*"

        private val RULES = listOf(
            Rule(Claim.CLOCK, "set|setting|set up|setting up|started|starting|created|creating|added|adding|scheduled|scheduling|start|create|add|schedule", CLOCK),
            Rule(Claim.CLOCK, "remind|reminding", " you\\b.*"),
            Rule(Claim.PLAY, "played|play|playing|now playing", "$NOT_MUSIC.+"),
            Rule(Claim.PLAY, "paused|pause|pausing|resumed|resume|resuming", " $MEDIA\\b.*"),
            Rule(Claim.PLAY, "skipped|skip|skipping", " (?:to the next|to the previous|the song|the track|this song|that song|this track|ahead|forward|it|this one|that one)\\b.*"),
            Rule(Claim.PLAY, "stopped|stop|stopping", " (?:the music|the song|playback|the audio|the player|your music|the track)\\b.*"),
            Rule(Claim.PLAY, "queued|queue|queueing|queuing|shuffled|shuffle|shuffling", " .+"),
            Rule(Claim.PLAY, "put on|putting on|started playing|start playing|starting playing|started|start|starting", " (?:some |your |the |a )?(?:music|songs?|tracks?|album|playlist|radio|tunes|jazz)\\b.*"),
            Rule(Claim.CONTACT, "called|call|calling|dialed|dialled|dial|dialing|dialling|rang|ringing", " (?!it\\b|this\\b|that\\b|a day\\b|out\\b|back to\\b).+"),
            Rule(Claim.CONTACT, "texted|text|texting|messaged|message|messaging|emailed|email|emailing|sent|send|sending|forwarded|forward|forwarding", " .+"),
            Rule(Claim.CONTACT, "shared|share|sharing", " (?:it|that|this|the|your)\\b.*"),
            Rule(Claim.OTHER, "saved|save|saving|printed|print|printing|downloaded|download|downloading|deleted|delete|deleting|booked|book|booking|ordered|order|ordering|bought|buy|buying|turned on|turn on|turning on|turned off|turn off|turning off|switched on|switched off|opened|open|opening|launched|launch|launching|recorded|record|recording", " $THING"),
        )

        /** "Your timer is set.", "The message has been sent.", "Alarm set for 7." */
        private val PASSIVE = Regex("^$LEAD(?:your |the |a |an )?(?<obj>timer|alarm|reminder|countdown|message|text|email|call|song|music|playlist|album|track|file|document|note|order|booking|print job|photo)s? (?:is |has been |was |have been |are |now |is now )?(?<v>set|sent|saved|scheduled|created|started|playing|paused|printed|placed|booked|queued|on its way|on the way)\\b.*$")

        /** Words that make a sentence a refusal, a suggestion, a question or
         *  a how-to, not a claim. */
        private val HONEST = Regex("\\b(?:can't|cannot|can not|couldn't|could not|won't|unable|not able|don't|do not|didn't|did not|isn't|aren't|wasn't|haven't|have not|hasn't|no way|if you|you can|you could|you might|you may|you'd|you'll need|try saying|just say|say \"|ask me|would you|do you want|shall i|should i|want me to|like me to|how to|if i could|i wish|i'd love to|i would|i could|pretend|imagine|let's say)\\b")

        /** "Playing music can help you relax.": a gerund opening a statement. */
        private val STATEMENT = Regex("\\b(?:is|are|was|were|can|could|will|would|helps?|makes?|means?|requires?|involves?|has|have|takes?|gives?|lets?|keeps?|feels?|might|may|should|seems?|tends?|isn't|is a|is an|was a)\\b")

        internal fun plain(s: String): String = SentenceSplitter.speakable(s).lowercase()
            .replace(Regex("[’‘`´]"), "'")
            .replace(Regex("[^a-z0-9' ]+"), " ").replace(Regex("\\s+"), " ").trim()

        /** What [sentence] claims to have done (or to be doing), or null. */
        fun claimIn(sentence: String): Claim? {
            val t = plain(sentence)
            if (t.isEmpty() || sentence.trimEnd().trimEnd('"', '\'', ')', '”').endsWith("?")) return null
            if (HONEST.containsMatchIn(t)) return null
            for (r in RULES) if (r.first.matches(t)) return r.claim
            for (r in RULES) {
                val m = r.gerund.matchEntire(t) ?: continue
                val rest = m.groups["rest"]!!.value
                if (STATEMENT.containsMatchIn(rest)) continue
                return r.claim
            }
            PASSIVE.matchEntire(t)?.let { m ->
                return when (m.groups["obj"]!!.value) {
                    "timer", "alarm", "reminder", "countdown" -> Claim.CLOCK
                    "message", "text", "email", "call" -> Claim.CONTACT
                    "song", "music", "playlist", "album", "track" -> Claim.PLAY
                    else -> Claim.OTHER
                }
            }
            return null
        }
    }
}
