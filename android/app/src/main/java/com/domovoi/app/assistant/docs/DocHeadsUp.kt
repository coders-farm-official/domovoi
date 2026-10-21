package com.domovoi.app.assistant.docs

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.util.Locale
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min

/**
 * The honest heads-up (documents plan part 5, D3): a short card before a
 * shaky result. The Kotlin twin of the server's `domovoi/doc_qa/headsup.py`,
 * held to the same results by `headsup_cases.json` (DocHeadsUpSharedCasesTest).
 *
 * **The app decides, never the model.** A request is matched against the
 * measured weak spots in `chat-weak-spots.json` (the server's file, copied
 * into the app's assets at build time: [WeakSpots]): a kind of task
 * recognised from the wording and the documents alone, in a band measured
 * below the bar, gets a card naming the weakness, with only the options that
 * work here ([HeadsUpRequest.can]). [decide] applies the nag rules: once per
 * chat per kind, "don't warn me about this again" per kind and the master
 * switch (both kept on this phone: [HeadsUpStore]); silenced, exact sums and
 * scanned pages ([NOTE_KINDS]) become a one-line note under the answer.
 *
 * [mapChunks] is kept for its D1 callers (map/reduce is [DocLong]'s).
 */
object DocHeadsUp {
    val DOC_KINDS = listOf("scanned", "language", "calc", "compare", "rewrite", "long")
    val PICTURE_KINDS = listOf("blurry", "handwriting")
    val KINDS = DOC_KINDS + PICTURE_KINDS
    val NOTE_KINDS = setOf("calc", "scanned")
    val FIRES = setOf("not_reliable", "not_measured", "slow")
    val ALWAYS = setOf("try_anyway", "go_ahead", "send_anyway", "choose_another")
    val CALCULATOR_OPS = setOf("sum", "average", "difference", "days")
    val NOTHING_FOUND_KINDS = setOf("rewrite", "language", "scanned")
    val LANGUAGE_NAMES = mapOf(
        "en" to "English", "es" to "Spanish", "fr" to "French", "de" to "German", "it" to "Italian",
        "pt" to "Portuguese", "nl" to "Dutch", "pl" to "Polish",
    )

    // ── Wording, recognised without a model ─────────────────────────────

    /** The question as matched: plain, lowercase, curly quotes straight, single spaces. */
    internal fun q(text: String?): String =
        DocText.pySplit(DocText.normalize(text).lowercase(Locale.ROOT).replace('’', '\'').replace('‘', '\''))
            .joinToString(" ")

    private fun re(p: String) = pyRegex(p)

