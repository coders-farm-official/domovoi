package com.domovoi.app.assistant

import android.content.Context
import com.domovoi.app.ui.screens.manual.ManualBlock
import com.domovoi.app.ui.screens.manual.ManualDoc
import com.domovoi.app.ui.screens.manual.shownInApp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlin.math.ln

/**
 * A phone chat's own help desk (assistant plan part 9): questions about
 * Domovoi itself ("how do I add a room?") are answered from the user manual
 * the app carries (assets/manual/manual-content.json, the dashboard's own
 * file copied in at build time), never from the server: each place answers
 * from its own world.
 *
 *  * A help question ([isHelpQuestion]) is searched in the manual's pieces
 *    ([search]): most of its content words must be found in a piece, or
 *    nothing is.
 *  * Found: the model is given up to [MAX_PASSAGES] short passages in a
 *    marked block and told to answer only from them ([withPassages]); the
 *    reply ends with a sources line the code writes ([footer]).
 *  * Not found: [NOT_COVERED], with no model at all.
 *  * A question about the screen gets [SCREENSHOT_NOTE]: this phone can't
 *    read pictures yet, and nothing is sent anywhere.
 *
 * Pure, so the JVM tests run it against the repo's own manual file.
 */
object LocalHelp {
    const val NOT_COVERED = "The manual doesn't cover that."

    const val SCREENSHOT_NOTE =
        "This phone can't read screenshots yet. A home-server chat can: start one and attach the screenshot there " +
            "(it stays on your home server)."

    const val MAX_PASSAGES = 3

    /** About the length of a passage the model is given, characters. */
    const val PASSAGE_CHARS = 480

    private val ASKING = Regex(
        """\b(how (do|can|should|would|could|does|did) (i|you|we|it)|how to|how come|where (is|are|do|does|can|did)|""" +
            """where's|why (is|isn't|isnt|are|aren't|does|doesn't|doesnt|do|don't|won't|wont|can't|cant|did|didn't)|""" +
            """what (does|do|is) (the|a|this|my)|is there a way|can i|help (me )?with|i can't|i cant|""" +
            """it (won't|wont|doesn't|doesnt|isn't|isnt)|not working|stopped working)\b""",
    )

    /** Words that make a question about Domovoi rather than the world. */
    private val DOMOVOI_WORDS = setOf(
        "domovoi", "room", "rooms", "app", "dashboard", "plugin", "plugins", "setting", "settings", "timer", "timers",
        "music", "wake", "phone", "chat", "chats", "satellite", "satellites", "speaker", "voice", "voices", "manual",
        "server", "queue", "playlist", "podcast", "podcasts", "audiobook", "audiobooks", "radio", "station", "stations",
        "reminder", "reminders", "intercom", "announce", "announcement", "pair", "pairing", "update", "upgrade",
        "admin", "people", "profile", "screen", "page", "screenshot", "notification", "notifications", "files",
        "video", "videos", "library", "microphone", "mic", "tablet", "backup", "container", "containers", "drop-in",
        "dropin", "model", "assistant",
    )

    private val SCREEN = Regex("""\b(screens?|screenshots?|screen shot|this page|on the page|on my page|error message|pop-?up|toast)\b""")

    private val STOP = setOf(
        "how", "do", "does", "did", "i", "to", "a", "an", "the", "is", "isn", "isnt", "isn't", "are", "aren't", "where",
        "where's", "why", "what", "when", "which", "who", "can", "can't", "cant", "could", "would", "should", "my", "me",
        "it", "its", "it's", "this", "that", "these", "those", "in", "on", "of", "for", "with", "and", "or", "not", "no",
        "doesn't", "doesnt", "don't", "dont", "won't", "wont", "didn't", "there", "you", "your", "we", "our", "from",
        "at", "by", "be", "been", "am", "was", "were", "so", "if", "then", "any", "some", "get", "got", "way", "come",
        "please", "help", "domovoi", "just", "again", "now", "here", "will", "want", "need", "about", "up", "out",
        "one", "all", "into", "has", "have", "s", "t",
    )

    private val WORD = Regex("""[a-z0-9]+(?:['’-][a-z0-9]+)*""")

    /** "how do I…" (and the like) together with a word about Domovoi. */
    fun isHelpQuestion(text: String): Boolean {
        val t = text.lowercase()
        if (!ASKING.containsMatchIn(t)) return false
        return words(t).any { it in DOMOVOI_WORDS } || "wake word" in t || "home server" in t
    }

    /** The question is about something on the screen. */
    fun mentionsScreen(text: String): Boolean = SCREEN.containsMatchIn(text.lowercase())

    private fun words(t: String): List<String> = WORD.findAll(t.lowercase()).map { it.value.replace('’', '\'') }.toList()

