package com.domovoi.app.assistant

import kotlinx.serialization.Serializable
import java.time.ZonedDateTime

// N2, actions (plan part 2). On Gemma 4 E2B every action comes from the
// phone's own patterns here, never from a model tool call: on the owner's
// Fold (N0, 2026-10-21) the model made false actions on 28.6% of
// near-misses and 11.5% of chit-chat, the patterns on none. The patterns are
// narrow and anchored, ported from the server's fast paths and the N0 bench
// (domovoi/handlers/timer.py, music.py, shared/number_words.py,
// shared/clock_time.py; assistant/n0/N0Actions.kt), widened for the bench's
// misses ("go back", "shuffle my music", the sleep-timer phrasings,
// "quarter to seven", "a bit before seven").

/** What an action does. */
@Serializable
enum class ActionKind {
    /** A countdown in the Clock app (AlarmClock.ACTION_SET_TIMER): asked first. */
    SET_TIMER,

    /** An alarm in the Clock app (ACTION_SET_ALARM): asked first. */
    SET_ALARM,

    /** The Clock app's timers or alarms on screen: no question. */
    SHOW_TIMERS,
    SHOW_ALARMS,

    /** Music saved on the phone, at once, with Undo. */
    PLAY_MUSIC,

    /** The phone's player, at once, with Undo. */
    PAUSE,
    RESUME,
    NEXT,
    PREVIOUS,
    STOP,

    /** Playback stops in [PhoneAction.minutes] (the player's sleep timer,
     *  never a Clock timer). */
    SLEEP,
    CANCEL_SLEEP,

    /** A book saved on the phone, at its saved place, with Undo. */
    PLAY_BOOK,

    /** A video saved on the phone: asked first. */
    PLAY_VIDEO,

    /** A kind this version doesn't know (a newer app wrote it): never run. */
    OTHER,
    ;

    /** Asked first (a card, or "yes" in a voice turn): the Clock app's
     *  timers and alarms can't be taken back cleanly, and a video takes the
     *  screen. */
    val asksFirst: Boolean get() = this == SET_TIMER || this == SET_ALARM || this == PLAY_VIDEO

    /** Done at once with Undo on screen: the player's actions. */
    val undoable: Boolean
        get() = this == PLAY_MUSIC || this == PAUSE || this == RESUME || this == NEXT || this == PREVIOUS ||
            this == STOP || this == SLEEP || this == CANCEL_SLEEP || this == PLAY_BOOK
}

/**
 * One action, as the patterns (or, behind [CatalogModel.actionTools], a
 * model) read it. Kept with a card in the conversation, so every field has
 * a default.
 */
@Serializable
data class PhoneAction(
    val kind: ActionKind = ActionKind.OTHER,
    /** SET_TIMER: the length. */
    val seconds: Int? = null,
    /** SET_TIMER: its name ("pasta"). */
    val label: String? = null,
    /** PLAY_MUSIC: the words as said after the verb ("some nights"), for
     *  the library check beside [query] ("nights"). */
    val phrase: String? = null,
    /** SET_ALARM: 0-23 and 0-59. */
    val hour: Int? = null,
    val minute: Int? = null,
    /** SET_ALARM: repeat days, java.util.Calendar's (SUNDAY = 1 … SATURDAY = 7). */
    val days: List<Int> = emptyList(),
    /** SET_ALARM: "tomorrow" was said. */
    val tomorrow: Boolean = false,
    /** SET_ALARM: the time is the phone's guess at a vague one ("a bit
     *  before seven"): the card asks about it. */
    val approximate: Boolean = false,
    /** SLEEP: playback stops after this many minutes. */
    val minutes: Int? = null,
    /** PLAY_MUSIC / PLAY_BOOK / PLAY_VIDEO: what to look for (empty: all, or
     *  the book in progress). */
    val query: String? = null,
    /** PLAY_MUSIC: shuffled. */
    val shuffle: Boolean = false,
)

/** What a sentence asks of the phone's actions. */
sealed interface ActionAsk {
    data class Do(val action: PhoneAction) : ActionAsk

    /** Something for the house's rooms or the home server ("play jazz in the
     *  kitchen", "set a timer on the server"): not this round. */
    data object House : ActionAsk

    /** "undo", "take that back". */
    data object Undo : ActionAsk
}

object ActionPatterns {
    private const val MAX_CHARS = 140

    /** The text a pattern reads: lower case, plain apostrophes, commas as
     *  spaces, closing punctuation gone, polite openings and endings off. */
    internal fun norm(text: String): String {
        var t = text.lowercase().replace(Regex("[’‘`´]"), "'").replace(Regex("[,;!?]"), " ")
            .replace(Regex("\\s+"), " ").trim().trimEnd('.', ' ')
        // "o'clock" keeps its apostrophe; other dots ("a.m.") go.
        t = t.replace(Regex("\\b([ap])\\.m\\b\\.?"), "$1m")
        var before: String
        do {
            before = t
            t = t.replace(Regex("^(?:hey|hi|ok|okay|so|um|uh|well|please|domovoi|domovoy|domo|hold on|wait a sec(?:ond)?|actually|oh)\\s+"), "")
            t = t.replace(Regex("^(?:can|could|would|will) you(?: please)?(?: just)?\\s+"), "")
            t = t.replace(Regex("^(?:go ahead and|just)\\s+"), "")
            t = t.replace(Regex("\\s+(?:please|for me|thanks|thank you|now|right now|domovoi)$"), "")
        } while (t != before)
        return t.trim()
    }

