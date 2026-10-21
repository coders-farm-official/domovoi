package com.domovoi.app.assistant

import kotlinx.serialization.Serializable
import java.time.ZonedDateTime
import java.time.temporal.ChronoUnit
import java.time.temporal.TemporalAdjusters

/**
 * A hand-off (owner, 2026-10-21): another app does the work and the person
 * finishes there. Domovoi only fills it in: the dialer with the number, a
 * text in Messages, an email draft, directions in Maps, a new event in the
 * calendar app's own editor, a link, the share sheet. None needs a
 * permission; each shows a card first ("Text Sam?") and launches only on a
 * tap.
 *
 * N2 adds timers and alarms through the Clock app (AlarmClock) beside these,
 * asked first the same way: they are [HandOffKind]s of their own then, and
 * this card is their confirmation.
 */
@Serializable
enum class HandOffKind {
    CALL, TEXT, EMAIL, DIRECTIONS, EVENT, LINK, SHARE,

    /** A kind this version doesn't know (a newer app wrote it): shown as
     *  nothing, never launched. */
    OTHER,
}

@Serializable
data class HandOff(
    val kind: HandOffKind = HandOffKind.OTHER,
    /** The person as said ("Sam"), before a contact is chosen. */
    val who: String? = null,
    /** The contact's name once one is chosen or found. */
    val name: String? = null,
    val number: String? = null,
    val email: String? = null,
    /** A text's words, an email's body, what is shared. */
    val body: String? = null,
    val subject: String? = null,
    /** Where to (directions). */
    val place: String? = null,
    val url: String? = null,
    /** An event's title. */
    val title: String? = null,
    val beginMs: Long? = null,
    val endMs: Long? = null,
    val allDay: Boolean = false,
) {
    /** A call or text still waiting for its person (the contact picker). */
    val needsContact: Boolean
        get() = (kind == HandOffKind.CALL || kind == HandOffKind.TEXT) && number.isNullOrBlank()
}

/** Where a card stands. */
@Serializable
enum class CardState { OPEN, DONE, DISMISSED }

/** The kind of card a reply carries. */
@Serializable
enum class CardKind { HANDOFF, PERMISSION }

/**
 * A card under a reply: a hand-off waiting for its tap, or a permission
 * asked on first use. Kept with the message, so a conversation opened later
 * shows what was done. Every field has a default (old files still read).
 */
@Serializable
data class PhoneCard(
    val kind: CardKind = CardKind.HANDOFF,
    val state: CardState = CardState.OPEN,
    val handOff: HandOff? = null,
    /** PERMISSION: which one (PhonePermission's name). */
    val permission: String? = null,
    /** PERMISSION: the question answered once it is decided (its text). */
    val question: String? = null,
) {
    val phonePermission: PhonePermission? get() = permission?.let { p -> PhonePermission.entries.firstOrNull { it.name == p } }
}

object HandOffs {
    private const val MAX_CHARS = 300

    private const val LEAD = "(?:(?:can|could|would|will) you (?:please )?|please |hey domovoi |domovoi |ok |okay )*"
    private const val SUFFIX = "(?: (?:please|for me|now|thanks|thank you))*"

    private val call = Regex(
        "^${LEAD}(?:(?:call|phone|ring|dial) (?<who>.+?)|give (?<who2>.+?) a (?:call|ring))" +
            "(?: (?:on|at) (?:their|his|her) (?:mobile|cell|phone|home|work))?$SUFFIX$",
    )

