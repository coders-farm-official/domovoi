package com.domovoi.app.assistant.drafts

import java.text.Normalizer

/** A message asking for a draft: its kind, template and what it is for
 *  ("for the warehouse job at Acme", in the person's own capitals). */
data class DraftAsk(val kind: String, val template: String, val topic: String)

/**
 * What in a phone chat's message asks for a draft, for a revision of the
 * chat's draft, or to print it: a small phrase list with no model involved,
 * the twin of the server's `domovoi/drafts/phrases.py` (shared_cases.json
 * holds both to the same answers).
 */
object DraftPhrases {
    private val POLITE = Regex(
        "^(?:(?:ok|okay|now|and|also|great|thanks|thank you|please|hey domovoi|domovoi|so|then|right)[,.!]* )*" +
            "(?:(?:can|could|would|will) you (?:please )?|i(?:'d| would) like you to |i (?:want|need) you to |please )?" +
            "(?:please )?",
    )
    private val QUESTION = Regex(
        "^(?:how|what|why|when|where|which|who|whom|whose|should|is|are|was|were|do|does|did|" +
            "can i|could i|tips|any tips|advice)\\b",
    )
    private val DRAFT = Regex(
        "^(?:help me (?:with |to )?)?(?:(?:write|draft|compose|create|make|prepare|put together|type up|start|do|" +
            "redo|i need|i want|i(?:'d| would) like) )?(?:me |up )?(?:(?:a|an|my|the|our) )?(?:new )?(?<what>[a-z' -]{0,40}?)" +
            "\\b(?<obj>resume|cv|cover letter|letter)\\b(?<rest>.*)$",
    )
    private val VERB = Regex(
        "^(?:help me\\b|write\\b|draft\\b|compose\\b|create\\b|make\\b|prepare\\b|put together\\b|type up\\b|" +
            "start\\b|do\\b|redo\\b|i need\\b|i want\\b|i(?:'d| would) like\\b|(?:a|an|my|the|our|new) )",
    )
    private val CHANGE_AFTER = Regex(
        "^ ?(?:shorter|longer|more|less|better|clearer|simpler|friendlier|nicer|sound|formal|informal|casual)\\b",
    )
    private val PERSONAL = Regex(
        "\\b(?:personal|landlord|neighbou?rs?|school|teacher|friend|family|grandma|grandpa|" +
            "grandmother|grandfather|mom|mum|dad|aunt|uncle)\\b",
    )
    private val NOT_A_DOCUMENT = Regex("\\b(?:email|e-mail|text message|text|song|poem|story|essay|newsletter|post|tweet)\\b")
    /** An editing verb, at the start of the message. */
    private const val EDIT_VERB = "(?:make|shorten|lengthen|rewrite|reword|rephrase|redo|revise|edit|tighten|simplify|condense|" +
        "trim|expand|polish|proofread|fix|change|update|add|remove|delete|cut|drop|mention|include|insert|" +
        "put|use|replace|swap|move|sign|address|tone|start|end|open|close|say|keep|turn|give|write)"

    /** The draft, named. */
    private const val DRAFT_NOUN = "(?:the|my|this|that|your|our) (?:draft|letter|cover letter|resume|cv|document)"
    private val NAMES_DRAFT = Regex(
        "^" + EDIT_VERB + "\\b.*\\b" + DRAFT_NOUN + "\\b" +
            "|^" + DRAFT_NOUN + " (?:is|was|sounds|reads|seems|feels)\\b" +
            "|^(?:shorten|lengthen|rewrite|reword|rephrase|redo|revise|edit|tighten|simplify|condense|" +
            "trim|polish|proofread) " + DRAFT_NOUN + "\\b",
    )

    /** How a draft can sound ("make it louder" is the music's, "make it
     *  warmer" the thermostat's, never the letter's). */
    private const val STYLE = "(?:formal|informal|casual|friendly|friendlier|professional|polite|concise|detailed|direct|" +
        "confident|enthusiastic|personal|specific|upbeat|serious|simple|simpler|clear|clearer|" +
        "short|shorter|long|longer|brief|briefer|tight|tighter|persuasive|positive|firm|firmer|gentle|gentler|" +
        "modern|plain|punchy|human|wordy|stiff|verbose|repetitive|blunt)"

    /** A place or a destination: about the house, a list, the calendar or a
     *  person, never the draft (review round 2). */
    private val ELSEWHERE = Regex(
        "\\b(?:in|on|to|into|onto|off|at) (?:the |my |our |your |this |that )?" +
            "(?:shopping list|grocery list|to-?do list|list|calendar|cart|basket|bedroom|bathroom|kitchen|living room|" +
            "dining room|office|garage|house|home|room|here|there|reminders?|notes?|playlist|queue|phone|fridge)\\b" +
            "|\\b(?:in|out) here\\b|\\bto (?:mom|mum|dad|grandma|grandpa|him|her|them)\\b",
    )

    /** What a complaint right after the draft may be about: the writing,
     *  never the room ("it's too cold") or a thing ("it's not working"). */
    private const val WRITING_FAULT = "(?:long|short|formal|informal|casual|wordy|stiff|blunt|verbose|repetitive|vague|generic)"

    /** "it"/"this" as the object of an editing verb. */
    private val EDITS_IT = Regex(
        "^(?:(?:shorten|lengthen|rewrite|reword|rephrase|redo|revise|edit|tighten|simplify|" +
            "condense|trim|polish|proofread) (?:it|this)\\b" +
            "|make (?:it|this) (?:sound )?(?:a bit |a little |much |even )?(?:(?:more|less) )?" + STYLE + "\\b" +
            "|tone (?:it|this) (?:down|up)\\b|sign (?:it|this) |address (?:it|this) to )",
    )

