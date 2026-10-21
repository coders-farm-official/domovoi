package com.domovoi.app.ui.screens.manual

import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.shell.visibleWith
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/*
 * The user manual's content: the server's web/static/manual-content.json
 * (made by scripts/manual/build_manual.py from scripts/manual/manual.md).
 * The dashboard's #manual page renders the same file; the app fetches it
 * from the server it is connected to (ManualRepository), so the manual
 * here always matches that server's version. This file is the pure part:
 * the model, the inline markup, the wake word the rooms answer to, which
 * docs a "More detail" link opens, the checklist's ticks, and search.
 * Unit-tested on the JVM (ManualContentTest), against the repo's own file.
 */

internal const val MANUAL_PATH = "/manual-content.json"
internal const val MANUAL_SCHEMA_PREFIX = "domovoi-manual/"

@Serializable
internal data class ManualDoc(
    val schema: String = "",
    @SerialName("doc_base") val docBase: String = "",
    val labels: JsonObject = JsonObject(emptyMap()),
    @SerialName("wake_word") val wakeWord: ManualWakeWord = ManualWakeWord(),
    val routes: Map<String, ManualRoute> = emptyMap(),
    /** handler → the server feature it needs on (GET /api/capabilities
     *  features) to be listed in "Everything it can do". */
    @SerialName("handler_needs_feature") val handlerNeedsFeature: Map<String, String> = emptyMap(),
    val centre: ManualCentre = ManualCentre(),
    val start: ManualStart = ManualStart(),
    val topics: List<ManualTopic> = emptyList(),
    val pieces: List<ManualPiece> = emptyList(),
) {
    /** A chrome string from `labels` ("search_placeholder"), or a nested
     *  one ("admin", "text"); [fallback] when the file has none. */
    fun label(key: String, sub: String? = null, fallback: String = ""): String {
        val v = labels[key] ?: return fallback
        return runCatching {
            if (sub == null) v.jsonPrimitive.contentOrNull else v.jsonObject[sub]?.jsonPrimitive?.contentOrNull
        }.getOrNull() ?: fallback
    }

    fun piece(id: String?): ManualPiece? = pieces.firstOrNull { it.id == id }
    fun topic(id: String?): ManualTopic? = topics.firstOrNull { it.id == id }

    /** The topics around the centre (the centre's own opens from it). */
    val ringTopics: List<ManualTopic> get() = topics.filter { it.id != centre.opens }

    companion object {
        fun parse(text: String): ManualDoc? = runCatching {
            DomovoiJson.decodeFromString(serializer(), text)
        }.getOrNull()?.takeIf { it.schema.startsWith(MANUAL_SCHEMA_PREFIX) && it.topics.isNotEmpty() }
    }
}

@Serializable
internal data class ManualWakeWord(
    val default: String = "hey_jarvis",
    val names: Map<String, String> = mapOf("hey_jarvis" to "Hey Jarvis"),
    val quoted: String = "“{phrase}”",
    val note: String = "Some rooms answer to another word: {list}.",
    @SerialName("note_item") val noteItem: String = "“{phrase}” in {rooms}",
    @SerialName("note_join") val noteJoin: String = "; ",
)

@Serializable
internal data class ManualRoute(val dashboard: String? = null, val app: ManualAppRoute = ManualAppRoute())

/** [screen]: a Route name; [tab]: a Settings tab key; [dashboard]: the app
 *  opens the dashboard at the route instead. */
@Serializable
internal data class ManualAppRoute(val screen: String? = null, val tab: String? = null, val dashboard: Boolean = false)

@Serializable
internal data class ManualCentre(
    val id: String = "domovoi",
    val title: String = "Domovoi",
    val subtitle: String = "",
    val opens: String = "",
)

@Serializable
internal data class ManualStart(
    val title: String = "Start here",
    val blocks: List<ManualBlock> = emptyList(),
    val checklist: List<ManualCheck> = emptyList(),
)

