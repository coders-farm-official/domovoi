package com.domovoi.app.assistant

/**
 * Chat titles (assistant plan part 7), the same rules as the server's
 * `domovoi/chat_titles.py`; keep the two in step.
 *
 *  - **Right away:** the first [WORDS] words of the first message
 *    ([firstWords]).
 *  - **After the first reply has finished:** the phone's model writes a
 *    title of at most [WORDS] words in a short conversation of its own
 *    (the kept one is left as it is: N0 measured that it survives), once
 *    the engine is idle. Its words are cleaned up here ([clean]); anything
 *    that doesn't read as a title leaves the first words. Free text, not a
 *    regex `ResponseFormat`: N0's pattern with nested counted repeats
 *    crashed the runtime (SIGILL) and ran the words together.
 *  - A name the person set ([StoredConversation.titleByUser]) is never
 *    written over.
 */
object ChatTitles {
    const val WORDS = 5
    const val MAX_CHARS = 60

    /** What the model is asked (the same sentence as the server's). */
    const val SYSTEM = "Write a title of at most five words for this chat. Reply with the title only."

    /** How much of the first question and answer the model reads. */
    const val EXCERPT_CHARS = 800

    /** Tokens a title may take: five words and some slack. */
    const val MAX_TOKENS = 16

    /** Failed or unusable model titles before the job gives up on a chat. */
    const val MAX_TRIES = 2

    private val LEAD = Regex("^(?:title|chat title|subject)\\s*[:\\-–]\\s*", RegexOption.IGNORE_CASE)
    private const val WRAP = "\"'`*_#“”‘’«»[]()<> "
    private const val TRAIL = ".,;:!?-–—…"

    /** The first [WORDS] words of [text], with "…" when there were more
     *  (or they ran past [MAX_CHARS]); null for no words at all. */
    fun firstWords(text: String?): String? {
        val words = text.orEmpty().split(Regex("\\s+")).filter { it.isNotEmpty() }
        if (words.isEmpty()) return null
        var title = words.take(WORDS).joinToString(" ")
        var cut = words.size > WORDS
        if (title.length > MAX_CHARS) {
            title = title.take(MAX_CHARS).trimEnd()
            cut = true
        }
        return if (cut) "$title…" else title
    }

    /** What the model wrote, as a title: its first line without a
     *  "Title:" lead, quotes, markdown marks or closing punctuation, cut to
     *  [WORDS] words; null when nothing is left. */
    fun clean(raw: String?): String? {
        val line = raw.orEmpty().lineSequence().map { it.trim() }.firstOrNull { it.isNotEmpty() } ?: return null
        val unwrapped = LEAD.replace(line.trim { it in WRAP }, "").trim { it in WRAP }
        val bare = unwrapped.trimEnd { it in TRAIL }.trim { it in WRAP }
        val words = bare.split(Regex("\\s+")).filter { it.isNotEmpty() }
        if (words.isEmpty()) return null
        val title = words.take(WORDS).joinToString(" ").let { if (it.length > MAX_CHARS) it.take(MAX_CHARS).trimEnd() else it }
        return title.ifEmpty { null }
    }

    /** The user turn the model is given: the chat's first exchange, each
     *  side cut to [EXCERPT_CHARS]. */
    fun prompt(question: String, answer: String): String =
        "Chat:\nPerson: ${question.trim().take(EXCERPT_CHARS)}\nDomovoi: ${answer.trim().take(EXCERPT_CHARS)}\n\nTitle:"
}
