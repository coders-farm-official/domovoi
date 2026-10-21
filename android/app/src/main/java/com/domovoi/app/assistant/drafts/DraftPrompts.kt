package com.domovoi.app.assistant.drafts

/**
 * What the phone's model is asked to write, and how its answer becomes a
 * draft: the twin of the server's `domovoi/drafts/prompts.py`, word for word
 * (shared_cases.json holds the two to the same text). The model writes
 * content into a fixed skeleton, in markdown; our templates do the layout.
 * Only the version being revised goes to the model. D0 found the small
 * model greets and adds remarks: the rules forbid both, and [clean] drops
 * what slips through. D5's guided flows pass a form's fields as `facts`.
 */
object DraftPrompts {
    const val RULES =
        "You write documents for the person, in markdown, following the skeleton exactly: the same headings " +
            "and parts, in the same order. Write only the document itself: no greeting, no introduction, no " +
            "remarks before or after it, no code fences. Use only facts the person gave. Where a fact is missing, " +
            "write a short placeholder in square brackets, like [your phone number]. Never invent names, " +
            "employers, dates, numbers, addresses or skills."

    const val REVISE_RULES =
        "You revise a document. You get the current version and one instruction. Write the whole document " +
            "again with the instruction applied. Keep every fact unless the instruction removes it, add no facts " +
            "the person didn't give, and keep the same skeleton: the same headings and parts, in the same order. " +
            "Write only the revised document, in markdown: no greeting, no remarks, no code fences."

    private const val RESUME =
        "# [Full name]\n" +
            "[City, State] · [phone] · [email]\n\n" +
            "## Summary\n" +
            "[Two or three sentences.]\n\n" +
            "## Work Experience\n" +
            "**[Job title], [Employer] — [start] to [end]**\n" +
            "- [What you did, starting with a verb]\n" +
            "- [Another one]\n\n" +
            "## Education\n" +
            "**[School] — [qualification], [year]**\n\n" +
            "## Skills\n" +
            "[Skill], [skill], [skill]\n"

    val SKELETONS: Map<String, String> = mapOf(
        "resume-plain" to RESUME,
        "letter-block" to
            "[Your name]\n[Street]\n[City, State ZIP]\n[email or phone]\n\n" +
            "[Date]\n\n" +
            "[Recipient name]\n[Organisation]\n[Street]\n[City, State ZIP]\n\n" +
            "**Re: [subject]**\n\n" +
            "Dear [name],\n\n" +
            "[Paragraph.]\n\n[Paragraph.]\n\n" +
            "Sincerely,\n\n" +
            "[Your name]\n",
        "letter-personal" to
            "[Date]\n\n" +
            "Dear [name],\n\n" +
            "[Paragraph.]\n\n[Paragraph.]\n\n" +
            "Best wishes,\n\n" +
            "[Your name]\n",
        "resume-classic" to RESUME,
    )

    private val CHATTER = Regex(
        "^(?:here(?:'s| is| are)\\b|sure\\b|certainly\\b|of course\\b|absolutely\\b|okay\\b|ok\\b|hello\\b|hi\\b|" +
            "i(?:'ve| have) (?:written|drafted|revised|made|updated)\\b|below is\\b|this (?:version|draft)\\b|" +
            "let me know\\b|feel free\\b|i hope\\b|i am domovoi\\b|i'm domovoi\\b|note:)",
        RegexOption.IGNORE_CASE,
    )
    private val FENCE = Regex("^\\s*```")

    /** The system rules and the user turn for a first draft. */
    fun firstDraft(kind: String, template: String, request: String, today: String, facts: List<String> = emptyList()): Pair<String, String> {
        val k = DraftKinds.get(kind)
        val skeleton = SKELETONS[template] ?: SKELETONS.getValue("letter-block")
        val lines = mutableListOf("Write a ${k.label.lowercase()}.", "The person asked: ${request.trim()}", "Today's date: $today")
        if (facts.isNotEmpty()) {
            lines += "Facts the person gave:"
            lines += facts.map { "- $it" }
        }
        lines += listOf("", "Skeleton:", skeleton)
        return RULES to lines.joinToString("\n")
    }

    /** The system rules and the user turn for a revision: the one version
     *  being revised, as data, and the instruction. */
    fun revision(markdown: String, instruction: String): Pair<String, String> =
        REVISE_RULES to ("Current version:\n<<<DOCUMENT\n" + markdown.trim() + "\nDOCUMENT>>>\n\n" +
            "Instruction: " + instruction.trim())

    private fun remark(line: String): Boolean {
        val t = line.trim()
        return t.isNotEmpty() && t.last() in ":.!" && CHATTER.find(t) != null
    }

    /** The document in a model's reply: fences, a greeting or remark on the
     *  first lines and a sign-off remark on the last ones dropped. */
    fun clean(reply: String): String {
        val lines = reply.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            .filter { ln -> FENCE.find(ln) == null && ln.trim() !in setOf("DOCUMENT>>>", "<<<DOCUMENT") }
            .toMutableList()
        while (lines.isNotEmpty() && (lines.first().isBlank() || remark(lines.first()))) lines.removeAt(0)
        while (lines.isNotEmpty() && (lines.last().isBlank() || remark(lines.last()))) lines.removeAt(lines.lastIndex)
        return lines.joinToString("\n") { it.trimEnd() }.trim()
    }
}