    // ── The patterns ────────────────────────────────────────────────────

    private const val LABEL = "(?<label>[a-z][a-z' -]{0,40})"
    private const val A = "(?:a |an |the |me a |me an |my )"
    private const val TIMER_VERB = "(?:set|start|make|create|put on|i need|i want|give me|get me|i'd like)"

    private val timerFor = Regex("^(?:$TIMER_VERB $A?)?timer (?:for |of )?(?<dur>$DURATION)(?: (?:for|called|named|labelled|labeled) $LABEL)?$")
    private val durationTimer = Regex("^$TIMER_VERB $A?(?<dur>$DURATION) (?:(?<word>[a-z]+) )?timer(?: (?:for|called|named) $LABEL)?$")
    private val namedTimer = Regex("^$TIMER_VERB $A?(?<label>[a-z]+(?: [a-z]+)?) timer (?:for |of )?(?<dur>$DURATION)$")
    private val countDown = Regex("^count ?down (?:for |from )?(?<dur>$DURATION)$")
    private val timerLabelled = Regex("^$TIMER_VERB $A?timer (?:called|named|for) $LABEL (?:for |of )(?<dur>$DURATION)$")

    private const val DAYPARTS = "(?:in the morning|in the afternoon|in the evening|at night|tonight|tomorrow morning|this morning|" +
        "this evening|tomorrow evening|tomorrow night|tomorrow afternoon|tomorrow)"
    private const val WAKE = "(?:wake me(?: up)?|get me up|i need to (?:get|be|wake) up|i have to (?:get|be|wake) up|i want to (?:get|wake) up|i need to be awake)"
    private const val ALARM = "(?:set (?:an |my |the |a )?alarm|alarm|make (?:an |me an )?alarm|create (?:an )?alarm|i need an alarm|put (?:an |my )?alarm)"
    private const val REPEAT = "(?<days> (?:every ?day|every (?:week ?day|weekday|weekend)|on weekdays|on weekends|every (?:mon|tues|wednes|thurs|fri|satur|sun)day(?:s)?" +
        "(?:(?: and|,)? (?:(?:mon|tues|wednes|thurs|fri|satur|sun)day)s?)*|on (?:(?:mon|tues|wednes|thurs|fri|satur|sun)day)s(?:(?: and|,)? (?:(?:mon|tues|wednes|thurs|fri|satur|sun)day)s?)*))"
    private const val VAGUE = "(?<vague>(?:a (?:little )?bit|a little|just|shortly|slightly|a few minutes|a couple of minutes) (?<rel>before|after)|(?<about>around|about|roughly|sometime around))"

    private val alarm = Regex("^(?<wake>$WAKE|$ALARM)(?: (?:for|at|by|on))?(?: (?<tom1>tomorrow)(?: (?:at|for|by))?)? (?:$VAGUE )?(?<time>$TIME)$REPEAT?$")

    // Durations said as alarms or reminders (Fold check 2026-10-21): the
    // Clock app has no reminders, and "an alarm for five minutes from now"
    // is a countdown, so both are a Clock timer for that long, named for
    // what to do when it was said. A reminder at a clock time is an alarm.
    // norm() has already taken a closing "now" off ("five minutes from").
    private const val LATER = "(?: from now| from right now| from right| from| later)?"
    private const val TODO = "(?<label>[a-z0-9][a-z0-9' -]{0,60})"
    private const val REMIND = "(?:remind me|set (?:a |me a )?reminder|make (?:a |me a )?reminder|give me a reminder|i need a reminder)"
    private val alarmIn = Regex("^(?:$ALARM|$WAKE) (?:in|for|after) (?<dur>$DURATION)$LATER(?: (?:to|so i can|for) $TODO)?$")
    private val alarmFromNow = Regex("^(?:$ALARM|$WAKE)(?: for)? (?<dur>$DURATION) from(?: right)?(?: now)?(?: (?:to|so i can|for) $TODO)?$")
    private val remindIn = Regex("^$REMIND (?:in|after|for) (?<dur>$DURATION)$LATER(?: (?:to|that|about|of) $TODO)?$")
    private val remindToIn = Regex("^$REMIND (?:to|about|of) $TODO (?:in|after) (?<dur>$DURATION)$LATER$")
    private val inRemind = Regex("^in (?<dur>$DURATION) $REMIND (?:to|about|of) $TODO$")
    private val remindAt = Regex("^$REMIND (?:at|for|by) (?<time>$TIME)(?: (?:to|that|about|of) $TODO)?$")
    private val remindToAt = Regex("^$REMIND (?:to|about|of) $TODO (?:at|by) (?<time>$TIME)$")

