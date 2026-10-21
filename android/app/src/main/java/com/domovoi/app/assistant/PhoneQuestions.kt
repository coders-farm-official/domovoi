package com.domovoi.app.assistant

import java.time.ZonedDateTime
import java.time.temporal.ChronoUnit
import java.util.Locale

/** A question a phone chat answers from the phone itself, at once. */
enum class PhoneQuestion {
    NOW_PLAYING,
    BATTERY,
    CHARGING,
    STORAGE,
    NETWORK,
    NEXT_ALARM,
    DO_NOT_DISTURB,
    TIMERS,
    PHONE_MODEL,
    ANDROID_VERSION,
    WHICH_MODEL,

    // Asked on first use (PhonePermission), but HEADPHONES: Android's audio
    // outputs name them with no permission.
    CALENDAR_TODAY,
    CALENDAR_TOMORROW,
    CALENDAR_NEXT,
    WHERE_AM_I,
    WIFI_NAME,
    HEADPHONES,

    /** Needs the internet, which a phone chat doesn't use: said plainly. */
    WEATHER,
    ;

    /** The permission the answer needs, if any. */
    val permission: PhonePermission?
        get() = when (this) {
            CALENDAR_TODAY, CALENDAR_TOMORROW, CALENDAR_NEXT -> PhonePermission.CALENDAR
            WHERE_AM_I, WIFI_NAME -> PhonePermission.LOCATION
            else -> null
        }
}

/** What a fast path answers: words, or first a permission request. */
sealed interface PhoneAnswer {
    data class Said(val text: String) : PhoneAnswer

    /** The answer needs [permission], never asked yet: the chat shows its
     *  reason and asks; [question] is answered once it is decided. */
    data class Ask(val permission: PhonePermission, val question: PhoneQuestion) : PhoneAnswer
}

/** What a fast path's answer is made from, read when it is asked. */
data class PhoneAnswerInputs(
    val now: ZonedDateTime,
    val style: ClockStyle,
    val phone: PhoneContext,
    /** The model answering, for "which model are you": "Gemma 4 E2B". */
    val modelName: String,
    val backend: LlmBackend,
)

/**
 * The questions a phone chat answers from the phone at once, with no model
 * (part 1's fast paths, like [ClockQuestions]): what's playing, the battery,
 * free space, Wi-Fi or mobile data, the next alarm, Do Not Disturb, the
 * timers, the phone and its Android, which model is answering; and, with the
 * owner's permission list of 2026-10-21, the phone's calendar, where am I,
 * the Wi-Fi network's name and the connected headphones, each asked for on
 * first use.
 *
 * Narrow like the clock's: the whole question must be one of these forms
 * (after the same fillers, lead-ins and endings), so "why does my battery
 * drain so fast" or "is it bad to charge overnight" go to the model.
 */
object PhoneQuestions {
    private const val MAX_CHARS = 90

    private const val FILLER = "(?:hey|hi|hello|ok|okay|so|um|uh|erm|er|hmm|well|excuse me|sorry|please|domovoi|domovoy|domo)"
    private const val LEAD_IN = "(?:(?:can|could|would|will) you (?:please )?tell me|please tell me|tell me|do you know|" +
        "(?:i want|i would like|id like) to know)"
    private const val SUFFIX = "(?:please|now|right now|currently|at the moment|again|for me|thanks|thank you|" +
        "on (?:my|this|the) phone|domovoi|domovoy|domo)"

    private const val WIFI = "(?:wifi|wi fi|wireless)"
    private const val MOBILE = "(?:mobile data|cellular|cellular data|cell data|data|mobile|4g|5g|lte)"