@Serializable
internal data class ManualCheck(
    val id: String = "",
    @SerialName("done_when") val doneWhen: String = "",
    val platforms: List<String> = emptyList(),
    val text: String = "",
    @SerialName("app_text") val appText: String? = null,
    @SerialName("app_done_when") val appDoneWhen: String? = null,
)

@Serializable
internal data class ManualTopic(
    val id: String = "",
    val title: String = "",
    val subtitle: String = "",
    val keywords: List<String> = emptyList(),
    val pieces: List<String> = emptyList(),
    val centre: Boolean = false,
    val intro: String? = null,
)

@Serializable
internal data class ManualPiece(
    val id: String = "",
    val topic: String = "",
    val title: String = "",
    val subtitle: String = "",
    val keywords: List<String> = emptyList(),
    val preview: Boolean = false,
    val plugin: String? = null,
    val live: String? = null,
    @SerialName("alias_of") val aliasOf: String? = null,
    val blocks: List<ManualBlock> = emptyList(),
    val more: ManualMore? = null,
)

@Serializable
internal data class ManualMore(val text: String = "More detail", val target: String = "")

/** One block of a page; which fields are set depends on [type] (the
 *  file's notes.blocks). */
@Serializable
internal data class ManualBlock(
    val type: String = "",
    val text: String = "",
    val only: String? = null,
    val ordered: Boolean = false,
    val items: List<ManualItem> = emptyList(),
    val role: String? = null,
    val admin: Boolean = false,
    val note: String? = null,
    val phrases: List<ManualPhrase> = emptyList(),
    val header: List<String> = emptyList(),
    val rows: List<List<String>> = emptyList(),
    val slot: String? = null,
)

@Serializable
internal data class ManualItem(val text: String = "", val only: String? = null)

@Serializable
internal data class ManualPhrase(
    val text: String = "",
    val handler: String? = null,
    val path: String? = null,
    val note: String? = null,
    val then: Boolean = false,
    val plugin: String? = null,
    val pending: String? = null,
)

/** Shown in the app: everything but the dashboard-only lines. */
internal fun shownInApp(only: String?): Boolean = only == null || only == "app"

// ─── inline markup ───────────────────────────────────────────────────────

internal sealed interface Span {
    data class Plain(val text: String) : Span
    data class Bold(val text: String) : Span
    data class Italic(val text: String) : Span
    data class Code(val text: String) : Span
    /** kind: route | node | doc. */
    data class Link(val text: String, val kind: String, val target: String) : Span
    /** admin | preview | plugin. */
    data class Tag(val name: String) : Span
    data object Wake : Span
}

private val TOKEN = Regex(
    """\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[[^\]]+\]\((?:route|node|doc):[^)]+\)|\{(?:admin|preview|plugin|wake)\}""",
)
private val LINK = Regex("""^\[([^\]]+)\]\((route|node|doc):([^)]+)\)$""")

/** The file's inline markup (notes.inline) as spans. */
internal fun parseInline(text: String): List<Span> {
    val out = mutableListOf<Span>()
    var at = 0
    for (m in TOKEN.findAll(text)) {
        if (m.range.first > at) out += Span.Plain(text.substring(at, m.range.first))
        val t = m.value
        out += when {
            t.startsWith("**") -> Span.Bold(t.substring(2, t.length - 2))
            t.startsWith("`") -> Span.Code(t.substring(1, t.length - 1))
            t == "{wake}" -> Span.Wake
            t.startsWith("{") -> Span.Tag(t.substring(1, t.length - 1))
            t.startsWith("[") -> LINK.find(t)!!.destructured.let { (label, kind, target) -> Span.Link(label, kind, target) }
            else -> Span.Italic(t.substring(1, t.length - 1))
        }
        at = m.range.last + 1
    }
    if (at < text.length) out += Span.Plain(text.substring(at))
    return out
}

// ─── the wake word ───────────────────────────────────────────────────────