    private val REWRITE_RE = re(
        """\b(rewrite|re-write|rephrase|reword|paraphrase|translate|proofread|proof-read|proof read|""" +
            """reformat|re-format|redraft|copyedit|copy-edit|polish up|tidy up|simplify)\b""" +
            """|\bmake (it|this|the (whole )?\w+) """ +
            """(shorter|longer|simpler|clearer|plainer|friendlier|more \w+|less \w+)\b""" +
            """|\b(fix|correct|check) (the |its |all (the )?)?(grammar|spelling|typos|punctuation)\b""" +
            """|\bput (it|this) (in|into) (plain|simple|simpler|everyday) (english|spanish|words|language)\b""" +
            """|\bturn (it|this|the (whole )?(document|letter|file|text)) into\b""",
    )
    private val PART_RE = re(
        """\b(paragraph|paragraphs|sentence|sentences|line|lines|clause|bullet|heading|title|caption|""" +
            """(section|page|part|chapter|article|item) \d+|p\. ?\d+|""" +
            """(first|last|second|third|opening|closing|final) (part|paragraph|section|page|line|sentence|bit))\b""",
    )
    private val COMPARE_RE = re(
        """\b(compare|compares|comparing|comparison|contrast|differ|differs|different from|differences|""" +
            """contradict\w*|consistent|inconsisten\w*|throughout|anywhere else|elsewhere in|""" +
            """every (mention|time|place|instance|reference|section|page)|""" +
            """each (mention|time|place|instance|reference)|""" +
            """all (the )?(mentions|references|instances|places)|changed? from|versus|vs\.?)(?=\W|$)""" +
            """|\bdifference between\b""",
    )
    private val PLACE_RE = re("""\b(page|p\.|pg\.?|section|part|chapter|clause|article|sheet) ?(\d+)\b""")
    private val DAYS_RE = re(
        """\bhow many (days|weeks|months|years)\b.*\b(between|from|until|till|since|before|after)\b""" +
            """|\b(days|weeks|months) between\b|\bhow long (is it )?between\b""",
    )
    private val AVERAGE_RE = re("""\baverage\b|\bthe mean\b|\bmean (of|value|amount|cost|price|bill|score)\b""")
    private val PERCENT_RE = re("""\b(percent|percentage|per cent)\b|%""")
    private val DIFF_RE = re(
        """\bhow much (more|less|higher|lower|bigger|smaller|cheaper|dearer)\b|\bhow many more\b""" +
            """|\bhow much did .{1,40} (go up|go down|rise|fall|increase|decrease|change)\b""",
    )
    private val DIFF_BETWEEN_RE = re("""\bdifference (between|in)\b""")
    private val QUANTITY_RE = re(
        """\b(how much|amount|amounts|cost|costs|price|prices|fee|fees|rent|pay|paid|""" +
            """charge|charges|total|number|balance|salary|wage|wages|deposit|bill)\b|\$|€|£""",
    )
    private val SUM_RE = re(
        """\b(total|totals|sum|add up|added up|adds up|add together|added together|altogether|""" +
            """in all|combined|grand total|all together)\b""",
    )
    private val NUMBER_RE = re("""(?<![\w.,])[-$€£]?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|(?<![\w.,])[-$€£]?\d+(?:\.\d+)?%?""")
    private val LOOKUP_WORDS = mapOf(
        "sum" to "(total|totals|sum|altogether|combined|in all|grand total)",
        "average" to "(average|mean)",
        "percent" to "(percent|percentage|per cent|%)",
    )
    private val READING_RE = re(
        """\b(read|reads|say|says|said|text|transcribe|transcription|letter|document|page|words|written|writing|""" +
            """note|receipt|label|sign|form|bill|invoice|translate|spell|spelled|handwriting|handwritten)\b""",
    )

    fun readingRequest(question: String): Boolean = READING_RE.containsMatchIn(q(question))

    fun rewriteRequest(question: String): Boolean {
        val s = q(question)
        return REWRITE_RE.containsMatchIn(s) && !PART_RE.containsMatchIn(s)
    }

    fun namedPlaces(question: String): List<String> {
        val out = ArrayList<String>()
        for (m in PLACE_RE.findAll(q(question))) {
            var kind = m.groupValues[1].trimEnd('.')
            if (kind == "p" || kind == "pg" || kind == "page") kind = "page"
            val label = "$kind ${m.groupValues[2]}"
            if (label !in out) out += label
        }
        return out
    }

    fun compareRequest(question: String): Boolean =
        COMPARE_RE.containsMatchIn(q(question)) || namedPlaces(question).size >= 2

    fun calcRequest(question: String): String? {
        val s = q(question)
        return when {
            DAYS_RE.containsMatchIn(s) -> "days"
            AVERAGE_RE.containsMatchIn(s) -> "average"
            PERCENT_RE.containsMatchIn(s) -> "percent"
            DIFF_RE.containsMatchIn(s) || (DIFF_BETWEEN_RE.containsMatchIn(s) && QUANTITY_RE.containsMatchIn(s)) -> "difference"
            SUM_RE.containsMatchIn(s) -> "sum"
            else -> null
        }
    }

    fun numbersIn(text: String?): List<String> = NUMBER_RE.findAll(DocText.normalize(text)).map { it.value }.toList()

    fun isLookup(op: String, texts: List<String>): Boolean {
        val words = LOOKUP_WORDS[op] ?: return false
        val pat = if (op != "percent") re("""\b$words\b[^.\n]{0,40}?\d|\d[^.\n]{0,12}?\b$words\b""")
        else re("""\d\s?(%|percent|per cent)""")
        return texts.any { pat.containsMatchIn(q(it)) }
    }