    private val FORMS: List<Pair<PhoneQuestion, String>> = listOf(
        PhoneQuestion.NOW_PLAYING to "what is playing|what song is (?:this|playing|on)|what is this song|what song is it|" +
            "what am i listening to|who sings this|who is singing|what music is playing|what is this track|what track is this|" +
            "what is on the player|whats playing",
        PhoneQuestion.CHARGING to "is (?:my |the |this )?(?:phone|battery) charging|is it charging|am i charging|" +
            "is (?:my |the )?(?:phone|battery) (?:plugged in|on the charger)",
        PhoneQuestion.BATTERY to "how much battery(?: do i have| is left| have i got| left| does it have)?(?: left)?|" +
            "what is (?:my|the) battery(?: level| at| percentage)?|(?:my |the )?battery(?: level| percentage)?|" +
            "how is (?:my|the) battery|is (?:my|the) battery low|how much charge(?: do i have| is left)?(?: left)?|" +
            "what percent is (?:my|the) battery|how charged is (?:my|the) phone",
        PhoneQuestion.STORAGE to "how much (?:free )?(?:storage|space)(?: is left| do i have(?: left)?| is free| have i got(?: left)?| left| is there)?|" +
            "is (?:my |the )?(?:phone|storage) full|how full is (?:my|the) (?:phone|storage)|what is my free (?:storage|space)",
        PhoneQuestion.NETWORK to "am i on (?:$WIFI|$MOBILE)(?: or (?:$WIFI|$MOBILE))?|am i (?:using|connected to) (?:$WIFI|$MOBILE)(?: or (?:$WIFI|$MOBILE))?|" +
            "is (?:my |the |this )?phone on (?:$WIFI|$MOBILE)(?: or (?:$WIFI|$MOBILE))?|am i online|" +
            "am i connected to the internet|do i have (?:internet|a connection|signal|data)|(?:what|which) connection am i (?:on|using)",
        PhoneQuestion.WIFI_NAME to "(?:what|which) $WIFI(?: network)? am i (?:on|connected to|using)|what is (?:the|my) $WIFI(?: network)?(?: name| called)?|" +
            "what is the name of (?:the|my) $WIFI(?: network)?|what network am i (?:on|connected to)",
        PhoneQuestion.NEXT_ALARM to "when is my (?:next )?alarm|what time is my (?:next )?alarm(?: set for)?|what is my next alarm|" +
            "do i have (?:an|any) alarms?(?: set)?|is (?:my |an |there an )?alarm set|what alarms do i have|when does my alarm go off|" +
            "what time does my alarm go off",
        PhoneQuestion.DO_NOT_DISTURB to "is do not disturb (?:turned )?on|is dnd on|am i (?:on|in) do not disturb|" +
            "is (?:my |the )?phone (?:on|in) do not disturb|is do not disturb off|is (?:my |the )?phone on silent|" +
            "is (?:my |the )?phone (?:silenced|muted)",
        PhoneQuestion.TIMERS to "what timers do i have|do i have any timers(?: running| set)?|what timers are (?:running|set|on)|" +
            "how (?:much time|long) is left on (?:my|the) timers?|(?:any|my) timers|list (?:my |the )?timers|" +
            "what reminders do i have|do i have any reminders|what are my timers|show (?:me )?my timers",
        PhoneQuestion.PHONE_MODEL to "what phone is this|which phone is this|what phone (?:do i have|am i using|is this)|" +
            "what (?:model|kind of phone|type of phone|make of phone) is this(?: phone)?|what model is (?:my|this) phone|what is this phone",
        PhoneQuestion.ANDROID_VERSION to "(?:what|which) (?:version of )?android(?: version)?(?: is this| am i (?:on|running|using)| do i have| does (?:it|this phone) (?:run|have)| is (?:it|this phone) (?:on|running))?|" +
            "what android version is this|what version of android is this phone on|what os (?:is this|am i on|version is this)",
        PhoneQuestion.WHICH_MODEL to "(?:which|what) (?:language |ai )?model are you|(?:which|what) (?:language |ai )?model is (?:this|answering|running)|" +
            "(?:which|what) ai (?:are you|is this)|what are you running on|are you (?:running )?on the (?:cpu|gpu)|" +
            "what model are you using|who made you|what llm are you",
        PhoneQuestion.CALENDAR_TOMORROW to "what is on my calendar tomorrow|what do i have (?:on )?tomorrow|do i have anything (?:on )?tomorrow|" +
            "what is my schedule (?:for )?tomorrow|what is on (?:my calendar|the calendar) for tomorrow|what is tomorrow looking like",
        PhoneQuestion.CALENDAR_NEXT to "what is my next (?:meeting|event|appointment)|when is my next (?:meeting|event|appointment)|" +
            "do i have any (?:meetings|events|appointments)(?: coming up)?",
        PhoneQuestion.CALENDAR_TODAY to "what is on (?:my|the) calendar(?: today| for today)?|what do i have (?:on )?today|" +
            "do i have anything (?:on )?today|what is my schedule(?: for)?(?: today)?|what is on my schedule(?: today)?|" +
            "what is on (?:my|the) agenda(?: today)?|whats on my calendar|what is today looking like",
        PhoneQuestion.WHERE_AM_I to "where am i|what is my location|where am i right now|where is this phone|what are my coordinates",
        PhoneQuestion.HEADPHONES to "(?:which|what) (?:headphones|earbuds|earphones|bluetooth devices?|bluetooth headphones|speaker) (?:are|is) connected|" +
            "are my (?:headphones|earbuds|earphones) connected|(?:is|are) (?:any )?(?:headphones|earbuds|bluetooth) connected|" +
            "what is connected (?:to|by) bluetooth|what bluetooth (?:devices|device) (?:are|is) connected",
        PhoneQuestion.WEATHER to "what is the weather(?: like)?(?: here| today| outside| right now| now)?|how is the weather(?: here| today)?|" +
            "is it going to rain(?: today)?|do i need an umbrella(?: today)?|what is the temperature(?: outside| here)?|how hot is it(?: outside)?|" +
            "how cold is it(?: outside)?",
    )

