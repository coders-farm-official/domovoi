package com.domovoi.app.assistant

/**
 * The model's read tools for a phone chat (N1, part 1): find_music,
 * find_video, find_book, list_timers and phone_status. They only read:
 * nothing plays, nothing is set, nothing leaves the phone. LiteRT-LM runs in
 * manual tool mode (the app sees each call and runs it itself), so a call
 * for a tool that isn't offered, or with odd arguments, is answered with an
 * error and never reaches anything else.
 *
 * A tool is offered only when it can work: find_music and find_video with
 * the media permission local Music and Videos ask for, find_book when a book
 * is saved on the phone.
 */
object PhoneTools {
    const val FIND_MUSIC = "find_music"
    const val FIND_VIDEO = "find_video"
    const val FIND_BOOK = "find_book"
    const val LIST_TIMERS = "list_timers"
    const val PHONE_STATUS = "phone_status"

    /** Results per search: the plan's "top 5". */
    const val LIMIT = 5

    /** A query longer than this is cut (the model sometimes writes a
     *  sentence). */
    private const val MAX_QUERY = 80

    private val SPECS = mapOf(
        FIND_MUSIC to """{"name":"$FIND_MUSIC","description":"Search the music saved on this phone by song title, artist or album. Returns up to $LIMIT matches. It cannot play anything.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"A song, artist or album name"}},"required":["query"]}}""",
        FIND_VIDEO to """{"name":"$FIND_VIDEO","description":"Search the videos saved on this phone by name. Returns up to $LIMIT matches. It cannot play anything.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"Words from the video's name"}},"required":["query"]}}""",
        FIND_BOOK to """{"name":"$FIND_BOOK","description":"Search the audiobooks saved on this phone by title or author. Returns each match with the chapter and place the listener is at. It cannot play anything.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"A title or author; empty for every saved book"}}}}""",
        LIST_TIMERS to """{"name":"$LIST_TIMERS","description":"List the timers and reminders this phone has from the home server, with when each rings.","parameters":{"type":"object","properties":{}}}""",
        PHONE_STATUS to """{"name":"$PHONE_STATUS","description":"Read this phone's battery and charging, free storage, network (Wi-Fi or mobile data), model, Android version and memory.","parameters":{"type":"object","properties":{}}}""",
    )

    /** The tools offered now, in a fixed order (the order is part of the
     *  conversation's identity: [ReadTools.key]). */
    fun offered(library: PhoneLibrary): List<String> = buildList {
        if (library.canFindMusic()) add(FIND_MUSIC)
        if (library.canFindVideos()) add(FIND_VIDEO)
        if (library.canFindBooks()) add(FIND_BOOK)
        add(LIST_TIMERS)
        add(PHONE_STATUS)
    }

    fun spec(name: String): String = SPECS.getValue(name)

    /**
     * Runs [name] with [args] and returns what the model is told (a map the
     * runtime turns into JSON). Unknown tools, tools not offered and failed
     * reads come back as {"error": …}.
     */
    suspend fun run(
        name: String,
        args: Map<String, Any?>,
        offered: List<String>,
        phone: PhoneContext,
        nowMs: Long,
        time: (Long) -> String,
    ): Map<String, Any?> {
        if (name !in offered) return mapOf("error" to "no tool named $name here")
        return runCatching {
            when (name) {
                FIND_MUSIC -> found(phone.library.findMusic(query(args), LIMIT), music = true)
                FIND_VIDEO -> found(phone.library.findVideos(query(args), LIMIT), music = false)
                FIND_BOOK -> books(phone.library.findBooks(query(args), LIMIT))
                LIST_TIMERS -> timers(phone.timers(nowMs), nowMs, time)
                PHONE_STATUS -> status(phone.reader)
                else -> mapOf("error" to "no tool named $name here")
            }
        }.getOrElse { mapOf("error" to "could not read it on this phone") }
    }

    private fun query(args: Map<String, Any?>): String =
        (args["query"] as? String).orEmpty().trim().take(MAX_QUERY)

    private fun found(items: List<FoundItem>, music: Boolean): Map<String, Any?> = mapOf(
        "count" to items.size,
        "results" to items.map { i ->
            buildMap {
                put("title", i.title)
                if (music) i.by?.let { put("artist", it) }
                if (music) i.album?.let { put("album", it) }
                i.durationSec?.let { put("length", PhoneFacts.clock(it)) }
            }
        },
    )

    private fun books(items: List<FoundItem>): Map<String, Any?> = mapOf(
        "count" to items.size,
        "results" to items.map { i ->
            buildMap {
                put("title", i.title)
                i.by?.let { put("author", it) }
                i.chapter?.let { put("chapter", it) }
                i.positionSec?.let { put("listened_to", PhoneFacts.clock(it)) }
                i.durationSec?.let { put("length", PhoneFacts.clock(it)) }
            }
        },
    )

    private fun timers(list: List<PhoneTimer>, nowMs: Long, time: (Long) -> String): Map<String, Any?> = mapOf(
        "count" to list.size,
        "timers" to list.take(10).map { t ->
            buildMap {
                put("kind", if (t.reminder) "reminder" else "timer")
                if (!t.masked) t.label?.takeIf { it.isNotBlank() }?.let { put("label", it) }
                if (t.masked) put("private", true)
                put("rings_at", time(t.triggerAtMs))
                if (!t.reminder) put("time_left", PhoneQuestions.left(t.triggerAtMs - nowMs))
                t.room?.takeIf { it.isNotBlank() }?.let { put("room", it) }
            }
        },
    )

    private fun status(r: PhoneReader): Map<String, Any?> = buildMap {
        r.battery()?.let { put("battery_percent", it.percent); put("charging", it.charging) }
        r.storage()?.let { put("free_storage", PhoneQuestions.gb(it.freeBytes)); put("total_storage", PhoneQuestions.gb(it.totalBytes)) }
        r.network()?.let { n ->
            put("network", when (n.kind) {
                NetworkKind.WIFI -> "wifi"
                NetworkKind.MOBILE -> "mobile data"
                NetworkKind.ETHERNET -> "wired"
                NetworkKind.OTHER -> "other"
                NetworkKind.NONE -> if (n.airplaneMode) "offline, airplane mode" else "offline"
            })
        }
        r.device()?.let { d ->
            put("phone", PhoneQuestions.phoneName(d).removePrefix("a "))
            put("android", d.android)
            put("memory", "${DeviceTiers.marketedGb(d.totalRamBytes)} GB")
        }
        if (isEmpty()) put("error", "could not read the phone")
    }
}

/** The tools a turn offers the model, and how to run a call. */
class ReadTools(
    val names: List<String>,
    val specs: List<String>,
    /** Runs one call; the result goes back to the model. */
    val run: suspend (name: String, args: Map<String, Any?>) -> Map<String, Any?>,
) {
    /** Which tools a conversation was built with: a kept conversation goes
     *  on only with the same set. */
    val key: String get() = names.joinToString(",")
}