    /** A light stem, so "rooms" finds "room" and "playing" "play". */
    internal fun stem(w: String): String {
        var s = w.removeSuffix("'s")
        s = when {
            s.length > 4 && s.endsWith("ies") -> s.dropLast(3) + "y"
            s.length > 5 && s.endsWith("ing") -> s.dropLast(3)
            s.length > 4 && s.endsWith("ed") -> s.dropLast(2)
            s.length > 3 && s.endsWith("s") && !s.endsWith("ss") -> s.dropLast(1)
            else -> s
        }
        return s
    }

    /** The question's words that say what it is about, stemmed, once each. */
    internal fun contentWords(question: String): List<String> =
        words(question).filter { it !in STOP }
            .flatMap { it.split(Regex("""[^a-z0-9]+""")) }
            .filter { it.length > 1 && it !in STOP }
            .map(::stem).distinct()

    /**
     * The best pieces for [question], at most [max]: a piece counts only when
     * MORE than half the question's content words are in it (its title,
     * subtitle, search words, topic and text), so "how do I bake bread"
     * finds nothing however much the manual says about rooms. Ranked BM25
     * style, with the title and the search words weighing more than the text.
     */
    fun search(index: HelpIndex, question: String, max: Int = MAX_PASSAGES): List<HelpPassage> {
        val q = contentWords(question)
        if (q.isEmpty()) return emptyList()
        val n = index.entries.size.toDouble()
        val avg = index.entries.map { it.length }.average().takeIf { it > 0 } ?: 1.0
        return index.entries.mapNotNull { e ->
            val matched = q.filter { it in e.all }
            if (matched.size * 2 <= q.size) return@mapNotNull null
            val score = matched.sumOf { w ->
                val df = index.docFreq[w] ?: 1
                val idf = ln(1 + (n - df + 0.5) / (df + 0.5))
                val tf = (e.body[w] ?: 0).toDouble()
                val bm25 = tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * e.length / avg))
                idf * (bm25 + (if (w in e.title) 2.0 else 0.0) + (if (w in e.keys) 1.5 else 0.0))
            }
            // A search word of the piece said whole ("add a room"): the page
            // is about exactly that.
            val phrase = e.phrases.filter { it.size >= 2 && q.containsAll(it) }.maxOfOrNull { it.size } ?: 0
            e to score + 3.0 * phrase
        }
            .sortedByDescending { it.second }
            .take(max)
            .map { it.first.passage }
    }

    /** What to do with a phone-chat question; null: not a help question (or
     *  no manual on the phone), so the model answers as ever. */
    fun plan(index: HelpIndex?, question: String): HelpTurn? {
        if (index == null || !isHelpQuestion(question)) return null
        return HelpTurn(search(index, question), mentionsScreen(question))
    }

    /** The block the model is given ahead of the question. */
    fun block(passages: List<HelpPassage>): String = buildString {
        append("[From the Domovoi manual. Answer only from these; if they don't answer it, say exactly: ")
        append(NOT_COVERED)
        append("]\n")
        passages.forEachIndexed { i, p -> append("${i + 1}. ${p.topic} → ${p.title}: ${p.text}\n") }
        append("[End of the manual]")
    }

    /** [turns] with the manual's block ahead of the last (the question). */
    fun withPassages(turns: List<ChatTurn>, turn: HelpTurn): List<ChatTurn> {
        if (turns.isEmpty() || turn.passages.isEmpty() || !turns.last().fromUser) return turns
        val q = turns.last()
        return turns.dropLast(1) + q.copy(text = "${block(turn.passages)}\n\nQuestion: ${q.text}")
    }

    /** "From: Rooms and devices → A Raspberry Pi room", once per piece. */
    fun sources(passages: List<HelpPassage>): String =
        "From: " + passages.distinctBy { it.id }.joinToString("; ") { "${it.topic} → ${it.title}" }

    /** What goes under the model's [reply]: the sources (not when it said
     *  the manual doesn't cover it) and the screenshot note. Empty: none. */
    fun footer(turn: HelpTurn, reply: String): String {
        val lines = mutableListOf<String>()
        if (turn.passages.isNotEmpty() && !reply.contains("manual doesn't cover", ignoreCase = true) &&
            !reply.contains("manual doesn’t cover", ignoreCase = true)
        ) lines += sources(turn.passages)
        if (turn.screen) lines += SCREENSHOT_NOTE
        return lines.joinToString("\n\n")
    }

    /** The whole reply when nothing was found: no model. */
    fun notCovered(turn: HelpTurn): String =
        if (turn.screen) "$NOT_COVERED\n\n$SCREENSHOT_NOTE" else NOT_COVERED
}

/** A help question, planned: what was found (empty: nothing) and whether
 *  it asks about the screen. */
data class HelpTurn(val passages: List<HelpPassage>, val screen: Boolean)

/** One manual piece as the model is given it. */
data class HelpPassage(val id: String, val topic: String, val title: String, val text: String)