    // ── Language: letters and stop words, our own code ──────────────────

    private val STOP: Map<String, Set<String>> = mapOf(
        "en" to ("the and of to is in that it for you with as on are this be at by not or from have was " +
            "will your an they which but if we has can all their would there been any shall must"),
        "es" to ("el los las del que y en por para con una es se no su al lo como más pero sus le ya " +
            "este esta entre cuando muy sin sobre también hasta hay donde quien desde todo nos " +
            "durante ese eso usted"),
        "fr" to ("le les des et est une du dans pour pas qui sur au avec ce il elle sont ou aux ne mais " +
            "nous vous leur été cette être fait comme plus tout si ses aussi"),
        "de" to ("der die das und ist nicht den mit von zu des sich auf für dem ein eine im sie es auch " +
            "als an wird werden bei oder nach aus sind wenn kann nur über einen einer wir ihr sein"),
        "it" to ("il di che è per non una sono gli della del le si con da nel alla anche ma più come " +
            "questo essere dei delle ha al ci se lo suo tra molto quando sua"),
        "pt" to ("o os as não uma do da dos das em para com é que por se mais na no ao como mas foi ele " +
            "ela são seu sua também ou quando muito já está isso pelo pela você"),
        "nl" to ("de het een en van is dat op te in zijn voor met niet aan er ook als bij maar om nog " +
            "wordt worden dit uit naar heeft kan zo deze wel geen door hun u"),
        "pl" to ("się nie na jest że do to jak ale po co tak za od przez dla jego czy jej tylko oraz być " +
            "który która które są ten ta już może będzie tym także lub gdy ich było jako"),
    ).mapValues { (_, v) -> v.split(' ').toSet() }
    private val LANG_ORDER = listOf("en", "es", "fr", "de", "it", "pt", "nl", "pl")
    private val WORD_RE = Regex("""[\p{L}\p{Nl}\p{No}]+""")
    const val LANG_MIN_WORDS = 25
    const val LANG_MIN_SHARE = 0.12
    const val LANG_LEAD = 1.5

    fun guessLanguage(text: String?): String? {
        val words = WORD_RE.findAll(DocText.normalize(text).lowercase(Locale.ROOT)).map { it.value }.toList()
        if (words.size < LANG_MIN_WORDS) return null
        val counts = LANG_ORDER.associateWith { lang -> words.count { it in STOP.getValue(lang) } }
        val ranked = LANG_ORDER.sortedWith(compareBy({ -counts.getValue(it) }, { LANG_ORDER.indexOf(it) }))
        val best = ranked[0]
        val second = ranked[1]
        if (counts.getValue(best) < LANG_MIN_SHARE * words.size) return null
        if (counts.getValue(best) < LANG_LEAD * counts.getValue(second)) return null
        return best
    }

    /** The first [limit] characters of a document's text, for the language guess. */
    fun docSample(parts: List<DocPart>, limit: Int = 3000): String {
        val out = ArrayList<String>()
        var used = 0
        for (p in parts) {
            val t = p.text
            if (t.isEmpty()) continue
            out += t.take(max(0, limit - used))
            used += out.last().length + 1
            if (used >= limit) break
        }
        return out.joinToString("\n").take(limit)
    }

    // ── Pictures ────────────────────────────────────────────────────────

    /** The variance of the Laplacian of a grey image (rows of 0-255): low is
     *  blurry. Infinity for an image too small to measure. */
    fun blurVariance(grey: List<IntArray>): Double {
        val h = grey.size
        val w = if (h > 0) grey[0].size else 0
        if (h < 3 || w < 3) return Double.POSITIVE_INFINITY
        var n = 0
        var s = 0.0
        var s2 = 0.0
        for (y in 1 until h - 1) {
            val up = grey[y - 1]
            val row = grey[y]
            val down = grey[y + 1]
            for (x in 1 until w - 1) {
                val v = (up[x] + down[x] + row[x - 1] + row[x + 1] - 4 * row[x]).toDouble()
                n++
                s += v
                s2 += v * v
            }
        }
        val mean = s / n
        return s2 / n - mean * mean
    }