    /** Words a text's message often starts with, when it follows the name
     *  with no "saying": "text Sam I'm running late". */
    private const val BODY_START = "(?:i|i'm|im|i'll|ill|i've|ive|we|we're|on my way|running|be|see you|call me|can you|could you|" +
        "are you|do you|don't|dont|happy|love|thanks|thank you|sorry|good|what|where|when|how|leaving|almost|home|ok|okay|yes|no)\\b"
    private val text = Regex(
        "^${LEAD}(?:text|message|send (?:a )?(?:text|message|sms)(?: message)? to) (?<who>.+?)" +
            "(?:(?::|,| -) (?<body>.+)| (?<body2>$BODY_START.*))?$",
    )
    private val textSaying = Regex("^${LEAD}(?:text|message) (?<who>.+?) (?:saying|that says|to say|that) (?<body>.+)$")
    private val email = Regex("^${LEAD}(?:email|e mail|write an email to|send an email to|draft an email to) (?<who>.+?)(?: (?:about|saying|regarding|re) (?<about>.+))?$SUFFIX$")
    private val directions = Regex(
        // Not "how do I get to …": that is as often "… sleep" or "… level 5".
        "^${LEAD}(?:(?:give me |get |show me )?directions to|navigate to|take me to|route to|drive me to|" +
            "(?:open )?maps (?:to|for)) (?<place>.+?)$SUFFIX$",
    )
    private val event = Regex(
        "^${LEAD}(?:add|put|schedule|create|make) (?:an? (?:event|appointment|meeting|reminder in my calendar) (?:for |called )?)?(?<title>.+?)" +
            " (?:to|on|in) (?:my|the) calendar(?<when> .+)?$SUFFIX$",
    )
    private val eventFor = Regex(
        "^${LEAD}(?:add|create|make|schedule) (?:an? )?(?:calendar )?(?<noun>event|appointment|meeting) (?:called |for )?(?<title>.+?)(?<when> (?:today|tonight|tomorrow|on|at|this|next) .+)?$SUFFIX$",
    )
    private val link = Regex("^${LEAD}(?:open|go to|visit|show me|launch) (?:the )?(?:link |website |site |page )?(?<url>(?:https?://)?[a-z0-9-]+(?:\\.[a-z0-9-]+)+(?:/\\S*)?)$SUFFIX$")
    private val share = Regex("^${LEAD}(?:share|send) (?:this|that|it|your (?:answer|reply)|the (?:answer|reply)|what you (?:said|just said))(?: with (?:someone|somebody|a friend))?$SUFFIX$")

    /** Callees that are not people: "call it a day", "call me Ishmael". */
    private val notAPerson = Regex(
        "^(?:it|that|this|me|myself|us|you|yourself|them|him|her|back|off|out|home|for|from|about|of|to|a|an|a taxi|an uber|a doctor|911|the police|" +
            "an ambulance|it a day|it a night|attention|in|up|a meeting|a vote|someone|somebody|anyone)\\b",
    )

    /** A hand-off [text] asks for, or null (then it is a question for the
     *  model). [lastReply]: Domovoi's last words in this chat, for "share
     *  this". [now]: for an event's day and time. */
    fun match(text: String, lastReply: String?, now: ZonedDateTime): HandOff? {
        if (text.length > MAX_CHARS) return null
        val raw = text.trim().trimEnd('.', '!', '?')
        val t = norm(raw)
        if (t.isEmpty()) return null

        share.matchEntire(t)?.let {
            val said = lastReply?.takeIf { r -> r.isNotBlank() } ?: return null
            return HandOff(HandOffKind.SHARE, body = said)
        }
        link.matchEntire(t)?.let { m ->
            val explicit = urlIn(raw)
            val u = explicit ?: m.groups["url"]!!.value
            // "open invoice.zip" is a file, not a site: without a scheme it
            // must start with www. or end in a well-known web domain.
            if (explicit == null && !looksLikeSite(u)) return null
            val full = if (u.startsWith("http://", true) || u.startsWith("https://", true)) u else "https://$u"
            return if (safeLink(full)) HandOff(HandOffKind.LINK, url = full) else null
        }
        directions.matchEntire(t)?.let { m ->
            val place = original(raw, m.groups["place"]!!.value)
            return HandOff(HandOffKind.DIRECTIONS, place = place)
        }
        (textSaying.matchEntire(t) ?: this.text.matchEntire(t))?.let { m ->
            val who = m.groups["who"]!!.value
            if (notAPerson.containsMatchIn(who) && phoneNumber(who) == null) return null
            val body = (m.groups["body"] ?: m.groups["body2"])?.value?.let { rest(text.trim(), it) }
            return person(HandOffKind.TEXT, original(raw, who))?.copy(body = body?.replaceFirstChar { it.uppercase() })
        }
        call.matchEntire(t)?.let { m ->
            val who = (m.groups["who"] ?: m.groups["who2"])!!.value
            if (notAPerson.containsMatchIn(who) && phoneNumber(who) == null) return null
            return person(HandOffKind.CALL, original(raw, who))
        }
        email.matchEntire(t)?.let { m ->
            val who = original(raw, m.groups["who"]!!.value)
            val about = m.groups["about"]?.value?.let { original(raw, it) }
            val address = Regex("[^\\s@]+@[^\\s@]+\\.[^\\s@]+").find(raw)?.value
            return HandOff(HandOffKind.EMAIL, who = if (address == null) who else null, email = address, subject = about?.replaceFirstChar { it.uppercase() })
        }
        (event.matchEntire(t) ?: eventFor.matchEntire(t))?.let { m ->
            var title = original(raw, m.groups["title"]!!.value)
            var whenText = m.groups["when"]?.value?.trim()
            // "schedule a meeting for next tuesday": all of it is the when;
            // the event is just "Meeting".
            val noun = runCatching { m.groups["noun"]?.value }.getOrNull()
            val whole = listOfNotNull(title, whenText).joinToString(" ").trim()
            if (noun != null && WHEN_ONLY.matches(norm(whole))) {
                title = noun
                whenText = whole
            }
            // "add lunch with Sam tomorrow at noon to my calendar": the time
            // was said inside the title.
            if (whenText == null) {
                splitWhen(title)?.let { (t2, w) ->
                    title = t2
                    whenText = w
                }
            }
            val (begin, allDay) = whenText?.let { parseWhen(norm(it), now) } ?: (null to false)
            val end = begin?.let { if (allDay) it + DAY_MS else it + HOUR_MS }
            return HandOff(
                HandOffKind.EVENT,
                title = title.trim().replaceFirstChar { it.uppercase() },
                beginMs = begin,
                endMs = end,
                allDay = allDay,
            )
        }
        return null
    }