    private val forms: List<Pair<PhoneQuestion, Regex>> = FORMS.map { (q, core) ->
        q to Regex("^(?:$FILLER )*(?:$LEAD_IN )?(?:$core)(?: $SUFFIX)*$")
    }

    /** What [text] asks of the phone, or null: a question for the model. */
    fun match(text: String): PhoneQuestion? {
        if (text.length > MAX_CHARS) return null
        val said = norm(text)
        if (said.isEmpty()) return null
        return forms.firstOrNull { (_, re) -> re.matches(said) }?.first
    }

    /** As [ClockQuestions.normalize], plus "wi-fi" and "do not disturb"
     *  spelled one way. */
    internal fun norm(text: String): String = ClockQuestions.normalize(text)
        .replace(Regex("\\bwi fi\\b"), "wifi")
        .replace(Regex("\\bdo not disturb mode\\b"), "do not disturb")
        .replace(Regex("\\b(when|where|how|who)s\\b"), "$1 is")

    /** The reply to [q], from the phone now. */
    suspend fun answer(q: PhoneQuestion, inputs: PhoneAnswerInputs): PhoneAnswer {
        val phone = inputs.phone
        // Off Wi-Fi there is no network name to read: no reason to ask.
        if (q == PhoneQuestion.WIFI_NAME) phone.reader.network()?.takeIf { it.kind != NetworkKind.WIFI }?.let {
            return PhoneAnswer.Said(say(q, inputs))
        }
        q.permission?.let { p ->
            when (phone.permission(p)) {
                PermissionState.GRANTED -> Unit
                PermissionState.ASKABLE -> return PhoneAnswer.Ask(p, q)
                PermissionState.DECLINED -> return PhoneAnswer.Said(cannot(p, phone.serverKnown(), q))
            }
        }
        return PhoneAnswer.Said(say(q, inputs))
    }

