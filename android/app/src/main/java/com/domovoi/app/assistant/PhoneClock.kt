package com.domovoi.app.assistant

import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.chrono.IsoChronology
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeFormatterBuilder
import java.time.format.FormatStyle
import java.util.Locale

/**
 * How this phone shows the time: its time zone, its locale and the owner's
 * 12/24-hour setting. Read again for every turn: any of them can change
 * while the app runs (a trip, a settings change).
 *
 * The words are English, whatever the phone's language, because the
 * assistant hears and speaks English (Moonshine is English-only, the persona
 * is English); the phone's region still sets the order and the clock, so a
 * phone set to German reads the date as en-DE does ("Thursday, 15 October
 * 2026", 15:42). The 12/24-hour setting wins over the region's habit.
 */
data class ClockStyle(val zone: ZoneId, val locale: Locale, val is24Hour: Boolean) {
    /** The locale the words are written in (see the class comment). */
    val words: Locale
        get() = when {
            locale.language == "en" -> locale
            locale.country.isEmpty() -> Locale.ENGLISH
            else -> runCatching { Locale.Builder().setLanguage("en").setRegion(locale.country).build() }
                .getOrDefault(Locale.ENGLISH)
        }

    /** [epochMs] on this phone's clock. */
    fun at(epochMs: Long): ZonedDateTime = Instant.ofEpochMilli(epochMs).atZone(zone)

    /** "3:42 PM", or "15:42" on a 24-hour phone. */
    fun time(t: ZonedDateTime): String = plain(
        runCatching { DateTimeFormatter.ofPattern(timePattern(), words).format(t) }
            .getOrElse { DateTimeFormatter.ofPattern(if (is24Hour) "HH:mm" else "h:mm a", Locale.ENGLISH).format(t) },
    )

    /** "Thursday, October 15, 2026" (en-US), "Thursday, 15 October 2026" (en-GB). */
    fun date(t: ZonedDateTime): String = plain(
        runCatching { DateTimeFormatter.ofLocalizedDate(FormatStyle.FULL).withLocale(words).format(t) }
            .getOrElse { DateTimeFormatter.ofPattern("EEEE, MMMM d, yyyy", Locale.ENGLISH).format(t) },
    )

    /** "Thursday". */
    fun weekday(t: ZonedDateTime): String = DateTimeFormatter.ofPattern("EEEE", words).format(t)

    /** "October". */
    fun month(t: ZonedDateTime): String = DateTimeFormatter.ofPattern("MMMM", words).format(t)

    /** "Pacific Daylight Time". */
    fun zoneName(t: ZonedDateTime): String = plain(DateTimeFormatter.ofPattern("zzzz", words).format(t))

    /** The region's short time pattern, turned to the phone's 12/24-hour
     *  setting when the two disagree. */
    private fun timePattern(): String {
        val local = DateTimeFormatterBuilder.getLocalizedDateTimePattern(null, FormatStyle.SHORT, IsoChronology.INSTANCE, words)
        val twelveHour = local.any { it == 'h' || it == 'K' }
        return when {
            is24Hour -> if (twelveHour) "HH:mm" else local
            else -> if (twelveHour) local else "h:mm a"
        }
    }

    companion object {
        /** This phone now; [is24Hour] is the owner's setting
         *  (android.text.format.DateFormat.is24HourFormat). */
        fun system(is24Hour: Boolean): ClockStyle = ClockStyle(ZoneId.systemDefault(), Locale.getDefault(), is24Hour)

        /** "UTC-07:00", "UTC+05:30", "UTC". */
        fun utcOffset(t: ZonedDateTime): String = if (t.offset.totalSeconds == 0) "UTC" else "UTC${t.offset.id}"

        /** Newer locale data puts a narrow no-break space before "PM": a
         *  plain one reads and speaks the same everywhere. */
        private fun plain(s: String): String = s.replace(' ', ' ').replace(' ', ' ')
    }
}

/** What a question about the clock asks for. */
enum class ClockQuestion { TIME, DATE, DAY, MONTH, YEAR, DATE_AND_TIME }

/**
 * The questions the phone answers from its own clock, at once and without
 * the model ("what time is it", "what's the date", "what day is it", "what
 * year is it"...), however the speech to text writes them (capitals,
 * punctuation, "whats", "todays", a "hey Domovoi" before or a "please"
 * after).
 *
 * Narrow on purpose: the whole question must be one of these forms, so
 * anything more ("what time is it in Tokyo", "what day is Christmas", "how
 * long until Friday", "what's the date tomorrow") goes to the model, which is
 * told the phone's clock every turn (Persona.clockLine).
 */
object ClockQuestions {
    /** Longer than this is never just a clock question. */
    private const val MAX_CHARS = 80