    /** "smsto:5550123?body=On%20my%20way": the words in the link, where
     *  Google Messages reads them (it ignores the sms_body extra). */
    fun smsLink(number: String, body: String?): String =
        "smsto:" + percent(number) + (body?.takeIf { it.isNotBlank() }?.let { "?body=" + percent(it) } ?: "")

    /** RFC 3986 percent-encoding: all but the unreserved characters. */
    internal fun percent(s: String): String = buildString {
        for (b in s.toByteArray(Charsets.UTF_8)) {
            val c = b.toInt() and 0xff
            if (c in 'a'.code..'z'.code || c in 'A'.code..'Z'.code || c in '0'.code..'9'.code || c.toChar() in "-._~") append(c.toChar())
            else append('%').append("0123456789ABCDEF"[c shr 4]).append("0123456789ABCDEF"[c and 15])
        }
    }

    /** Only web links: never an intent:, file: or content: address typed
     *  into the chat. */
    fun safeLink(url: String): Boolean = SAFE_LINK.matches(url)

    /** A call or text for [who]: a number said aloud goes straight in. A
     *  "name" of more than four words is a sentence ("call mom and tell
     *  her…"): that's the model's. */
    private fun person(kind: HandOffKind, who: String): HandOff? {
        if (phoneNumber(who) == null && who.trim().split(Regex("\\s+")).size > MAX_NAME_WORDS) return null
        val number = phoneNumber(who)
        return if (number != null) HandOff(kind, number = number) else HandOff(kind, who = cleanName(who))
    }

    /** "my mom" → "mom"; "Sam's mobile" → "Sam". */
    internal fun cleanName(who: String): String =
        who.trim().removePrefix("my ").removePrefix("My ")
            .replace(Regex("(?i)'?s? (?:mobile|cell|phone|number|home|work)$"), "")
            .trim()

    /** Digits said as a number ("555 0123", "+1 (555) 010-2000"), or null. */
    internal fun phoneNumber(s: String): String? {
        val t = s.trim()
        if (!Regex("^\\+?[0-9 ().-]{3,20}$").matches(t)) return null
        val digits = t.filter { it.isDigit() || it == '+' }
        return digits.takeIf { it.count(Char::isDigit) >= 3 }
    }

    /** The card's question: "Text Sam?", "Open Maps to Pike Place?". */
    fun cardTitle(h: HandOff): String = when (h.kind) {
        HandOffKind.CALL -> "Call ${h.name ?: h.who ?: h.number ?: "someone"}?"
        HandOffKind.TEXT -> "Text ${h.name ?: h.who ?: h.number ?: "someone"}?"
        HandOffKind.EMAIL -> "Write an email" + (h.name ?: h.who ?: h.email)?.let { " to $it" }.orEmpty() + "?"
        HandOffKind.DIRECTIONS -> "Open Maps to ${h.place}?"
        HandOffKind.EVENT -> "Add \"${h.title}\" to your calendar?"
        HandOffKind.LINK -> "Open ${h.url}?"
        HandOffKind.SHARE -> "Share my last reply?"
        HandOffKind.OTHER -> ""
    }