    private val pause = Regex("^(?:pause|pause (?:the |my |this )?(?:music|song|track|audio|playback|book|audiobook|podcast|it|this|player)|pause it for a (?:sec|second|moment|minute|bit)|pause for a (?:sec|second|moment|bit)|hold the music|pause playback)$")
    private val resume = Regex("^(?:resume|resume (?:the |my )?(?:music|playback|playing|song|it)|continue|continue (?:the |my )?(?:music|playing|playback|song)|continue playing|unpause|un pause|keep (?:playing|going)|keep the music going|start (?:the music|playing) again|play again|carry on|press play|play|play it)$")
    private val stop = Regex("^(?:stop|stop (?:the |my |this )?(?:music|playback|playing|audio|song|it|player|book|audiobook|podcast)|stop playing (?:music|the music)|turn (?:off the music|the music off|it off)|shut (?:it|the music) off|stop all (?:the )?music|end playback|kill the music)$")
    private val next = Regex("^(?:next|skip|next (?:song|track|one|chapter|please)|skip (?:this|it|this one|this song|this track|ahead|forward|the song|the track|song|track|to the next(?: (?:song|track|one|chapter))?)|go to the next (?:song|track|one|chapter)|play the next (?:song|track|one)|skip to next (?:song|track)|next song please)$")
    private val previous = Regex("^(?:previous|back|go back|go back one(?: (?:song|track))?|go back a (?:song|track)|back one (?:song|track)|previous (?:song|track|one|chapter)|last (?:song|track)|play the (?:previous|last) (?:song|track|one)(?: again)?|go to the previous (?:song|track|one|chapter)|the song before|play that (?:song|again) again|replay (?:that|the last) song|skip back)$")

    private const val PLAYBACK = "(?:the music|the song|the audio|playback|playing|playing music|music|it|this|the book|the audiobook|the podcast|the player)"
    private val sleep = Regex(
        "^(?:(?:set |start |put on |turn on )?(?:a |the )?sleep timer (?:for |of |to )?(?<d1>$DURATION)|" +
            "(?:stop|pause|end|turn off|switch off) $PLAYBACK (?:in|after|in about) (?<d2>$DURATION)|" +
            "(?:turn|switch) $PLAYBACK off (?:in|after) (?<d3>$DURATION)|" +
            "(?:stop|pause) (?:in|after) (?<d4>$DURATION)|" +
            "(?:play|keep playing) (?:music |it )?for (?:another )?(?<d5>$DURATION)(?: then stop| and then stop| and stop)|" +
            "sleep (?:in|after) (?<d6>$DURATION))$",
    )
    private val cancelSleep = Regex("^(?:cancel|turn off|stop|clear|remove|delete) (?:the |my )?sleep timer$")

    private val showTimers = Regex("^(?:show|open|show me|see|let me see|bring up)(?: me)? (?:my |the |all my |all the )?timers?(?: in the clock(?: app)?)?$")
    private val showAlarms = Regex("^(?:(?:show|open|show me|see|let me see|bring up)(?: me)? (?:my |the |all my |all the )?alarms?(?: in the clock(?: app)?)?|open the clock(?: app)?|open clock)$")

    private const val BOOK = "(?:audio ?book|book)"
    private val bookMine = Regex("^(?:continue|resume|play|keep reading|keep playing|keep listening to|go back to|open|start|read|pick up|carry on with|put on|listen to|back to) (?:my|the) $BOOK(?: (?:where i left off|from where i (?:stopped|left off)))?$")
    private val bookTitled = Regex("^(?:play|put on|start|open|listen to|read) (?:the |my )?(?<title>.+?) $BOOK$")
    private val bookContinue = Regex("^(?:continue|resume|keep) (?:listening to|reading|playing) (?:the |my )?(?<title>.+?)(?: $BOOK)?$")
    private val bookWhere = Regex("^(?:pick up|continue) (?:my |the )?$BOOK where i (?:left off|stopped)$")

    private val video = Regex("^(?:play|watch|show me|open|put on|i want to watch|let me watch|can i watch) (?:the |my |a |that )?(?:video (?:called |named |of |about |from )?(?<q1>.+)|(?<q2>.+?) video)$")

    /** After "play": a game, a sport, a sentence; not music. */
    private val notMusic = Regex(
        "^(?:is|was|are|were|has|had|can|could|will|would|should|a game|games?|with|along|fair|dead|nice|dumb|hard|it (?:safe|cool|by ear)|" +
            "catch|chess|cards|hide and seek|tag|rock paper scissors|twenty questions|20 questions|trivia|a trick|tricks|pretend|" +
            "the role|a role|an instrument|the (?:piano|guitar|drums|violin|lottery|field)|video games|sports|football|soccer|" +
            "basketball|baseball|tennis|golf|time|date|devil's advocate|a part|out|in the|outside|the victim|favou?rites|" +
            "me|us|him|her|them|you|yourself|reason|advice|this$|that$|a coat|a jacket|a hat|weight|some weight|" +
            "makeup|clothes|shoes|socks|the kettle|the coffee|coffee|a pot|dinner|sunscreen|a brave face|a show)\\b",
    )
    /** "play a song on the guitar": the person plays it, not the phone. */
    private val instrument = Regex("\\bon (?:the|a|your|my) (?:guitar|piano|ukulele|violin|drums|harmonica|flute|recorder|kazoo|keyboard|harp|cello)\\b")
    private val play = Regex("^(?:play|put on|shuffle|listen to|i want to (?:hear|listen to)|i'd like to hear|let me hear|can i hear|let's hear|queue up) (?<what>.+)$")
    private val startMix = Regex("^start (?<what>(?:my |the |some )?.*(?:mix|playlist|music|songs|radio|album))$")
    private val shuffleAll = Regex("^(?:shuffle|shuffle (?:all|everything|it all|all my music|all my songs|all songs)|shuffle play|play (?:something|anything) (?:random|at random)|play random (?:music|songs))$")
    private val musicOnly = Regex("^(?:music|my music|some music|songs|some songs|my songs|anything|something|a song|some tunes|tunes|all my music|all music|all my songs|everything)$")