/** The manual's pieces, ready to search ([LocalHelp.search]). */
class HelpIndex internal constructor(internal val entries: List<Entry>) {
    internal class Entry(
        val passage: HelpPassage,
        val title: Set<String>,
        val keys: Set<String>,
        val body: Map<String, Int>,
        val length: Int,
        /** Each search word's content words ("add a room" -> add, room). */
        val phrases: List<Set<String>> = emptyList(),
    ) {
        val all: Set<String> = title + keys + body.keys
    }

    internal val docFreq: Map<String, Int> =
        entries.flatMap { it.all }.groupingBy { it }.eachCount()

    val size: Int get() = entries.size

    companion object {
        private val STRIP = listOf(
            Regex("""\[([^\]]+)\]\((?:route|node|doc):[^)]+\)""") to "$1",
            Regex("""\*\*([^*]+)\*\*""") to "$1",
            Regex("""\*([^*\s][^*]*)\*""") to "$1",
            Regex("""`([^`]+)`""") to "$1",
            Regex("""\{admin\}""") to "(admin)",
            Regex("""\{(?:preview|plugin)\}""") to "",
        )

        /** The manual's inline markup as plain words; {wake} is [wake]. */
        internal fun plain(text: String, wake: String): String {
            var s = text.replace("{wake}", "“$wake”")
            STRIP.forEach { (re, to) -> s = re.replace(s, to) }
            return s.replace(Regex("""\s+"""), " ").trim()
        }

        private fun blockText(b: ManualBlock, wake: String): String? {
            if (!shownInApp(b.only)) return null
            return when (b.type) {
                "p" -> plain(b.text, wake)
                "label" -> null
                "list" -> b.items.filter { shownInApp(it.only) }
                    .mapIndexed { i, it -> (if (b.ordered) "${i + 1}) " else "- ") + plain(it.text, wake) }
                    .joinToString(" ")
                "say" -> b.phrases.filter { it.pending == null }.joinToString(", ", prefix = "Say: ") { "“${it.text}”" }
                    .takeIf { b.phrases.any { it.pending == null } }
                "table" -> (listOf(b.header) + b.rows).joinToString(" ") { row -> row.joinToString(": ") { plain(it, wake) } }
                else -> null
            }
        }

        /** A passage of about [LocalHelp.PASSAGE_CHARS], cut at a sentence. */
        internal fun shorten(text: String, max: Int = LocalHelp.PASSAGE_CHARS): String {
            if (text.length <= max) return text
            val cut = text.take(max)
            val end = cut.lastIndexOfAny(charArrayOf('.', '!', '?'))
            return if (end > max / 2) cut.take(end + 1) else cut.trimEnd() + "…"
        }

        private fun tokens(s: String): List<String> =
            Regex("""[a-z0-9]+""").findAll(s.lowercase()).map { LocalHelp.stem(it.value) }.toList()

        /** Every piece of [doc] the app shows (not a plugin's, not an alias). */
        internal fun of(doc: ManualDoc): HelpIndex {
            val wake = doc.wakeWord.names[doc.wakeWord.default] ?: doc.wakeWord.default
            val entries = doc.pieces.filter { it.plugin == null && it.aliasOf == null && it.title.isNotBlank() }.map { p ->
                val topic = doc.topic(p.topic)?.title ?: p.topic
                val text = p.blocks.mapNotNull { blockText(it, wake) }.filter { it.isNotBlank() }.joinToString(" ")
                val full = listOf(p.subtitle.takeIf { it.isNotBlank() }?.let { plain(it, wake).replaceFirstChar(Char::uppercaseChar) + "." }, text)
                    .filterNotNull().joinToString(" ")
                val bodyTokens = tokens("$full $topic")
                Entry(
                    passage = HelpPassage(p.id, topic, p.title, shorten(full)),
                    title = tokens(p.title).toSet(),
                    keys = (p.keywords.flatMap(::tokens) + tokens(p.subtitle)).toSet(),
                    body = bodyTokens.groupingBy { it }.eachCount(),
                    length = bodyTokens.size,
                    phrases = p.keywords.map { LocalHelp.contentWords(it).toSet() },
                )
            }
            return HelpIndex(entries)
        }

        fun parse(json: String): HelpIndex? = ManualDoc.parse(json)?.let(::of)
    }
}

/** The manual the app carries (assets/manual/manual-content.json), read
 *  once on first use; null when this build has none. */
class BundledManual(context: Context) {
    private val app = context.applicationContext
    private val lock = Mutex()
    @Volatile private var loaded = false
    @Volatile private var index: HelpIndex? = null

    suspend fun index(): HelpIndex? = lock.withLock {
        if (!loaded) {
            index = withContext(Dispatchers.IO) {
                runCatching { app.assets.open(ASSET).use { it.readBytes().toString(Charsets.UTF_8) } }
                    .getOrNull()?.let(HelpIndex::parse)
            }
            loaded = true
        }
        index
    }

    companion object {
        const val ASSET = "manual/manual-content.json"
    }
}