    /** An edit-only instruction: the whole message, right after the draft turn. */
    private val EDIT_ONLY = Regex(
        "^(?:(?:a bit |a little |much |even )?(?:shorter|longer|simpler|clearer|friendlier|nicer|tighter)" +
            "|(?:a bit |a little |much |even )?(?:more|less) " + STYLE + "(?: and (?:more|less) " + STYLE + ")?" +
            "|(?:(?:it|that|this)(?:'s| is) )?(?:too|way too|a bit too|a little too|a bit|a little) " + WRITING_FAULT +
            "|tone it (?:down|up)|(?:shorten|tighten|simplify|condense|polish|proofread|reword|rephrase|rewrite|redo)" +
            "(?: it)?|(?:fix|check) the (?:typos|spelling|grammar)|another version|one more version" +
            "|mention (?!(?:it|this|him|her|them)\\b|that$)[a-z0-9 ,.'-]+|sign it [a-z ,.'-]+|address it to [a-z ,.'-]+)$",
    )
    /** A polite ending ("shorter please", "more formal, thanks"); the polite
     *  opening goes in [normalize]. */
    private val POLITE_END = Regex("(?:,? (?:please|thanks|thank you))+$")
    private val PRINT = Regex(
        "^print(?: (?:it|this|that|out|it out|this out|that out|a copy|me a copy|" +
            "(?:the|my) (?:draft|letter|resume|cv|cover letter|document)))?$",
    )
    private val TOPIC_LEAD = Regex("^(?:,|that|which|please)\\s*", RegexOption.IGNORE_CASE)
    private val SPACES = DraftSpace.RUNS

    private fun pyRstrip(s: String, chars: String): String = s.trimEnd { it in chars }

    private fun collapse(s: String) = s.split(SPACES).filter { it.isNotEmpty() }.joinToString(" ")

    /** Lowercase, accents and curly quotes plain, spaces collapsed, the end
     *  punctuation and the polite opening dropped. */
    fun normalize(text: String): String {
        val nfkd = Normalizer.normalize(text, Normalizer.Form.NFKD)
        val plain = StringBuilder()
        var i = 0
        while (i < nfkd.length) {
            val cp = nfkd.codePointAt(i)
            i += Character.charCount(cp)
            val t = Character.getType(cp)
            if (t == Character.NON_SPACING_MARK.toInt() || t == Character.COMBINING_SPACING_MARK.toInt() ||
                t == Character.ENCLOSING_MARK.toInt()
            ) continue
            plain.appendCodePoint(cp)
        }
        var s = plain.toString().replace('’', '\'').replace('‘', '\'').lowercase()
        s = collapse(s)
        s = pyRstrip(s, " .!?")
        var prev: String? = null
        while (prev != s) {
            prev = s
            s = POLITE.replaceFirst(s, "").trim { it.isWhitespace() }
        }
        return s
    }

    fun draftRequest(text: String): DraftAsk? {
        val s = normalize(text)
        if (s.isEmpty() || QUESTION.find(s) != null || VERB.find(s) == null) return null
        val m = DRAFT.find(s) ?: return null
        val what = m.groups["what"]?.value ?: ""
        if (NOT_A_DOCUMENT.containsMatchIn(what)) return null
        val obj = m.groups["obj"]!!.value
        val kind = when (obj) {
            "resume", "cv" -> "resume"
            "cover letter" -> "cover_letter"
            else -> "letter"
        }
        val rest = m.groups["rest"]?.value ?: ""
        if (CHANGE_AFTER.find(rest) != null) return null
        var template = when (kind) {
            "resume" -> "resume-plain"
            else -> "letter-block"
        }
        if (kind == "letter" && PERSONAL.containsMatchIn("$what $rest")) template = "letter-personal"
        return DraftAsk(kind, template, topic(text, rest.codePointCount(0, rest.length)))
    }

    private fun topic(original: String, restLen: Int): String {
        val o = pyRstrip(collapse(original), " .!?")
        val total = o.codePointCount(0, o.length)
        var tail = if (restLen > 0 && restLen <= total) o.substring(o.offsetByCodePoints(0, total - restLen)).trim { it.isWhitespace() } else ""
        tail = TOPIC_LEAD.replaceFirst(tail, "")
        if (tail.length > 60) {
            val first = tail.substring(0, 60)
            val cut = if (first.contains(' ')) first.substringBeforeLast(' ') else first
            tail = cut.ifEmpty { first }
        }
        return tail
    }

    /**
     * Whether [text] asks to change the chat's draft: it names the draft,
     * or edits "it"/"this", or, when [afterDraft] (the message before it was
     * the reply that wrote or revised the draft), the whole message is an
     * edit-only instruction. Anything else is an ordinary message (review
     * round 1: better to miss a revision than to take over a request; the
     * card's Revise box revises whatever it says).
     */
    fun revisionRequest(text: String, afterDraft: Boolean = false): Boolean {
        val s = POLITE_END.replace(normalize(text), "").trimEnd(' ', ',')
        if (s.isEmpty()) return false
        if (ELSEWHERE.containsMatchIn(s)) return false
        if (NAMES_DRAFT.find(s) != null || EDITS_IT.find(s) != null) return true
        return afterDraft && EDIT_ONLY.find(s) != null
    }

    fun printRequest(text: String): Boolean = PRINT.find(normalize(text)) != null

    fun titleFor(kindLabel: String, topic: String): String {
        val t = topic.trim()
        return if (t.isNotEmpty()) "$kindLabel $t".trim().take(120) else kindLabel
    }
}