    // House actions (not this round): an action said for a room, the whole
    // house or the home server.
    private const val HOUSE_VERB = "(?:play|put on|pause|stop|resume|skip|start|cast|send|move|turn (?:up|down|on|off)|set (?:a |an )?(?:timer|alarm)|set (?:a |an )?\\S+ (?:timer|alarm)|announce|shuffle|listen to)"
    private val houseRoom = Regex("^$HOUSE_VERB\\b.*? (?:in|on|to|into|through) (?:the |my |our )?(?<room>[a-z']+(?: [a-z']+){0,3}?)(?: room| speakers?| satellite)?(?: for .+)?$")
    private val houseAll = Regex("^$HOUSE_VERB\\b.*\\b(?:everywhere|in every room|in all (?:the )?rooms|on all (?:the )?speakers|through(?:out)? the house|in the whole house|all over the house|around the house)\\b.*$")
    private val houseServer = Regex("^(?:set (?:a |an )?(?:server|house|household|home) (?:timer|alarm|reminder)(?: .*)?|set (?:a |an )?(?:timer|alarm|reminder) on the (?:home )?server|(?:cast|send|move) (?:this|it|the music|the song|that) (?:to|into) .+|announce .+)$")

    /** "cancel the kitchen timer": a house timer, never the phone's. */
    private val houseTimer = Regex("^(?:cancel|stop|delete|remove|pause|turn off|check|end|dismiss|how long is left on|how much time is left on|what's left on|whats left on) (?:the |my )?(?<room>[a-z]+(?: [a-z]+)?) (?:timer|timers|alarm|reminder)s?$")

    /** Rooms people name. Never the household's own room names: a phone
     *  chat doesn't read the server's (owner, 2026-10-21). */
    private val ROOM_WORDS = setOf(
        "kitchen", "living room", "living", "lounge", "bedroom", "master bedroom", "kids room", "kid's room", "nursery",
        "office", "study", "garage", "basement", "attic", "bathroom", "den", "dining room", "dining", "hallway", "hall",
        "patio", "porch", "garden", "backyard", "yard", "playroom", "family room", "guest room", "laundry room", "gym",
    )

    private val undo = Regex("^(?:undo|undo (?:that|it|this|the last (?:one|thing))|take (?:that|it) back|put (?:that|it) back|revert (?:that|it)|go back to what i was (?:playing|listening to)|put (?:the music|my music) back)$")

    /** Transport words that are also chat words ("continue" after a story,
     *  "next one" after a joke): the player's only in a playback context. */
    private val BARE = setOf(
        "continue", "go on", "keep going", "carry on", "next", "next one", "next please", "skip", "skip it",
        "back", "go back", "play", "play it", "play again", "stop it",
    )

    /**
     * What [text] asks of the phone's actions, or null (a question for the
     * phone's other fast paths or the model). [now]: for an alarm said with
     * no am or pm. [playback]: the chat is about playback (the turn before
     * was the player's, or music is playing); without it a bare
     * "continue" or "next one" is left to the model.
     */
    fun match(text: String, now: ZonedDateTime, playback: Boolean = true): ActionAsk? {
        if (text.length > MAX_CHARS) return null
        val t = norm(text)
        if (t.isEmpty()) return null
        if (!playback && t in BARE) return null
        if (undo.matches(t)) return ActionAsk.Undo
        if (isHouse(t)) return ActionAsk.House
        return action(t, now)?.let { a -> ActionAsk.Do(a.copy(label = a.label?.let { asSaid(it, text) })) }
    }

    /** [label] (lower case, from the normalized text) as [text] spelled it:
     *  "check Dome of War", not "check dome of war". */
    internal fun asSaid(label: String, text: String): String {
        val i = text.lowercase().replace(Regex("[’‘`´]"), "'").indexOf(label)
        return if (i >= 0) text.substring(i, i + label.length) else label
    }

    /** A reminder's or an alarm's "to …", as a Clock label. */
    private fun todo(m: MatchResult): String? = runCatching { m.groups["label"]?.value }.getOrNull()?.trim()
        ?.removePrefix("to ")?.trim()?.takeIf { it.isNotBlank() && it !in NOT_LABELS }

    private fun reminder(t: String, now: ZonedDateTime): PhoneAction? {
        for (re in listOf(alarmIn, alarmFromNow, remindIn, remindToIn, inRemind)) {
            val m = re.matchEntire(t) ?: continue
            val secs = parseDurationSeconds(m.groups["dur"]!!.value) ?: return null
            if (secs <= 0) return null
            return PhoneAction(ActionKind.SET_TIMER, seconds = secs, label = todo(m))
        }
        for (re in listOf(remindAt, remindToAt)) {
            val m = re.matchEntire(t) ?: continue
            val raw = m.groups["time"]!!.value
            val spoken = parseTime(raw) ?: return null
            val tomorrow = raw.contains("tomorrow")
            val (hour, minute) = resolveHour(spoken, tomorrow, now)
            return PhoneAction(ActionKind.SET_ALARM, hour = hour, minute = minute, tomorrow = tomorrow, label = todo(m))
        }
        return null
    }