    /** The answer once any permission it needs is held. */
    internal suspend fun say(q: PhoneQuestion, inputs: PhoneAnswerInputs): String {
        val r = inputs.phone.reader
        val nowMs = inputs.now.toInstant().toEpochMilli()
        return when (q) {
            PhoneQuestion.NOW_PLAYING -> nowPlaying(inputs.phone.facts())
            PhoneQuestion.BATTERY -> r.battery()?.let { b ->
                when {
                    b.full -> "The battery is full (${b.percent}%)" + if (b.charging) " and still plugged in." else "."
                    b.charging -> "The battery is at ${b.percent}% and charging."
                    else -> "The battery is at ${b.percent}%, not charging."
                }
            } ?: "I can't read the battery on this phone."
            PhoneQuestion.CHARGING -> r.battery()?.let { b ->
                if (b.charging) "Yes, it's charging. The battery is at ${b.percent}%." else "No, it isn't charging. The battery is at ${b.percent}%."
            } ?: "I can't read the battery on this phone."
            PhoneQuestion.STORAGE -> r.storage()?.let { s -> "You have ${gb(s.freeBytes)} free of ${gb(s.totalBytes)}." }
                ?: "I can't read this phone's storage."
            PhoneQuestion.NETWORK -> r.network()?.let { n ->
                when (n.kind) {
                    NetworkKind.WIFI -> "You're on Wi-Fi."
                    NetworkKind.MOBILE -> "You're on mobile data."
                    NetworkKind.ETHERNET -> "You're on a wired connection."
                    NetworkKind.OTHER -> "You're connected, but not over Wi-Fi or mobile data."
                    NetworkKind.NONE -> if (n.airplaneMode) "You're offline: airplane mode is on." else "You're offline: no network is connected."
                }
            } ?: "I can't read this phone's connection."
            PhoneQuestion.WIFI_NAME -> {
                val n = r.network()
                when {
                    n != null && n.kind != NetworkKind.WIFI -> "You're not on Wi-Fi" + if (n.kind == NetworkKind.MOBILE) ": you're on mobile data." else "."
                    else -> r.wifiName()?.let { "You're on the Wi-Fi network \"$it\"." }
                        ?: "I can't read the Wi-Fi network's name. Android shows it only with precise location on."
                }
            }
            PhoneQuestion.NEXT_ALARM -> r.nextAlarm()?.let { a -> "Your next alarm is ${whenSaid(a.triggerAtMs, inputs)}." }
                ?: "You have no alarm set in the Clock app."
            PhoneQuestion.DO_NOT_DISTURB -> when (r.doNotDisturb()) {
                DndMode.OFF -> "Do Not Disturb is off."
                DndMode.PRIORITY -> "Do Not Disturb is on: only priority notifications come through."
                DndMode.ALARMS -> "Do Not Disturb is on: only alarms come through."
                DndMode.SILENT -> "Do Not Disturb is on: everything is silenced."
                DndMode.UNKNOWN -> "Do Not Disturb is on."
                null -> "I can't read Do Not Disturb on this phone."
            }
            PhoneQuestion.TIMERS -> timers(inputs.phone.timers(nowMs), inputs)
            PhoneQuestion.PHONE_MODEL -> r.device()?.let { d ->
                "This is ${phoneName(d)} on Android ${d.android}, with ${DeviceTiers.marketedGb(d.totalRamBytes)} GB of memory."
            } ?: "I can't read which phone this is."
            PhoneQuestion.ANDROID_VERSION -> r.device()?.let { d -> "This phone runs Android ${d.android}." }
                ?: "I can't read the Android version."
            PhoneQuestion.WHICH_MODEL -> "I'm Domovoi, running ${inputs.modelName.substringBefore(" (")} on this phone's ${inputs.backend.name}, with no internet."
            PhoneQuestion.CALENDAR_TODAY -> {
                val end = inputs.now.truncatedTo(ChronoUnit.DAYS).plusDays(1).toInstant().toEpochMilli()
                calendar(r.calendar(nowMs, end), inputs, "today", rest = true)
            }
            PhoneQuestion.CALENDAR_TOMORROW -> {
                val start = inputs.now.truncatedTo(ChronoUnit.DAYS).plusDays(1)
                calendar(r.calendar(start.toInstant().toEpochMilli(), start.plusDays(1).toInstant().toEpochMilli()), inputs, "tomorrow", rest = false)
            }
            PhoneQuestion.CALENDAR_NEXT -> {
                val events = r.calendar(nowMs, inputs.now.plusDays(NEXT_EVENT_DAYS).toInstant().toEpochMilli())
                when {
                    events == null -> "I can't read this phone's calendar."
                    events.isEmpty() -> "Nothing on this phone's calendar in the next $NEXT_EVENT_DAYS days." +
                        if (inputs.phone.serverKnown()) " $HOUSEHOLD_CALENDAR" else ""
                    else -> events.first().let { e -> "Your next event is ${e.title}, ${eventWhen(e, inputs)}." }
                }
            }
            PhoneQuestion.WHERE_AM_I -> r.location()?.let { l ->
                val acc = l.accuracyM?.let { " (to about ${it.toInt()} m)" }.orEmpty()
                val age = l.ageMs?.takeIf { it > 2 * 60_000 }?.let { ", ${ago(it)}" }.orEmpty()
                "The phone's last location is ${coord(l.latitude, 'N', 'S')}, ${coord(l.longitude, 'E', 'W')}$acc$age. " +
                    "I can't turn that into an address without the internet."
            } ?: "I can't get this phone's location right now. Location may be off in Android's settings."
            PhoneQuestion.HEADPHONES -> r.headphones()?.let { names ->
                when (names.size) {
                    0 -> "No Bluetooth headphones or speakers are connected."
                    1 -> "${names[0]} is connected."
                    else -> names.dropLast(1).joinToString(", ") + " and ${names.last()} are connected."
                }
            } ?: "I can't read which headphones are connected on this phone."
            PhoneQuestion.WEATHER -> "I can't check the weather from this phone: phone chats don't use the internet." +
                if (inputs.phone.serverKnown()) " Your home server chats can tell you." else ""
        }
    }

    /** A refused permission's one line: what can't be done, how to change
     *  it, and (calendar) where the household's is. Never a new request. */
    fun cannot(p: PhonePermission, serverKnown: Boolean, q: PhoneQuestion? = null): String = when (p) {
        PhonePermission.CALENDAR -> "I can't read this phone's calendar without the calendar permission, which you can turn on " +
            "in Android's settings for Domovoi." + if (serverKnown) " $HOUSEHOLD_CALENDAR" else ""
        PhonePermission.LOCATION -> if (q == PhoneQuestion.WIFI_NAME) {
            "I can't read the Wi-Fi network's name without the location permission, which you can turn on in Android's settings for Domovoi."
        } else {
            "I can't see where the phone is without the location permission, which you can turn on in Android's settings for Domovoi."
        }
        PhonePermission.CONTACTS -> "I can't look people up by name without the contacts permission; I'll open the contact picker instead."
    }

    /** "Playing "Glass Harbor" by Velvet Kites on this phone, 1:12 of 3:58." */
    fun nowPlaying(f: PromptFacts): String {
        val p = f.playing ?: return "Nothing is playing."
        val what = "\"${p.title}\"" + (p.artist?.takeIf { it.isNotBlank() }?.let { " by $it" } ?: "")
        val where = p.room?.let { " in the $it" } ?: " on this phone"
        val at = p.positionSec?.let { pos ->
            ", ${PhoneFacts.clock(pos)}" + (p.durationSec?.takeIf { it > 0 }?.let { " of ${PhoneFacts.clock(it)}" } ?: "")
        }.orEmpty()
        val sleep = f.sleepRemainingSec?.takeIf { it > 0 }?.let { " The sleep timer stops it in ${PhoneFacts.minutes(it)}." }.orEmpty()
        return (if (p.paused) "Paused: " else "Playing ") + what + where + at + "." + sleep
    }

    private fun timers(list: List<PhoneTimer>, inputs: PhoneAnswerInputs): String {
        if (list.isEmpty()) return "This phone has no timers or reminders from your home server. Timers in the Clock app aren't visible to me."
        val nowMs = inputs.now.toInstant().toEpochMilli()
        val shown = list.take(MAX_TIMERS).map { t ->
            when {
                t.masked -> "a private reminder ${whenSaid(t.triggerAtMs, inputs)}"
                t.reminder -> "a reminder" + (t.label?.takeIf { it.isNotBlank() }?.let { ", $it," } ?: "") + " ${whenSaid(t.triggerAtMs, inputs)}"
                else -> (t.label?.takeIf { it.isNotBlank() }?.let { "$it, " } ?: "a timer, ") + left(t.triggerAtMs - nowMs) + " left" +
                    (t.room?.takeIf { it.isNotBlank() }?.let { " (${it})" } ?: "")
            }
        }
        val more = (list.size - MAX_TIMERS).takeIf { it > 0 }?.let { ", and $it more" }.orEmpty()
        val count = if (list.size == 1) "one" else "${list.size}"
        val noun = if (list.all { it.reminder }) "reminder" else if (list.none { it.reminder }) "timer" else "timer or reminder"
        val plural = if (list.size == 1) noun else if (noun == "timer or reminder") "timers and reminders" else noun + "s"
        return "This phone has $count $plural from your home server: " + shown.joinToString("; ") + more + "."
    }

    private fun calendar(events: List<CalendarEvent>?, inputs: PhoneAnswerInputs, day: String, rest: Boolean): String {
        if (events == null) return "I can't read this phone's calendar."
        if (events.isEmpty()) {
            return (if (rest) "Nothing else is on this phone's calendar today." else "Nothing is on this phone's calendar $day.") +
                if (inputs.phone.serverKnown()) " $HOUSEHOLD_CALENDAR" else ""
        }
        val shown = events.take(MAX_EVENTS).joinToString("; ") { e ->
            if (e.allDay) "${e.title} (all day)" else "${e.title} at ${inputs.style.time(inputs.style.at(e.beginMs))}"
        }
        val more = (events.size - MAX_EVENTS).takeIf { it > 0 }?.let { "; and $it more" }.orEmpty()
        val n = events.size
        return "On this phone's calendar $day" + (if (rest) " from now" else "") + ", $n ${if (n == 1) "event" else "events"}: $shown$more."
    }

    private fun eventWhen(e: CalendarEvent, inputs: PhoneAnswerInputs): String =
        if (e.allDay) dayWord(inputs.style.at(e.beginMs), inputs) + " (all day)" else whenSaid(e.beginMs, inputs)

    /** "at 6:30 AM today", "tomorrow at 7:00 AM", "Wednesday at 9:00 AM",
     *  "on Thursday, October 29, 2026 at 9:00 AM". */
    internal fun whenSaid(ms: Long, inputs: PhoneAnswerInputs): String {
        val t = inputs.style.at(ms)
        val time = inputs.style.time(t)
        val days = ChronoUnit.DAYS.between(inputs.now.toLocalDate(), t.toLocalDate())
        return when {
            days == 0L -> "at $time today"
            days == 1L -> "tomorrow at $time"
            days in 2..6 -> "${inputs.style.weekday(t)} at $time"
            else -> "on ${inputs.style.date(t)} at $time"
        }
    }

    private fun dayWord(t: ZonedDateTime, inputs: PhoneAnswerInputs): String {
        val days = ChronoUnit.DAYS.between(inputs.now.toLocalDate(), t.toLocalDate())
        return when (days) {
            0L -> "today"
            1L -> "tomorrow"
            in 2..6 -> inputs.style.weekday(t)
            else -> "on ${inputs.style.date(t)}"
        }
    }

    /** "4 minutes", "1 hour 5 minutes", "30 seconds". */
    internal fun left(ms: Long): String {
        val s = (ms / 1000).coerceAtLeast(0)
        return when {
            s < 60 -> "$s ${if (s == 1L) "second" else "seconds"}"
            s < 3600 -> ((s + 30) / 60).let { "$it ${if (it == 1L) "minute" else "minutes"}" }
            else -> {
                val h = s / 3600
                val m = (s % 3600 + 30) / 60
                "$h ${if (h == 1L) "hour" else "hours"}" + if (m > 0) " $m ${if (m == 1L) "minute" else "minutes"}" else ""
            }
        }
    }

    private fun ago(ms: Long): String {
        val m = ms / 60_000
        return when {
            m < 60 -> "$m minutes ago"
            m < 48 * 60 -> "${m / 60} hours ago"
            else -> "${m / (24 * 60)} days ago"
        }
    }

    private fun coord(v: Double, pos: Char, neg: Char): String =
        String.format(Locale.US, "%.4f° %c", kotlin.math.abs(v), if (v >= 0) pos else neg)

    /** "a Google Pixel 7", "a Samsung SM-F946B". */
    internal fun phoneName(d: DeviceReading): String {
        val maker = d.manufacturer.trim().replaceFirstChar { it.titlecase(Locale.ROOT) }
        val model = d.model.trim()
        val name = if (model.startsWith(maker, ignoreCase = true)) model else "$maker $model"
        return "a $name"
    }

    /** "23.4 GB", "512 MB". */
    internal fun gb(bytes: Long): String {
        val g = bytes / 1_000_000_000.0
        return if (g >= 1) String.format(Locale.US, if (g >= 100) "%.0f GB" else "%.1f GB", g)
        else String.format(Locale.US, "%.0f MB", bytes / 1_000_000.0)
    }

    /** The one honest line about the other place (owner, 2026-10-21):
     *  said, never fetched. */
    const val HOUSEHOLD_CALENDAR = "The household calendar is in your home server chats."

    private const val MAX_TIMERS = 5
    private const val MAX_EVENTS = 5
    private const val NEXT_EVENT_DAYS = 7L
}
