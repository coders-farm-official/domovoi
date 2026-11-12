package com.domovoi.app.ui.screens.podcasts

import com.domovoi.app.ui.components.parseInstant
import com.domovoi.app.ui.screens.home.plural
import java.time.Instant
import java.time.ZoneId
import java.time.temporal.ChronoUnit
import kotlin.math.floor
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull

/**
 * Episode details, worded exactly as the web's podcasts.jsx (podPublished,
 * podLength, podProgress, podDownload, podPlainText, podSubInfo,
 * podcastsPollText; pinned on the web in test_web_podcasts_page.py). Every
 * helper that reads the clock takes `now` and `zone` so a test can fix them.
 * Text only: a feed's description is HTML, reduced here to plain text and
 * drawn by a Text, never rendered as markup.
 */
internal object PodFormat {
    private val MONTHS = listOf("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

    /** JS Math.round (half up), not Kotlin's half-even round(). */
    private fun jsRound(x: Double): Long = floor(x + 0.5).toLong()

    /** "today" · "yesterday" · "3 days ago" (to 6) · "12 Oct" · "12 Oct 2025"
     *  (the year only when it is not this year). "" for no / unreadable date.
     *  Calendar days in the phone's zone, like the web's local time. */
    fun published(iso: String?, now: Instant, zone: ZoneId): String {
        val t = parseInstant(iso) ?: return ""
        val d = t.atZone(zone).toLocalDate()
        val today = now.atZone(zone).toLocalDate()
        val ago = ChronoUnit.DAYS.between(d, today)
        if (ago == 0L) return "today"
        if (ago == 1L) return "yesterday"
        if (ago in 2..6) return "$ago days ago"
        val plain = "${d.dayOfMonth} ${MONTHS[d.monthValue - 1]}"
        return if (d.year == today.year) plain else "$plain ${d.year}"
    }

    /** "1 h 12 min" · "1 h" · "38 min" · "<1 min" · "" when unknown. */
    fun length(sec: Double?): String {
        if (sec == null || !(sec > 0)) return ""
        val total = jsRound(sec / 60)
        if (total < 1) return "<1 min"
        val h = total / 60
        val m = total % 60
        if (h == 0L) return "$m min"
        return if (m == 0L) "$h h" else "$h h $m min"
    }

    sealed interface Progress {
        /** An older server (no position_sec in the row): nothing shown. */
        data object Unknown : Progress
        /** Never started: the unplayed dot. */
        data object New : Progress
        data object Played : Progress
        /** "24 min left" and a thin bar at [pct] percent. */
        data class Partial(val label: String, val pct: Int) : Progress
        /** A place but no length: "at 24 min". */
        data class Resumed(val label: String) : Progress
    }

    /** The listener's place in an episode. Within 30 s of the end counts as played. */
    fun progress(ep: PodcastEpisode): Progress {
        if (!ep.positionKnown) return Progress.Unknown
        val pos = ep.position_sec
        if (pos == null || !(pos > 0)) return Progress.New
        val dur = ep.duration_sec
        if (dur == null || !(dur > 0)) return Progress.Resumed("at ${length(pos).ifEmpty { "<1 min" }}")
        if (dur - pos <= 30) return Progress.Played
        val pct = jsRound(pos / dur * 100).toInt().coerceIn(2, 98)
        return Progress.Partial("${length(dur - pos)} left", pct)
    }

    fun progressLabel(p: Progress): String? = when (p) {
        Progress.Played -> "played"
        is Progress.Partial -> p.label
        is Progress.Resumed -> p.label
        else -> null
    }

    fun download(ep: PodcastEpisode): String = when {
        ep.has_file -> "downloaded"
        ep.download_status == "downloading" -> "downloading"
        ep.download_status == "failed" -> "download failed"
        else -> "not downloaded"
    }

    /** The meta line under an episode's title, in the web's order. */
    fun meta(ep: PodcastEpisode, now: Instant, zone: ZoneId): List<String> = listOfNotNull(
        published(ep.published_at, now, zone).ifEmpty { null },
        length(ep.duration_sec).ifEmpty { null },
        progressLabel(progress(ep)),
        ep.chapters.size.takeIf { it > 0 }?.let { plural(it, "chapter") },
        download(ep),
    )

    private val ENTITIES = mapOf(
        "amp" to "&", "lt" to "<", "gt" to ">", "quot" to "\"", "apos" to "'", "nbsp" to " ",
        "hellip" to "…", "ndash" to "–", "mdash" to "—", "rsquo" to "’",
        "lsquo" to "‘", "rdquo" to "”", "ldquo" to "“",
    )
    private val SCRIPT = Regex("<\\s*(script|style)[^>]*>[\\s\\S]*?<\\s*/\\s*\\1\\s*>", RegexOption.IGNORE_CASE)
    private val BREAK = Regex("<\\s*br\\s*/?\\s*>|<\\s*/\\s*(p|div|li|h[1-6]|ul|ol|blockquote)\\s*>", RegexOption.IGNORE_CASE)
    private val TAG = Regex("<[^>]*>")
    private val ENTITY = Regex("&(#x[0-9a-f]+|#\\d+|[a-z]+);", RegexOption.IGNORE_CASE)
    private val SPACES = Regex("[ \\t\\u00a0]+")
    private val LINE_EDGE = Regex(" ?\\n ?")
    private val MANY_LINES = Regex("\\n{3,}")

    /** HTML to plain text: block ends become line breaks, tags are dropped,
     *  the common entities decoded. */
    fun plainText(html: String?): String {
        if (html.isNullOrEmpty()) return ""
        return html
            .replace(SCRIPT, " ")
            .replace(BREAK, "\n")
            .replace(TAG, "")
            .replace(ENTITY) { m ->
                val e = m.groupValues[1]
                if (e[0] == '#') {
                    val n = (if (e.length > 1 && (e[1] == 'x' || e[1] == 'X')) e.drop(2).toLongOrNull(16)
                    else e.drop(1).toLongOrNull()) ?: 0L
                    if (n > 0 && n < 0x110000) String(Character.toChars(n.toInt())) else m.value
                } else {
                    ENTITIES[e.lowercase()] ?: m.value
                }
            }
            .replace(SPACES, " ")
            .replace(LINE_EDGE, "\n")
            .replace(MANY_LINES, "\n\n")
            .trim()
    }

    /** The web's relTime: "just now" · "1m ago" · "5m ago" · "2h ago" · "3d ago". */
    fun relTime(iso: String?, now: Instant): String {
        val t = parseInstant(iso) ?: return "—"
        val s = (now.toEpochMilli() - t.toEpochMilli()) / 1000.0
        return when {
            s < 30 -> "just now"
            s < 90 -> "1m ago"
            s < 3600 -> "${jsRound(s / 60)}m ago"
            s < 86400 -> "${jsRound(s / 3600)}h ago"
            else -> "${jsRound(s / 86400)}d ago"
        }
    }

    /** The show's info line: "6 episodes · updated 2h ago". */
    fun subInfo(sub: PodcastSubscription, total: Int?, now: Instant): String {
        val n = sub.episode_count ?: total
        val bits = mutableListOf<String>()
        if (n != null) bits += plural(n, "episode")
        bits += if (sub.last_polled_at != null) "updated ${relTime(sub.last_polled_at, now)}" else "not checked yet"
        return bits.joinToString(" · ")
    }

    /** The "poll now" toast: the counts, then any show whose feed couldn't
     *  be read, by name, with the server's plain-words reason (an older
     *  server sends no `failures`). */
    fun pollText(r: JsonElement?): String {
        val obj = r as? JsonObject
        fun count(k: String) = (obj?.get(k) as? JsonPrimitive)?.intOrNull ?: 0
        val head = "Polled — ${count("downloaded")} downloaded, ${count("new")} new"
        val failures = (obj?.get("failures") as? JsonArray).orEmpty()
        if (failures.isEmpty()) return head
        val n = failures.size
        fun str(f: JsonElement, k: String) =
            ((f as? JsonObject)?.get(k) as? JsonPrimitive)?.takeIf { it.isString }?.content?.ifEmpty { null }
        val named = failures.take(3).joinToString("; ") { f ->
            "${str(f, "title") ?: "a show"} — ${str(f, "message") ?: "couldn't read the feed"}"
        }
        val more = if (n > 3) "; and ${n - 3} more" else ""
        return "$head. ${plural(n, "show")} couldn't be read: $named$more"
    }

    /** The episodes read: search, then the listener's slot (the same
     *  device_id + person_id the player saves positions under). */
    fun episodesPath(subId: Long, needle: String, deviceId: String?, personId: String?, encode: (String) -> String): String {
        val qs = mutableListOf<String>()
        if (needle.isNotEmpty()) qs += "q=" + encode(needle)
        if (!deviceId.isNullOrEmpty()) {
            qs += "device_id=" + encode(deviceId)
            personId?.toLongOrNull()?.let { qs += "person_id=$it" }
        }
        return "/api/podcasts/subscriptions/$subId/episodes" + (if (qs.isEmpty()) "" else "?" + qs.joinToString("&"))
    }
}