    /** The card's reply text (what is said and kept in the chat). */
    fun replyText(h: HandOff, whenText: String?): String = when (h.kind) {
        HandOffKind.CALL -> if (h.needsContact) "Choose ${h.who ?: "who to call"} from your contacts, and I'll open the dialer with the number." else "I'll open the dialer with ${h.name ?: h.number} filled in; you press call."
        HandOffKind.TEXT -> if (h.needsContact) "Choose ${h.who ?: "who to text"} from your contacts, and I'll open Messages." else "I'll open Messages to ${h.name ?: h.number}" + (h.body?.let { " with \"$it\" written" } ?: "") + "; you press send."
        HandOffKind.EMAIL -> "I'll open a draft in your email app" + (h.subject?.let { " about \"$it\"" } ?: "") + "; you add the rest and send it."
        HandOffKind.DIRECTIONS -> "I'll open your maps app with directions to ${h.place}."
        HandOffKind.EVENT -> "I'll open your calendar app's editor with \"${h.title}\"" + (whenText?.let { " $it" } ?: "") + "; you check it and save."
        HandOffKind.LINK -> "I'll open ${h.url} in your browser."
        HandOffKind.SHARE -> "I'll open the share sheet with my last reply."
        HandOffKind.OTHER -> ""
    }

    /** The card's main button. */
    fun buttonLabel(h: HandOff): String = when (h.kind) {
        HandOffKind.CALL -> if (h.needsContact) "choose a contact" else "open the dialer"
        HandOffKind.TEXT -> if (h.needsContact) "choose a contact" else "open messages"
        HandOffKind.EMAIL -> "open email"
        HandOffKind.DIRECTIONS -> "open maps"
        HandOffKind.EVENT -> "open calendar"
        HandOffKind.LINK -> "open the link"
        HandOffKind.SHARE -> "share"
        HandOffKind.OTHER -> ""
    }

    /** "No app on this phone can …": a hand-off no app takes is not shown. */
    fun noApp(kind: HandOffKind): String = when (kind) {
        HandOffKind.CALL -> "This phone has no app that can make calls."
        HandOffKind.TEXT -> "This phone has no app that can send texts."
        HandOffKind.EMAIL -> "This phone has no email app."
        HandOffKind.DIRECTIONS -> "This phone has no maps app."
        HandOffKind.EVENT -> "This phone has no calendar app that can add events."
        HandOffKind.LINK -> "This phone has no browser to open links."
        HandOffKind.SHARE -> "Nothing on this phone can take a share."
        HandOffKind.OTHER -> "This version of the app can't do that."
    }

    // ── Words to times ───────────────────────────────────────────────────