    private fun isHouse(t: String): Boolean {
        if (houseServer.matches(t) || houseAll.matches(t)) return true
        houseTimer.matchEntire(t)?.let { m ->
            val w = m.groups["room"]!!.value
            if (w in ROOM_WORDS || w.removeSuffix(" room") in ROOM_WORDS) return true
        }
        val m = houseRoom.matchEntire(t) ?: return false
        val room = m.groups["room"]!!.value
        if (parseDurationSeconds(room) != null || room in NOT_ROOMS) return false
        return room in ROOM_WORDS || room.removeSuffix(" room") in ROOM_WORDS
    }

    private val NOT_ROOMS = setOf("morning", "afternoon", "evening", "night", "background", "car")

    private fun action(t: String, now: ZonedDateTime): PhoneAction? {
        // Before the timers: "set a sleep timer for 20 minutes" and "stop the
        // music in an hour" are playback's, never the Clock app's.
        sleep.matchEntire(t)?.let { m ->
            val d = (1..6).firstNotNullOf { m.groups["d$it"]?.value }
            val secs = parseDurationSeconds(d) ?: return null
            val min = ((secs + 59) / 60).coerceAtLeast(1)
            return PhoneAction(ActionKind.SLEEP, minutes = min)
        }
        if (cancelSleep.matches(t)) return PhoneAction(ActionKind.CANCEL_SLEEP)
        reminder(t, now)?.let { return it }
        for (re in listOf(timerFor, durationTimer, namedTimer, countDown, timerLabelled)) {
            val m = re.matchEntire(t) ?: continue
            val secs = parseDurationSeconds(m.groups["dur"]!!.value) ?: return null
            if (secs <= 0) return null
            val label = (runCatching { m.groups["label"]?.value }.getOrNull() ?: runCatching { m.groups["word"]?.value }.getOrNull())?.trim()
                ?.removePrefix("the ")?.removePrefix("my ")?.takeIf { it.isNotBlank() && it !in NOT_LABELS }
            return PhoneAction(ActionKind.SET_TIMER, seconds = secs, label = label)
        }
        alarm.matchEntire(t)?.let { m -> return alarmAction(m, now) }
        if (showTimers.matches(t)) return PhoneAction(ActionKind.SHOW_TIMERS)
        if (showAlarms.matches(t)) return PhoneAction(ActionKind.SHOW_ALARMS)
        if (pause.matches(t)) return PhoneAction(ActionKind.PAUSE)
        if (stop.matches(t)) return PhoneAction(ActionKind.STOP)
        if (next.matches(t)) return PhoneAction(ActionKind.NEXT)
        // Books before "go back" and "continue": "go back to my book".
        if (bookMine.matches(t) || bookWhere.matches(t)) return PhoneAction(ActionKind.PLAY_BOOK)
        if (previous.matches(t)) return PhoneAction(ActionKind.PREVIOUS)
        if (resume.matches(t)) return PhoneAction(ActionKind.RESUME)
        bookTitled.matchEntire(t)?.let { return PhoneAction(ActionKind.PLAY_BOOK, query = it.groups["title"]!!.value.trim()) }
        bookContinue.matchEntire(t)?.let { m ->
            val title = m.groups["title"]!!.value.trim()
            // "keep playing along": not a book.
            if (notMusic.containsMatchIn(title)) return null
            // "continue playing the music": the player's, not a book.
            if (musicOnly.matches(title) || title.startsWith("the music") || title == "it") return PhoneAction(ActionKind.RESUME)
            return PhoneAction(ActionKind.PLAY_BOOK, query = title)
        }
        video.matchEntire(t)?.let { m ->
            val q = (m.groups["q1"] ?: m.groups["q2"])!!.value.trim().removePrefix("the ")
            // "play that video again": a video, but no name for it.
            if (!realQuery(q)) return null
            if (q.isNotEmpty() && !notMusic.containsMatchIn(q)) return PhoneAction(ActionKind.PLAY_VIDEO, query = q)
        }
        if (shuffleAll.matches(t)) return PhoneAction(ActionKind.PLAY_MUSIC, query = "", shuffle = true)
        (play.matchEntire(t) ?: startMix.matchEntire(t))?.let { m ->
            val what = m.groups["what"]!!.value.trim()
            if (notMusic.containsMatchIn(what) || instrument.containsMatchIn(what)) return null
            val shuffle = t.startsWith("shuffle")
            val q = musicQuery(what)
            // "let's hear it": nothing to look for. Anything else plays only
            // when it is in the phone's library (MusicEvidence, checked by
            // the controller): "put on my shoes" then goes to the model.
            if (q.isNotEmpty() && !realQuery(q)) return null
            return PhoneAction(ActionKind.PLAY_MUSIC, query = q, shuffle = shuffle, phrase = what)
        }
        return null
    }

    private val NOT_LABELS = setOf("me", "a", "an", "it", "this")

    /** Words that are never a search by themselves ("it", "that", "the"). */
    private val PRONOUNS = setOf("it", "that", "this", "these", "those", "them", "the", "a", "an", "again")

    /** A query worth searching the phone for: more than pronouns ("let's
     *  hear it", "can I watch a video"). Whether it is music is the
     *  library's to say ([MusicEvidence]), not a word list. */
    internal fun realQuery(q: String): Boolean {
        val words = q.lowercase().split(Regex("[^\\p{L}\\p{N}']+")).filter { it.isNotEmpty() }
        return words.isNotEmpty() && !words.all { it in PRONOUNS }
    }