/** A room as the manual needs it (GET /api/satellites). */
@Serializable
internal data class ManualRoom(
    @SerialName("room_id") val roomId: String = "",
    @SerialName("room_label") val roomLabel: String? = null,
    val status: String = "",
    @SerialName("wake_word") val wakeWord: String? = null,
    @SerialName("wake_phrase") val wakePhrase: String? = null,
    @SerialName("last_seen_at") val lastSeenAt: String? = null,
    @SerialName("now_playing") val nowPlaying: ManualNowPlaying? = null,
) {
    val name: String get() = roomLabel?.takeIf { it.isNotBlank() } ?: roomId
}

@Serializable
internal data class ManualNowPlaying(val title: String? = null, val file: String? = null)

internal fun titleWords(s: String?): String =
    (s ?: "").replace(Regex("[_-]+"), " ").trim().split(Regex("\\s+")).filter { it.isNotEmpty() }
        .joinToString(" ") { w -> w.replaceFirstChar { it.uppercaseChar() } }

/** The words a room answers to, as people say them. */
internal fun wakePhraseOf(ww: ManualWakeWord, room: ManualRoom): String? {
    val slug = room.wakeWord ?: return null
    return room.wakePhrase?.takeIf { it.isNotBlank() }?.let(::titleWords) ?: ww.names[slug] ?: titleWords(slug)
}

internal data class WakeInfo(val lead: String, val others: List<Pair<String, List<String>>>)

/** The word to name: the out-of-the-box word while any room answers to it
 *  (or nothing is known), else the one most rooms use; [WakeInfo.others]:
 *  the other words, with their rooms (the dashboard's umWake). */
internal fun wakeInfo(ww: ManualWakeWord, rooms: List<ManualRoom>?): WakeInfo {
    val known = rooms.orEmpty().filter { it.wakeWord != null }
    val groups = LinkedHashMap<String, MutableList<String>>()
    known.forEach { r -> groups.getOrPut(wakePhraseOf(ww, r)!!) { mutableListOf() } += r.name }
    val fallback = ww.names[ww.default] ?: titleWords(ww.default)
    val order = compareByDescending<Map.Entry<String, MutableList<String>>> { it.value.size }.thenBy { it.key }
    val lead = if (known.isNotEmpty() && known.none { it.wakeWord == ww.default }) {
        groups.entries.sortedWith(order).first().key
    } else fallback
    val others = groups.entries.filter { it.key != lead }.sortedWith(order).map { it.key to it.value.toList() }
    return WakeInfo(lead, others)
}

internal fun fill(template: String, vars: Map<String, String>): String =
    Regex("""\{(\w+)\}""").replace(template) { m -> vars[m.groupValues[1]] ?: m.value }

internal fun wakeNote(ww: ManualWakeWord, info: WakeInfo): String? {
    if (info.others.isEmpty()) return null
    val list = info.others.joinToString(ww.noteJoin) { (phrase, rooms) ->
        fill(ww.noteItem, mapOf("phrase" to phrase, "rooms" to rooms.joinToString(", ")))
    }
    return fill(ww.note, mapOf("list" to list))
}

// ─── links ───────────────────────────────────────────────────────────────

private val RELEASE = Regex("""^\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$""")

/** The docs of the version the server runs: its tag when the version label
 *  is a release exactly ("1.0.0", "1.0.0-rc2"), else main. */
internal fun docRef(versionLabel: String?): String =
    if (versionLabel != null && RELEASE.matches(versionLabel)) "v$versionLabel" else "main"

internal fun docUrl(doc: ManualDoc, versionLabel: String?, target: String): String =
    doc.docBase.replace("{ref}", docRef(versionLabel)) + target.removePrefix("doc:")

/** The dashboard at [hash] ("#settings/voices") on the connected server. */
internal fun dashboardUrl(serverUrl: String?, hash: String): String? {
    val base = serverUrl?.trim()?.trimEnd('/').orEmpty()
    if (base.isEmpty()) return null
    return "$base/${if (hash.startsWith("#")) hash else "#$hash"}"
}

// ─── the checklist ───────────────────────────────────────────────────────

/** What the app read for the checklist; null = couldn't tell. */
internal data class ManualSignals(
    val rooms: List<ManualRoom>? = null,
    val roomHeard: Boolean? = null,
    val people: Int? = null,
    val tracks: Long? = null,
)