    // ── The cards ───────────────────────────────────────────────────────

    private fun listAnd(items: List<String>): String =
        if (items.size <= 1) items.joinToString("") else items.dropLast(1).joinToString(", ") + " and " + items.last()

    private fun grouped(n: Int): String = String.format(Locale.US, "%,d", n)

    private fun roundWords(w0: Int): Int = if (w0 >= 1000) floor(w0 / 100.0 + 0.5).toInt() * 100 else w0

    private fun size(d: HeadsUpDoc): String {
        if (d.pages != null && d.pages != 0) return "${d.pages} page${if (d.pages == 1) "" else "s"}"
        val w = roundWords(if (d.words != 0) d.words else max(1, d.chars / 6))
        return "about ${grouped(w)} words"
    }

    private fun sizeWords(docs: List<HeadsUpDoc>): String =
        "${grouped(roundWords(docs.sumOf { if (it.words != 0) it.words else max(1, it.chars / 6) }))} words"

    private fun scannedWords(pages: List<Int>): Map<String, String> {
        val n = pages.size
        if (n == 1) return mapOf("scanned" to "Page ${pages[0]}", "are_pictures" to "is a picture", "were_not" to "wasn't")
        val words = if (n > 5) "$n pages" else "Pages " + listAnd(pages.map { it.toString() })
        return mapOf("scanned" to words, "are_pictures" to "are pictures", "were_not" to "weren't")
    }

    private fun options(kindOpts: List<String>, can: Set<String>): List<String> =
        kindOpts.filter { it in ALWAYS || it in can }.ifEmpty { listOf("try_anyway") }

    private fun fmt(template: String, values: Map<String, Any>): String {
        var out = template
        for ((k, v) in values) out = out.replace("{$k}", v.toString())
        return out
    }

    private fun placeIn(place: String, label: String): Boolean {
        val kind = place.substringBefore(' ')
        val num = Regex.escape(place.substringAfter(' '))
        return if (kind == "page") re("""\bp\. ?$num\b""").containsMatchIn(label)
        else re("""\b${Regex.escape(kind)}\W{0,3}$num\b""").containsMatchIn(label)
    }