    private const val FILLER = "(?:hey|hi|hello|ok|okay|so|um|uh|erm|er|hmm|well|excuse me|sorry|please|domovoi|domovoy|domo)"
    private const val LEAD_IN = "(?:(?:can|could|would|will) you (?:please )?tell me|please tell me|tell me|do you know|" +
        "(?:i want|i would like|id like) to know)"
    private const val SUFFIX = "(?:please|now|right now|currently|today|at the moment|exactly|again|here|for me|" +
        "thanks|thank you|domovoi|domovoy|domo)"

    /** A question as it may be asked straight out (or after a lead-in). */
    private val DIRECT: List<Pair<ClockQuestion, String>> = listOf(
        ClockQuestion.DATE_AND_TIME to "what is (?:the |todays )?(?:date and time|time and date)",
        ClockQuestion.TIME to "what time is it|what hour is it|what is the (?:current )?time",
        ClockQuestion.DATE to "what is (?:the (?:current )?|todays )date|what date is it|what is today|todays date",
        ClockQuestion.DAY to "(?:what|which) day (?:of the week )?is (?:it|today)|what is the day|what weekday is it",
        ClockQuestion.MONTH to "(?:what|which) month is it|what is the (?:current )?month|what month are we in",
        ClockQuestion.YEAR to "(?:what|which) year is it|what is the (?:current )?year|what year are we in",
    )

    /** The same asked indirectly, after a lead-in only ("do you know what
     *  time it is", "can you tell me the date"). */
    private val INDIRECT: List<Pair<ClockQuestion, String>> = listOf(
        ClockQuestion.DATE_AND_TIME to "the (?:date and time|time and date)",
        ClockQuestion.TIME to "what time it is|what the time is|the (?:current )?time",
        ClockQuestion.DATE to "what date it is|what the date is|what todays date is|the (?:current )?date|the date today",
        ClockQuestion.DAY to "what day (?:of the week )?it is|what day today is|the day",
        ClockQuestion.MONTH to "what month it is|the (?:current )?month",
        ClockQuestion.YEAR to "what year it is|the (?:current )?year",
    )

    private val forms: List<Pair<ClockQuestion, Regex>> =
        DIRECT.map { (q, core) -> q to Regex("^(?:$FILLER )*(?:$LEAD_IN )?(?:$core)(?: $SUFFIX)*$") } +
            INDIRECT.map { (q, core) -> q to Regex("^(?:$FILLER )*$LEAD_IN (?:$core)(?: $SUFFIX)*$") }

    /** What [text] asks of the clock, or null: a question for the model. */
    fun match(text: String): ClockQuestion? {
        if (text.length > MAX_CHARS) return null
        val said = normalize(text)
        if (said.isEmpty()) return null
        return forms.firstOrNull { (_, re) -> re.matches(said) }?.first
    }

    /** "what time is twelve to nine": a clock time said as words, read
     *  back as the clock shows it (Fold check 2026-10-21: the model said
     *  11:00 AM). Only "X to/past/after/before Y" and "N tonight". */
    private val READING = Regex("^(?:$FILLER )*(?:$LEAD_IN )?(?:what time is|what is|when is) (?<t>.+?)(?: $SUFFIX)*$")
    private val RELATIVE_WORDS = Regex("\\b(?:past|after|to|till|before|of|tonight)\\b")

    /** The reply to [text] when it asks what a spoken clock time is, or null. */
    fun reading(text: String, style: ClockStyle, now: ZonedDateTime): String? {
        if (text.length > MAX_CHARS) return null
        val said = toNight(normalize(text))
        val t = READING.matchEntire(said)?.groups?.get("t")?.value?.trim() ?: return null
        if (!RELATIVE_WORDS.containsMatchIn(t)) return null
        val s = parseTime(t) ?: return null
        val lead = t.replaceFirstChar { it.uppercase() }
        val shown = when {
            s.pm == null -> String.format(Locale.US, "%d:%02d", s.hour, s.minute)
            else -> style.time(now.withHour(s.hour).withMinute(s.minute))
        }
        val midnight = if (s.pm == false && s.hour == 0 && s.minute == 0) ", midnight" else ""
        return "$lead is $shown$midnight."
    }

    /** The reply, as it is shown and spoken. */
    fun answer(question: ClockQuestion, now: ZonedDateTime, style: ClockStyle): String = when (question) {
        ClockQuestion.TIME -> "It's ${style.time(now)}."
        ClockQuestion.DATE -> "Today is ${style.date(now)}."
        ClockQuestion.DAY -> "It's ${style.weekday(now)}."
        ClockQuestion.MONTH -> "It's ${style.month(now)}."
        ClockQuestion.YEAR -> "It's ${now.year}."
        ClockQuestion.DATE_AND_TIME -> "It's ${style.time(now)} on ${style.date(now)}."
    }

    /** Lower case, apostrophes dropped ("what's" → "whats" → "what is"),
     *  everything else not a letter or digit a space. */
    internal fun normalize(text: String): String =
        text.lowercase(Locale.ROOT)
            .replace(Regex("['’‘`´]"), "")
            .replace(Regex("[^\\p{L}\\p{N}]+"), " ")
            .trim()
            .replace(Regex("\\bwhats\\b"), "what is")
}