/** The lines the app shows, with their text (the app's own for the phone
 *  line) and whether each is done (null: can't tell from here). */
internal fun appChecklist(doc: ManualDoc, s: ManualSignals): List<Pair<String, Boolean?>> =
    doc.start.checklist.mapNotNull { c ->
        when {
            "app" in c.platforms -> c.text to done(c.doneWhen, s)
            c.appText != null -> c.appText to (if (c.appDoneWhen == "this_phone_paired") true else null)
            else -> null
        }
    }

private fun done(key: String, s: ManualSignals): Boolean? = when (key) {
    "rooms" -> s.rooms?.isNotEmpty()
    "room_heard" -> s.roomHeard
    "people" -> s.people?.let { it > 0 }
    "music" -> s.tracks?.let { it > 0 }
    else -> null
}

// ─── search ──────────────────────────────────────────────────────────────

/** Pieces matching every word of [query] (title, subtitle, search words,
 *  topic), best first: the dashboard's umSearch. */
internal fun searchManual(doc: ManualDoc, visible: Set<String>, query: String, max: Int = 8): List<ManualPiece> {
    val words = query.lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }
    if (words.isEmpty()) return emptyList()
    return doc.pieces.asSequence()
        .filter { it.id in visible && it.aliasOf == null }
        .mapNotNull { p ->
            val title = p.title.lowercase()
            val hay = (listOf(title, p.subtitle.lowercase()) + p.keywords.map { it.lowercase() } +
                (doc.topic(p.topic)?.title?.lowercase() ?: "")).joinToString(" · ")
            if (!words.all { it in hay }) return@mapNotNull null
            val score = words.sumOf { w ->
                when {
                    title.startsWith(w) -> 4
                    w in title -> 3
                    p.keywords.any { it.lowercase().startsWith(w) } -> 2
                    else -> 1
                }.toInt()
            }
            p to score
        }
        .sortedWith(compareByDescending<Pair<ManualPiece, Int>> { it.second }.thenBy { it.first.title })
        .take(max).map { it.first }.toList()
}

/** The manual's own phrases per handler (never a pending one, never one
 *  written with "…"): what "Everything it can do" lists where the server
 *  gives a feature none. */
internal fun phrasesByHandler(doc: ManualDoc): Map<String, List<String>> {
    val out = LinkedHashMap<String, MutableList<String>>()
    (listOf(doc.start.blocks) + doc.pieces.map { it.blocks }).flatten().filter { it.type == "say" }.forEach { b ->
        b.phrases.forEach { p ->
            val h = p.handler ?: return@forEach
            if (p.pending != null || p.then || "…" in p.text) return@forEach
            val list = out.getOrPut(h) { mutableListOf() }
            if (p.text !in list) list += p.text
        }
    }
    return out
}

/** The phrase Home's first-run hint offers: Start here's first. */
internal fun startPhrase(doc: ManualDoc?): String? =
    doc?.start?.blocks?.firstOrNull { it.type == "say" && it.phrases.isNotEmpty() }
        ?.phrases?.firstOrNull { it.pending == null && !it.then }?.text

/** A label read from a JSON element, for the few nested labels. */
internal fun JsonObject.str(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull

/** The features "Everything it can do" lists: those the server has on. */
internal fun listedHandlers(doc: ManualDoc, names: List<String>, features: Map<String, Boolean>): List<String> =
    names.filter { n -> doc.handlerNeedsFeature[n]?.let { features[it] == true } ?: true }

/** Whether a manual link to [route] opens something here. A link to an app
 *  screen this server doesn't offer (a plugin's that isn't installed; Chat
 *  with the server's AI features off) reads as plain text, never as a link
 *  that lands back on Home. A route with no app screen opens only if it
 *  opens the dashboard. */
internal fun manualRouteOpens(route: ManualRoute, caps: Capabilities): Boolean {
    if (route.app.dashboard) return true
    val name = route.app.screen ?: return false
    val screen = Route.entries.firstOrNull { it.name == name } ?: return true
    return screen.visibleWith(caps)
}