    /** Every kind that fires for [req], in [KINDS] order (the first is the
     *  one shown; [decide] applies the nag rules). Nothing with AI off. */
    fun findings(req: HeadsUpRequest, t: WeakSpots): List<HeadsUpCard> {
        if (!req.aiOn) return emptyList()
        val out = ArrayList<HeadsUpCard>()
        if (req.mode == "picture") {
            val blur = req.blur
            if (blur != null && blur < t.threshold("blur_variance", 0.0) && readingRequest(req.question) &&
                t.fires("blurry", "blurry", req.where)
            ) {
                val k = t.kind("blurry")
                out += HeadsUpCard("blurry", k.card, options(k.options, req.can))
            }
            if (req.handwritten && t.fires("handwriting", "handwritten", req.where)) {
                val k = t.kind("handwriting")
                out += HeadsUpCard("handwriting", k.card, options(k.options, req.can))
            }
            return out
        }
        val docs = req.docs.filter { it.chars > 0 || it.scannedPages.isNotEmpty() }
        if (docs.isEmpty()) return out
        val question = req.question
        val totalChars = docs.sumOf { it.chars }
        val onePass = req.facts.onePassChars ?: t.threshold("one_pass_chars", 4800.0).toInt()

        docs.firstOrNull { it.scannedPages.isNotEmpty() }?.let { d ->
            if (t.fires("scanned", "pictures_of_text", req.where)) {
                val k = t.kind("scanned")
                val w = scannedWords(d.scannedPages)
                out += HeadsUpCard(
                    "scanned", fmt(k.card, w + ("doc" to d.name)), options(k.options, req.can),
                    note = k.note?.let { fmt(it, w) }, noteOnly = req.mode == "question",
                )
            }
        }
        for (d in docs) {
            val lang = guessLanguage(d.sample)
            if (lang != null && lang !in t.testedLanguages && t.fires("language", "untested", req.where)) {
                val k = t.kind("language")
                out += HeadsUpCard(
                    "language", fmt(k.card, mapOf("doc" to d.name, "language" to (LANGUAGE_NAMES[lang] ?: lang))),
                    options(k.options, req.can),
                )
                break
            }
        }
        val texts = req.given.map { it.second }
        if (req.mode == "question") {
            val op = calcRequest(question)
            val nums = texts.flatMap { numbersIn(it) }
            if (op != null && nums.toSet().size >= 2 && !isLookup(op, texts) && t.fires("calc", "combine", req.where)) {
                val k = t.kind("calc")
                out += if (op in CALCULATOR_OPS && "calculate" in req.can) {
                    HeadsUpCard("calc", k.card, options(k.options, req.can), note = k.note, op = op)
                } else {
                    HeadsUpCard("calc", k.cardNoCalculator ?: k.card, listOf("try_anyway"), note = k.note, op = op)
                }
            }
            if ((op == null || op == "percent") && compareRequest(question) && totalChars > onePass) {
                val places = namedPlaces(question)
                val labels = req.given.map { q(it.first) }
                val foundAll = places.isNotEmpty() && places.all { p -> labels.any { placeIn(p, it) } }
                if (!foundAll && t.fires("compare", "long", req.where)) {
                    val k = t.kind("compare")
                    out += HeadsUpCard(
                        "compare", fmt(k.card, mapOf("k" to (req.given.size.takeIf { it > 0 } ?: 4))),
                        options(k.options, req.can),
                    )
                }
            }
            if (rewriteRequest(question) && totalChars > onePass && t.fires("rewrite", "over_one_pass", req.where)) {
                val k = t.kind("rewrite")
                val big = docs.maxBy { it.chars }
                val seen = max(1, min(99, floor(100.0 * onePass / max(1, totalChars) + 0.5).toInt()))
                out += HeadsUpCard(
                    "rewrite",
                    fmt(k.card, mapOf(
                        "doc" to (if (docs.size > 1) "These documents" else big.name),
                        "size" to (if (docs.size == 1) size(big) else "about ${sizeWords(docs)}"),
                        "seen" to "$seen%",
                    )),
                    options(k.options, req.can),
                )
            }
        }
        if (req.mode == "long_summary") {
            val est = req.facts.estimateS
            val home = req.facts.homeServerS
            val parts = req.facts.parts
            val slow = est != null && est >= t.threshold("long_wait_s", 180.0)
            val offer = home != null && "home_server" in req.can && est != null && est >= t.threshold("home_server_wait_s", 60.0)
            if (parts >= 2 && (slow || offer)) {
                val k = t.kind("long")
                var text = fmt(k.card, mapOf(
                    "doc" to (if (docs.size > 1) "These documents" else docs[0].name),
                    "size" to (if (docs.size == 1) size(docs[0]) else "about ${sizeWords(docs)}"),
                    "parts" to parts, "time" to DocLong.aboutTime(est),
                ))
                var opts = k.options
                if (offer) text += " " + fmt(k.homeServerLine ?: "", mapOf("home_time" to DocLong.aboutTime(home)))
                else opts = opts.filter { it != "home_server" }
                out += HeadsUpCard("long", text, options(opts, req.can))
            }
        }
        return out.map { it.copy(after = t.kind(it.kind).after) }
    }

    fun whenNothingFound(found: List<HeadsUpCard>): List<HeadsUpCard> = found.filter { it.kind in NOTHING_FOUND_KINDS }

    fun check(req: HeadsUpRequest, t: WeakSpots): HeadsUpCard? = findings(req, t).firstOrNull()