    /** "some relaxing music" → "relaxing", "my running playlist" →
     *  "running", "something by Miles Davis" → "miles davis", "my music" →
     *  "" (everything). */
    internal fun musicQuery(what: String): String {
        var q = what.trim()
        if (musicOnly.matches(q)) return ""
        var before: String
        do {
            before = q
            q = q.replace(Regex("^(?:some|my|the|a|an|all|all my|all the|me|us)\\s+"), "")
            q = q.replace(Regex("^(?:songs|music|tracks|stuff|something|anything|a song|a track|the album|album|the song|song|the track|track|the artist|artist|the band|band|the playlist|playlist)\\s+(?:by|from|called|named|of)?\\s*"), "")
            q = q.replace(Regex("^(?:by|from)\\s+"), "")
            q = q.replace(Regex("\\s+(?:music|songs|tracks|playlist|mix|album|tunes|on (?:my|this|the) phone|from my phone|please|for me)$"), "")
        } while (q != before)
        return if (musicOnly.matches(q)) "" else q.trim()
    }

    private fun alarmAction(m: MatchResult, now: ZonedDateTime): PhoneAction? {
        val raw = m.groups["time"]!!.value
        val tomorrow = m.groups["tom1"] != null || raw.contains("tomorrow")
        val spoken = parseTime(raw) ?: return null
        val wake = m.groups["wake"]!!.value.let { !it.contains("alarm") }
        var (hour, minute) = resolveHour(spoken, wake || tomorrow, now)
        val vague = m.groups["vague"] != null
        if (vague) {
            val shift = when (m.groups["rel"]?.value) {
                "before" -> -VAGUE_MINUTES
                "after" -> VAGUE_MINUTES
                else -> 0
            }
            val total = ((hour * 60 + minute + shift) % (24 * 60) + 24 * 60) % (24 * 60)
            hour = total / 60
            minute = total % 60
        }
        val days = m.groups["days"]?.value?.let { repeatDays(it) }.orEmpty()
        return PhoneAction(ActionKind.SET_ALARM, hour = hour, minute = minute, days = days, tomorrow = tomorrow, approximate = vague)
    }

    /** "a bit before seven" is a quarter of an hour before it. */
    const val VAGUE_MINUTES = 15

    /** The hour on a 24-hour clock: as said with am or pm; a wake-up (or
     *  "tomorrow") with neither is the morning; otherwise the next of the
     *  two to come. */
    internal fun resolveHour(s: SpokenTime, morning: Boolean, now: ZonedDateTime): Pair<Int, Int> {
        if (s.pm != null || s.hour > 12 || s.hour == 0) return s.hour to s.minute
        // "12" alone is noon.
        if (morning || s.hour == 12) return s.hour to s.minute
        val am = s.hour
        val pm = s.hour + 12
        val nowMin = now.hour * 60 + now.minute
        // The first of the two still to come today; past both, the morning.
        return when {
            am * 60 + s.minute > nowMin -> am to s.minute
            pm * 60 + s.minute > nowMin -> pm to s.minute
            else -> am to s.minute
        }
    }

    /** "every weekday" → Mon-Fri, "on mondays and wednesdays" → 2, 4. */
    internal fun repeatDays(said: String): List<Int> {
        val s = said.trim()
        return when {
            Regex("every ?day").containsMatchIn(s) -> (1..7).toList()
            Regex("week ?day|weekdays").containsMatchIn(s) -> listOf(2, 3, 4, 5, 6)
            Regex("weekend").containsMatchIn(s) -> listOf(1, 7)
            else -> DAY_NAMES.entries.filter { (name, _) -> Regex("\\b${name}days?\\b").containsMatchIn(s) }.map { it.value }.sorted()
        }
    }

    private val DAY_NAMES = linkedMapOf("sun" to 1, "mon" to 2, "tues" to 3, "wednes" to 4, "thurs" to 5, "fri" to 6, "satur" to 7)
}

/**
 * Whether the phone's library holds what a "play" asked for: the query, as
 * a phrase, equal to a title, artist, album or genre, contained in one as
 * most of its words, or containing one as most of its own ("play some
 * nights" finds "Some Nights", "play the beatles" finds "The Beatles"; "put
 * on a play" never finds "Play That Funky Music"). Words, not letters:
 * "kid" is not in "kids". A leading "the" is ignored on both sides.
 */
object MusicEvidence {
    private const val MIN_CHARS = 3

    internal fun words(s: String): List<String> = s.lowercase().replace(Regex("[’‘`´]"), "'")
        .split(Regex("[^\\p{L}\\p{N}']+")).filter { it.isNotEmpty() }
        .let { if (it.size > 1 && it.first() == "the") it.drop(1) else it }

    private fun within(hay: List<String>, needle: List<String>): Boolean =
        needle.isNotEmpty() && hay.size >= needle.size && (0..hay.size - needle.size).any { i -> hay.subList(i, i + needle.size) == needle }

    /** [said] and [field] as phrases (see the object's note). */
    fun phrase(said: String?, field: String?): Boolean {
        val a = words(said ?: return false)
        val b = words(field ?: return false)
        if (a.joinToString(" ").length < MIN_CHARS || b.joinToString(" ").length < MIN_CHARS) return false
        if (a == b) return true
        if (within(b, a) && a.size * 2 > b.size) return true
        return within(a, b) && b.size * 2 > a.size
    }