    private val WEEKDAYS = listOf("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

    /** "at 3", "tomorrow at 3 pm", "on friday at noon", "next tuesday",
     *  "today at 15:30": the start, and whether it is all day (a day with
     *  no time). Null when nothing in it reads as a day or time. */
    internal fun parseWhen(t: String, now: ZonedDateTime): Pair<Long?, Boolean>? {
        var s = t.trim().removePrefix("for ").trim()
        var day = now.truncatedTo(ChronoUnit.DAYS)
        var dayGiven = false
        Regex("^(?:on )?(today|tonight|tomorrow|(?:this |next )?(?:${WEEKDAYS.joinToString("|")}))\\b").find(s)?.let { m ->
            val w = m.groupValues[1]
            dayGiven = true
            day = when {
                w == "today" || w == "tonight" -> day
                w == "tomorrow" -> day.plusDays(1)
                else -> {
                    val name = w.removePrefix("this ").removePrefix("next ")
                    val dow = java.time.DayOfWeek.of(WEEKDAYS.indexOf(name) + 1)
                    val next = day.with(TemporalAdjusters.next(dow))
                    if (w.startsWith("next ") && ChronoUnit.DAYS.between(day, next) < 7 && day.dayOfWeek < dow) next.plusWeeks(1) else next
                }
            }
            s = s.substring(m.range.last + 1).trim()
            if (w == "tonight" && s.isEmpty()) return day.withHour(20).toInstant().toEpochMilli() to false
        }
        s = s.removePrefix("at ").trim()
        if (s.isEmpty()) return if (dayGiven) day.toInstant().toEpochMilli() to true else null
        val time = clockTime(s) ?: return if (dayGiven) day.toInstant().toEpochMilli() to true else null
        var at = day.withHour(time.first).withMinute(time.second)
        // "at 3" with no day: the next 3 o'clock to come, afternoon first
        // for a bare 1-7 (people mean 3 pm, not 3 am).
        if (!dayGiven && at.isBefore(now)) at = at.plusDays(1)
        return at.toInstant().toEpochMilli() to false
    }

    /** "3", "3 pm", "3:30pm", "15:30", "noon", "midnight" → hour 0-23 and
     *  minute. A bare 1-7 means the afternoon. */
    internal fun clockTime(s: String): Pair<Int, Int>? {
        val t = s.trim().replace(".", "")
        if (t == "noon" || t == "midday") return 12 to 0
        if (t == "midnight") return 0 to 0
        val m = Regex("^(\\d{1,2})(?:[: ](\\d{2}))? ?(am|pm|in the morning|in the afternoon|in the evening|at night)?$").matchEntire(t) ?: return null
        var h = m.groupValues[1].toInt()
        val min = m.groupValues[2].takeIf { it.isNotEmpty() }?.toInt() ?: 0
        if (h > 23 || min > 59) return null
        when (m.groupValues[3]) {
            "am", "in the morning" -> if (h == 12) h = 0
            "pm", "in the afternoon", "in the evening", "at night" -> if (h < 12) h += 12
            else -> if (h in 1..7) h += 12
        }
        return h to min
    }

    /** "Lunch with Sam tomorrow at noon" → ("Lunch with Sam", "tomorrow at noon"). */
    private fun splitWhen(title: String): Pair<String, String>? {
        val m = Regex("(?i)^(.+?) ((?:on )?(?:today|tonight|tomorrow|(?:this |next )?(?:${WEEKDAYS.joinToString("|")}))(?: at .+)?|at \\d{1,2}(?::\\d{2})? ?(?:am|pm)?|at noon|at midnight)$")
            .matchEntire(title.trim()) ?: return null
        return m.groupValues[1] to m.groupValues[2]
    }

    /** Lower case, curly apostrophes made straight, spaces squeezed; unlike
     *  the questions' normalizing, punctuation inside (a URL, an email
     *  address, a number, "I'm") is kept. */
    internal fun norm(s: String): String = flat(s).lowercase().trim().trimEnd('.', '!', '?', ',')

    private fun flat(s: String): String = s.replace(Regex("[’‘`´]"), "'").replace(Regex("\\s+"), " ")

    /** [raw] from where [lowered] starts to its end, as written: a text's
     *  words keep their capitals and their closing "?". */
    private fun rest(raw: String, lowered: String): String {
        val f = flat(raw)
        val i = f.lowercase().indexOf(lowered)
        return if (i >= 0) f.substring(i).trim() else lowered
    }

    /** [lowered] as the person wrote it in [raw] (names keep their capitals). */
    private fun original(raw: String, lowered: String): String {
        val f = flat(raw)
        val i = f.lowercase().indexOf(lowered)
        return if (i >= 0) f.substring(i, i + lowered.length) else lowered
    }

    private fun urlIn(raw: String): String? = Regex("(?i)\\bhttps?://\\S+").find(raw)?.value?.trimEnd('.', ',', ')')

    /** A whole phrase that is only a day and/or a time. */
    private val WHEN_ONLY = Regex(
        "^(?:(?:on )?(?:today|tonight|tomorrow|(?:this |next )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))(?: at .+)?|at .+)$",
    )

    /** Web domains people name without "https://"; not the ones that are
     *  also file extensions (.zip, .mov). */
    private val SITE_TLDS = setOf(
        "com", "org", "net", "edu", "gov", "io", "dev", "app", "co", "info", "me", "tv", "ai", "uk", "us", "ca", "au",
        "de", "fr", "es", "it", "nl", "se", "no", "dk", "fi", "ie", "nz", "jp", "in", "br", "eu", "ch", "at", "be", "pl",
    )

    /** File extensions that are never read as a site, whatever follows. */
    private val FILE_EXTS = setOf(
        "zip", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "txt", "md", "csv", "json", "xml",
        "jpg", "jpeg", "png", "gif", "heic", "webp", "mov", "mp4", "mkv", "avi", "mp3", "wav", "flac", "m4a", "apk", "exe", "py", "kt",
    )

    /** "www.example.org", "example.org/a": a site; "invoice.zip",
     *  "notes.txt", "readme.md": a file. */
    internal fun looksLikeSite(u: String): Boolean {
        val host = u.substringBefore('/').lowercase()
        if (host.startsWith("www.")) return true
        val tld = host.substringAfterLast('.')
        return tld in SITE_TLDS && tld !in FILE_EXTS
    }

    private val SAFE_LINK = Regex("^https?://[^\\s/?#@]+\\.[^\\s/?#@]+(?:[/?#]\\S*)?$", RegexOption.IGNORE_CASE)

    private const val MAX_NAME_WORDS = 4

    private const val HOUR_MS = 3_600_000L
    private const val DAY_MS = 24 * HOUR_MS
}