    /**
     * The nag rules over [findings]: [chosen] (kind to option) is the pick on
     * this request's card (no new card; its note when the option wasn't the
     * fix); a note-only finding is its note; a kind [shown] in this chat, in
     * [skip], or any kind while [off] shows no card (exact sums and scanned
     * pages become their note); otherwise the first kind left is the card.
     */
    fun decide(
        found: List<HeadsUpCard>,
        shown: Set<String> = emptySet(),
        skip: Set<String> = emptySet(),
        off: Boolean = false,
        chosen: Pair<String, String>? = null,
    ): HeadsUpDecision {
        var card: HeadsUpCard? = null
        val notes = ArrayList<DocNote>()
        for (c in found) {
            if (chosen != null && c.kind == chosen.first) {
                val text = if (c.kind in NOTE_KINDS) c.note else c.after
                if (text != null && chosen.second !in setOf("calculate", "sections", "home_server")) {
                    notes += DocNote("heads_up_${c.kind}", text)
                }
                continue
            }
            val silenced = off || c.noteOnly || c.kind in skip || c.kind in shown || chosen != null || card != null
            if (!silenced) {
                card = c
                continue
            }
            if (c.kind in NOTE_KINDS && c.note != null) notes += DocNote("heads_up_${c.kind}", c.note)
        }
        return HeadsUpDecision(card, if (card != null) emptyList() else notes)
    }

    // ── The D1 seam, kept for its callers ───────────────────────────────

    const val MAP_CHUNK_TOKENS = 3500
    private const val CHARS_PER_TOKEN = 4.0

    fun mapChunks(parts: List<DocPart>, tokens: Int = MAP_CHUNK_TOKENS): List<List<Int>> {
        val limit = tokens * CHARS_PER_TOKEN
        val out = ArrayList<List<Int>>()
        var cur = ArrayList<Int>()
        var size = 0.0
        parts.forEachIndexed { i, p ->
            val n = p.text.length + p.head.length
            if (cur.isNotEmpty() && size + n > limit) {
                out += cur
                cur = ArrayList()
                size = 0.0
            }
            cur += i
            size += n
        }
        if (cur.isNotEmpty()) out += cur
        return out
    }
}

/** A document as the heads-up rules see it. */
data class HeadsUpDoc(
    val name: String,
    val chars: Int,
    val pages: Int? = null,
    val words: Int = 0,
    val scannedPages: List<Int> = emptyList(),
    /** The start of its text ([DocHeadsUp.docSample]), for the language guess. */
    val sample: String = "",
) {
    companion object {
        fun of(name: String, parts: List<DocPart>, pages: Int?, words: Int, scannedPages: List<Int>) = HeadsUpDoc(
            name, parts.sumOf { it.text.length + it.head.length }, pages, words, scannedPages, DocHeadsUp.docSample(parts),
        )
    }
}

/** A long summary's facts: its parts, its estimate here, and the home
 *  server's when its offer stands; the one-pass size, when not the table's. */
data class HeadsUpFacts(
    val parts: Int = 0,
    val estimateS: Double? = null,
    val homeServerS: Double? = null,
    val onePassChars: Int? = null,
)

/** What the rules see. [mode]: question | summary | long_summary | picture;
 *  [where]: phone | server; [given]: the passages found, (label, text);
 *  [can]: options beyond the always-working ones that work here
 *  (calculate, home_server, sections). */
data class HeadsUpRequest(
    val mode: String,
    val question: String = "",
    val where: String = "phone",
    val docs: List<HeadsUpDoc> = emptyList(),
    val given: List<Pair<String, String>> = emptyList(),
    val facts: HeadsUpFacts = HeadsUpFacts(),
    val blur: Double? = null,
    val handwritten: Boolean = false,
    val can: Set<String> = emptySet(),
    val aiOn: Boolean = true,
)

data class HeadsUpCard(
    val kind: String,
    val text: String,
    val options: List<String>,
    val note: String? = null,
    val op: String? = null,
    val after: String? = null,
    val noteOnly: Boolean = false,
)

data class HeadsUpDecision(val card: HeadsUpCard?, val notes: List<DocNote>)

/** A card as a conversation keeps it (phone chats), with its pick. */
@Serializable
data class StoredHeadsUp(
    val kind: String = "",
    val text: String = "",
    /** (id, label) pairs, in order. */
    val options: List<List<String>> = emptyList(),
    val neverLabel: String = "",
    val op: String? = null,
    val chosen: String? = null,
)

/** The weak-spots table (`chat-weak-spots.json`, the server's file). */
class WeakSpots(private val root: JsonObject) {
    data class Kind(
        val title: String,
        val card: String,
        val cardNoCalculator: String?,
        val homeServerLine: String?,
        val options: List<String>,
        val note: String?,
        val after: String?,
        val instead: String,
    )