    /** What [a] asked for, as the phrases to check: the query and the words
     *  as said, and "X by Y" as a title by an artist. */
    fun asked(a: PhoneAction): List<String> = listOfNotNull(a.query, a.phrase).map { it.trim() }.filter { it.isNotEmpty() }.distinct()

    /** [item] is what [a] asked for. */
    fun matches(a: PhoneAction, item: com.domovoi.app.player.PlayItem): Boolean = asked(a).any { q ->
        phrase(q, item.title) || phrase(q, item.artist) || phrase(q, item.album) ||
            Regex("^(.+?) by (.+)$").matchEntire(q.lowercase())?.destructured?.let { (t, by) ->
                phrase(t, item.title) && phrase(by, item.artist)
            } == true
    }

    /** The genre named [genre] is what [a] asked for. */
    fun genre(a: PhoneAction, genre: String): Boolean = asked(a).any { phrase(it, genre) }
}

// ─── Numbers and durations (port of domovoi/handlers/shared/number_words.py) ─

private val ONES = mapOf("one" to 1, "two" to 2, "three" to 3, "four" to 4, "five" to 5,
    "six" to 6, "seven" to 7, "eight" to 8, "nine" to 9)
private val TEENS = mapOf("ten" to 10, "eleven" to 11, "twelve" to 12, "thirteen" to 13, "fourteen" to 14,
    "fifteen" to 15, "sixteen" to 16, "seventeen" to 17, "eighteen" to 18, "nineteen" to 19)
private val TENS = mapOf("twenty" to 20, "thirty" to 30, "forty" to 40, "fifty" to 50,
    "sixty" to 60, "seventy" to 70, "eighty" to 80, "ninety" to 90)

private val NUMBER_WORD = "(?:(?:${TENS.keys.joinToString("|")})(?:[ -](?:${ONES.keys.joinToString("|")}))?" +
    "|${TEENS.keys.joinToString("|")}|${ONES.keys.joinToString("|")}|a couple of|a couple)"
private val NUMBER = "(?:\\d+|$NUMBER_WORD)"

/** "5", "five", "forty five", "forty-five", "a couple" → the number. */
internal fun parseNumber(text: String): Int? {
    val t = text.trim().lowercase()
    t.toIntOrNull()?.let { return it }
    if (t == "a couple" || t == "a couple of") return 2
    val parts = t.split(' ', '-')
    if (parts.size == 1) return ONES[t] ?: TEENS[t] ?: TENS[t]
    if (parts.size == 2) {
        val tens = TENS[parts[0]] ?: return null
        val ones = ONES[parts[1]] ?: return null
        return tens + ones
    }
    return null
}

private val UNIT_SECONDS = mapOf("second" to 1, "sec" to 1, "minute" to 60, "min" to 60, "hour" to 3600, "hr" to 3600)
private const val UNIT = "(?:second|sec|minute|min|hour|hr)"

/** One duration clause: "5 minutes", "ten-minute", "an hour and a half",
 *  "half an hour", "one and a half hours", "a quarter of an hour". */
private val CLAUSE = "(?:$NUMBER(?: and a half)?[ -]${UNIT}s?(?: and a half)?" +
    "|an? ${UNIT}(?: and a half)?|(?:a )?half (?:an? )?$UNIT|(?:a )?quarter (?:of )?an hour|three quarters of an hour)"

/** A spoken duration: one clause, or two joined ("1 hour and 30 minutes"). */
internal val DURATION = "(?:$CLAUSE(?:(?:,? and |,? )$CLAUSE)?)"

private val CLAUSE_RE = Regex(
    "^(?:(?<num>$NUMBER)(?<halfmid> and a half)?[ -](?<unit>$UNIT)s?(?<halfend> and a half)?" +
        "|an? (?<aunit>$UNIT)(?<ahalf> and a half)?|(?:a )?half (?:an? )?(?<hunit>$UNIT)|(?<quarter>(?:a )?quarter (?:of )?an hour)" +
        "|(?<threeq>three quarters of an hour))$",
)

private fun clauseSeconds(text: String): Int? {
    val m = CLAUSE_RE.matchEntire(text.trim()) ?: return null
    val g = m.groups
    if (g["quarter"] != null) return 900
    if (g["threeq"] != null) return 2700
    g["hunit"]?.let { return UNIT_SECONDS.getValue(it.value) / 2 }
    g["aunit"]?.let { val u = UNIT_SECONDS.getValue(it.value); return u + if (g["ahalf"] != null) u / 2 else 0 }
    val n = parseNumber(g["num"]!!.value) ?: return null
    val u = UNIT_SECONDS.getValue(g["unit"]!!.value)
    return n * u + if (g["halfmid"] != null || g["halfend"] != null) u / 2 else 0
}

/** "ten minutes" → 600, "an hour and a half" → 5400, "1 hour and 30
 *  minutes" → 5400; null when it is not a duration in this grammar. */
internal fun parseDurationSeconds(text: String): Int? {
    val t = text.trim().lowercase()
    clauseSeconds(t)?.let { return it }
    val joiner = Regex(",? and |,? ")
    for (m in joiner.findAll(t)) {
        val a = clauseSeconds(t.substring(0, m.range.first)) ?: continue
        val b = clauseSeconds(t.substring(m.range.last + 1)) ?: continue
        return a + b
    }
    return null
}

