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
    private val REVISE = Regex(
        "^(?:make (?:it|this|that|the [a-z]+(?: [a-z]+)?) |shorter\\b|longer\\b|shorten\\b|lengthen\\b|expand\\b|" +
            "tighten\\b|simplify\\b|condense\\b|trim\\b|cut\\b|remove\\b|delete\\b|drop\\b|add\\b|include\\b|mention\\b|put\\b|" +
            "insert\\b|change\\b|replace\\b|swap\\b|use\\b|reword\\b|rephrase\\b|rewrite\\b|redo\\b|fix\\b|correct\\b|" +
            "tone (?:it )?(?:down|up)\\b|more\\b|less\\b|too\\b|sound\\b|say\\b|move\\b|switch\\b|start with\\b|end with\\b|" +
            "open with\\b|don't\\b|do not\\b|no need\\b|instead\\b|turn\\b|keep it\\b|try again\\b|another version\\b|" +
            "(?:it|that|this)(?:'s| is) (?:too|a bit|a little|not)\\b|(?:sign|address) it\\b)",
    )
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

    fun revisionRequest(text: String): Boolean {
        val s = normalize(text)
        return s.isNotEmpty() && REVISE.find(s) != null
    }

    fun printRequest(text: String): Boolean = PRINT.find(normalize(text)) != null

    fun titleFor(kindLabel: String, topic: String): String {
        val t = topic.trim()
        return if (t.isNotEmpty()) "$kindLabel $t".trim().take(120) else kindLabel
    }
}
