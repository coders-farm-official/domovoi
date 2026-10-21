package com.domovoi.app.assistant

import java.util.Locale

// N1, phone facts (design-notes/ASSISTANT-NEXT-PLAN-2026-10-20.md, part 1):
// the few small facts a phone chat's model is told with every turn, the way
// it is told the clock. Nothing here reads the phone or the network: the app
// hands the facts in (AssistantController's PhoneContext), already cached.

/** Something playing (or paused), as the phone's player knows it. */
data class NowPlayingFact(
    val title: String,
    val artist: String? = null,
    /** Paused rather than playing. */
    val paused: Boolean = false,
    /** A room the phone cast to ("kitchen"); null: this phone. */
    val room: String? = null,
    val positionSec: Double? = null,
    val durationSec: Double? = null,
)

/** The home server, as this phone sees it. */
enum class ServerLink {
    /** No home server set up on this phone: the line is left out. */
    NONE,
    CONNECTED,
    NOT_CONNECTED,
}

/**
 * The prompt's phone facts (part 1's "Prompt" rows), all cached on the
 * phone: nothing is fetched to fill them.
 */
data class PromptFacts(
    val playing: NowPlayingFact? = null,
    /** The playback sleep timer, seconds left; null when it is off. */
    val sleepRemainingSec: Int? = null,
    val server: ServerLink = ServerLink.NONE,
    /** The household's room names, cached from the app's own reads; given
     *  to the model only while connected. */
    val rooms: List<String> = emptyList(),
    /** The listener picked in Settings, first name only. */
    val listener: String? = null,
) {
    companion object {
        val NONE = PromptFacts()
    }
}

/** One line of what the model is told alongside a turn (the clock, the
 *  phone facts), named by [kind] so a kept conversation is told a line again
 *  only when that line changed ([withContextLines]). */
data class ContextLine(val kind: String, val text: String) {
    companion object {
        const val CLOCK = "clock"
        const val FACTS = "facts"
    }
}

object PhoneFacts {
    /** At most this many room names go in (a big house says "and 3 more"). */
    const val MAX_ROOMS = 4

    /** Longer titles and names are cut, so the line stays small. */
    private const val MAX_NAME = 40

    /**
     * The facts as the model reads them, or null when there is nothing to
     * say. About 15-55 tokens: "Phone facts: playing "Glass Harbor" by
     * Velvet Kites on this phone, sleep timer 20 minutes left. Connected to
     * the home server, rooms: kitchen, office, garage. The person talking is
     * Kamron (say the name only if asked)." (The last clause after
     * the emulator gate: E2B once offered "a board game night with Kamron".)
     *
     * The playback position is left out on purpose: it changes every second,
     * so a kept conversation would be told the line again on every turn (the
     * "what's playing" fast path says the position).
     */
    fun line(f: PromptFacts): String? {
        // About 60 tokens at most (English runs about 4 characters a token):
        // fewer room names first, then shorter names, then a hard cut.
        for (rooms in MAX_ROOMS downTo 0) {
            val l = build(f, rooms, MAX_NAME) ?: return null
            if (l.length <= MAX_CHARS) return l
        }
        val short = build(f, 0, SHORT_NAME) ?: return null
        return if (short.length <= MAX_CHARS) short else short.take(MAX_CHARS - 1).trimEnd() + "…"
    }

    /** Most characters the line may take: about 60 tokens. */
    const val MAX_CHARS = 240

    private const val SHORT_NAME = 20