    data class Cell(val kind: String, val where: String, val band: String, val verdict: String?,
                    val pass: Double?, val source: String?)

    val bar: Double = root["bar"]?.jsonPrimitive?.doubleOrNull ?: 0.7
    val good: Double = root["good"]?.jsonPrimitive?.doubleOrNull ?: 0.85
    val testedLanguages: List<String> = root["tested_languages"]?.jsonArray?.map { it.jsonPrimitive.content }.orEmpty()
    val neverLabel: String = str(root["never_label"]) ?: "don't warn me about this again"
    val optionLabels: Map<String, String> =
        root["options"]?.jsonObject?.mapValues { it.value.jsonPrimitive.content }.orEmpty()
    private val thresholds: JsonObject = root["thresholds"]?.jsonObject ?: JsonObject(emptyMap())
    private val kinds: Map<String, Kind> = root["kinds"]?.jsonObject?.mapValues { (_, v) ->
        val o = v.jsonObject
        Kind(
            title = str(o["title"]).orEmpty(), card = str(o["card"]).orEmpty(),
            cardNoCalculator = str(o["card_no_calculator"]), homeServerLine = str(o["home_server_line"]),
            options = o["options"]?.jsonArray?.map { it.jsonPrimitive.content }.orEmpty(),
            note = str(o["note"]), after = str(o["after"]), instead = str(o["instead"]).orEmpty(),
        )
    }.orEmpty()
    val cells: List<Cell> = root["cells"]?.jsonArray?.map { e ->
        val o = e.jsonObject
        Cell(str(o["kind"]).orEmpty(), str(o["where"]).orEmpty(), str(o["band"]).orEmpty(), str(o["verdict"]),
             (o["pass"] as? JsonPrimitive)?.doubleOrNull, str(o["source"]))
    }.orEmpty()

    fun kind(name: String): Kind = kinds.getValue(name)
    fun threshold(name: String, default: Double): Double =
        (thresholds[name] as? JsonPrimitive)?.doubleOrNull ?: default
    fun label(option: String): String = optionLabels[option] ?: option

    fun cell(kind: String, band: String, where: String): Cell? =
        cells.firstOrNull { it.kind == kind && it.band == band && (it.where == where || it.where == "both") }

    fun verdict(c: Cell?): String {
        if (c == null) return "not_measured"
        val p = c.pass
        if (p != null && c.source == "eval") return when {
            p >= good -> "good"
            p >= bar -> "okay"
            else -> "not_reliable"
        }
        return c.verdict ?: "not_measured"
    }

    fun fires(kind: String, band: String, where: String): Boolean = verdict(cell(kind, band, where)) in DocHeadsUp.FIRES

    /** A card as a phone conversation keeps it. */
    fun stored(card: HeadsUpCard): StoredHeadsUp =
        StoredHeadsUp(card.kind, card.text, card.options.map { listOf(it, label(it)) }, neverLabel, card.op)

    /** This table with the cells of [overrides] ("kind/band" to pass) measured differently (the shared cases). */
    fun withPasses(overrides: Map<String, Double>): WeakSpots {
        if (overrides.isEmpty()) return this
        val cellsJson = root["cells"]?.jsonArray ?: return this
        val changed = JsonArray(cellsJson.map { e ->
            val o = e.jsonObject
            val key = "${str(o["kind"])}/${str(o["band"])}"
            val v = overrides[key]
            if (v == null) e else JsonObject(o + ("pass" to JsonPrimitive(v)))
        })
        return WeakSpots(JsonObject(root + ("cells" to changed)))
    }

    companion object {
        /** The app's copy of the table, in its assets. */
        const val ASSET = "docs/chat-weak-spots.json"

        fun parse(text: String): WeakSpots = WeakSpots(Json.parseToJsonElement(text).jsonObject)

        private fun str(e: JsonElement?): String? = when (e) {
            null, is JsonNull -> null
            is JsonPrimitive -> if (e.isString) e.content else e.booleanOrNull?.toString() ?: e.content
            else -> null
        }
    }
}