// ─── Clock times (a subset of domovoi/handlers/shared/clock_time.py) ──────

/** A spoken clock time: [hour] 0-23 when [pm] is known, else 1-12 as said. */
internal data class SpokenTime(val hour: Int, val minute: Int, val pm: Boolean?)

private val HOUR_WORDS = mapOf("one" to 1, "two" to 2, "three" to 3, "four" to 4, "five" to 5, "six" to 6,
    "seven" to 7, "eight" to 8, "nine" to 9, "ten" to 10, "eleven" to 11, "twelve" to 12)
// "0:30" and "00:15" too (a 24-hour time past midnight), with its minutes only.
private val HOUR = "(?:2[0-3]|1\\d|0?[1-9]|0{1,2}(?=:[0-5]\\d)|${HOUR_WORDS.keys.sortedByDescending { it.length }.joinToString("|")})"
private const val AMPM = "(?:am|pm|a m|p m)"
private const val PART = "(?:in the morning|in the afternoon|in the evening|at night|tonight|tomorrow morning|this morning|this evening|" +
    "tomorrow evening|tomorrow night|tomorrow afternoon|tomorrow)"

/** What follows "alarm for" / "wake me at". */
internal val TIME = "(?:noon|midday|midnight|(?:half past|quarter past|quarter to|quarter till|quarter of|\\d{1,2} past|\\d{1,2} to|$NUMBER_WORD past|$NUMBER_WORD to) $HOUR|" +
    "$HOUR(?::[0-5]\\d| [0-5]\\d| (?:oh |o )?$NUMBER_WORD)?(?: ?o'? ?clock)?)(?: ?$AMPM)?(?: $PART)?"

private fun hourOf(word: String): Int? = word.toIntOrNull() ?: HOUR_WORDS[word]

internal fun parseTime(text: String): SpokenTime? {
    var t = text.trim().lowercase()
    var pm: Boolean? = null
    var night = false
    Regex(" ?($PART)$").find(t)?.let {
        pm = when (it.groupValues[1]) {
            "in the morning", "tomorrow morning", "this morning" -> false
            "tomorrow" -> null
            else -> true
        }
        night = it.groupValues[1] in NIGHT_PARTS
        t = t.substring(0, it.range.first).trim()
    }
    Regex(" ?($AMPM)$").find(t)?.let {
        pm = it.groupValues[1].startsWith("p")
        night = false // "2 pm tonight": am or pm said wins (clock_time.py)
        t = t.substring(0, it.range.first).trim()
    }
    // "12 at night", "1 tonight": the small hours after the evening, as the
    // server's clock_time.candidate_hours reads them (12 and 1-4 at night
    // are a.m.; 5-11 at night are p.m.).
    fun meridiem(h12: Int): Boolean? = if (night) !(h12 == 12 || h12 <= 4) else pm
    t = t.replace(Regex(" ?o'? ?clock$"), "")
    if (t == "noon" || t == "midday") return SpokenTime(12, 0, true)
    if (t == "midnight") return SpokenTime(0, 0, false)
    Regex("^(half past|quarter past|quarter to|quarter till|quarter of|(\\S+(?: \\S+)?) past|(\\S+(?: \\S+)?) to) (\\S+)$").matchEntire(t)?.let { m ->
        val h = hourOf(m.groupValues[4]) ?: return null
        if (h !in 1..12) return null
        val rel = m.groupValues[1]
        val (hh, mm) = when {
            rel == "half past" -> h to 30
            rel == "quarter past" -> h to 15
            rel == "quarter to" || rel == "quarter till" || rel == "quarter of" -> (if (h == 1) 12 else h - 1) to 45
            rel.endsWith("past") -> h to (parseNumber(m.groupValues[2]) ?: return null)
            else -> (if (h == 1) 12 else h - 1) to (60 - (parseNumber(m.groupValues[3]) ?: return null))
        }
        if (mm !in 0..59) return null
        return withMeridiem(hh, mm, meridiem(hh))
    }
    Regex("^(\\S+?)(?::(\\d{2})| (\\d{2})| (?:oh |o )?(.+))?$").matchEntire(t)?.let { m ->
        val h = hourOf(m.groupValues[1]) ?: return null
        val minute = when {
            m.groupValues[2].isNotEmpty() -> m.groupValues[2].toInt()
            m.groupValues[3].isNotEmpty() -> m.groupValues[3].toInt()
            m.groupValues[4].isNotEmpty() -> parseNumber(m.groupValues[4]) ?: return null
            else -> 0
        }
        if (h > 23 || minute > 59) return null
        if (h > 12) return if (pm == false) null else SpokenTime(h, minute, true)
        if (h == 0) return SpokenTime(0, minute, false)
        return withMeridiem(h, minute, meridiem(h))
    }
    return null
}

private val NIGHT_PARTS = setOf("at night", "tonight", "tomorrow night")

private fun withMeridiem(hour: Int, minute: Int, pm: Boolean?): SpokenTime = when (pm) {
    null -> SpokenTime(hour, minute, null)
    true -> SpokenTime(if (hour == 12) 12 else hour + 12, minute, true)
    false -> SpokenTime(if (hour == 12) 0 else hour, minute, false)
}