    private fun build(f: PromptFacts, maxRooms: Int, maxName: Int): String? {
        fun cut(s: String) = cut(s, maxName)
        val parts = mutableListOf<String>()
        f.playing?.let { p ->
            val what = buildString {
                append(if (p.paused) "paused: " else "playing ")
                append('"').append(cut(p.title)).append('"')
                p.artist?.takeIf { it.isNotBlank() }?.let { append(" by ").append(cut(it)) }
                append(if (p.room != null) " in the ${cut(p.room)}" else " on this phone")
            }
            val sleep = f.sleepRemainingSec?.takeIf { it > 0 }?.let { ", sleep timer ${minutes(it)} left" }.orEmpty()
            parts += what + sleep
        }
        when (f.server) {
            ServerLink.CONNECTED -> {
                val rooms = f.rooms.map { it.trim() }.filter { it.isNotEmpty() }.distinct()
                parts += if (rooms.isEmpty() || maxRooms == 0) {
                    "connected to the home server"
                } else {
                    val shown = rooms.take(maxRooms).joinToString(", ") { cut(it) }
                    val more = (rooms.size - maxRooms).takeIf { it > 0 }?.let { " and $it more" }.orEmpty()
                    "connected to the home server, rooms: $shown$more"
                }
            }
            ServerLink.NOT_CONNECTED -> parts += "not connected to the home server"
            ServerLink.NONE -> Unit
        }
        val sentences = mutableListOf<String>()
        if (parts.isNotEmpty()) {
            // "Phone facts: playing …. Connected to …": the first part runs on
            // from the label, the rest start their own sentence.
            val body = parts.mapIndexed { i, p -> if (i == 0) p else p.replaceFirstChar { it.uppercase() } }
            sentences += "Phone facts: " + body.joinToString(". ") + "."
        }
        firstName(f.listener)?.let { sentences += "The person talking is $it (say the name only if asked)." }
        return sentences.joinToString(" ").ifBlank { null }
    }

    /** "Kamron" from "Kamron Kennedy"; null for a blank name. */
    fun firstName(name: String?): String? =
        name?.trim()?.split(Regex("\\s+"))?.firstOrNull()?.takeIf { it.isNotBlank() }?.let { cut(it) }

    /** "20 minutes", "1 minute", "under a minute". */
    fun minutes(seconds: Int): String {
        val m = (seconds + 59) / 60
        return when {
            seconds < 60 -> "under a minute"
            m == 1 -> "1 minute"
            else -> "$m minutes"
        }
    }

    /** "1:12", "1:02:05". */
    fun clock(seconds: Double): String {
        val s = seconds.toLong().coerceAtLeast(0)
        return if (s >= 3600) String.format(Locale.US, "%d:%02d:%02d", s / 3600, s % 3600 / 60, s % 60)
        else String.format(Locale.US, "%d:%02d", s / 60, s % 60)
    }

    private fun cut(s: String, max: Int = MAX_NAME): String {
        val t = s.trim().replace(Regex("\\s+"), " ")
        return if (t.length > max) t.take(max).trimEnd() + "…" else t
    }
}

/** What a turn's context lines do to the model's input ([withContextLines]). */
internal data class ContextTurn(
    /** The system instruction, for a conversation built for this turn. */
    val system: String,
    /** The new question as it is sent. */
    val question: String,
    /** What the conversation has now been told, by kind. */
    val told: Map<String, String>,
)

/**
 * [withClock] for any number of lines: a conversation built for this turn
 * has every line at the end of its system instruction; a kept one, told
 * [told] so far, gets only the lines that changed since (a new clock reading,
 * a song that changed), ahead of the new [question]. Lines that went away
 * (nothing plays any more) are not repeated: the next changed facts line
 * says the new state.
 */
internal fun withContextLines(
    system: String,
    question: String,
    lines: List<ContextLine>,
    told: Map<String, String>,
    rebuilt: Boolean,
): ContextTurn {
    val present = lines.filter { it.text.isNotBlank() }
    val now = present.associate { it.kind to it.text }
    if (rebuilt) {
        val sys = if (present.isEmpty()) system else system + "\n\n" + present.joinToString("\n") { it.text }
        return ContextTurn(sys, question, now)
    }
    val changed = present.filter { told[it.kind] != it.text }
    val q = if (changed.isEmpty()) question else changed.joinToString("\n") { it.text } + "\n\n" + question
    return ContextTurn(system, q, told + now)
}
