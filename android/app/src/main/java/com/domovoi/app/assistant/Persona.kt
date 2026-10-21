package com.domovoi.app.assistant

import java.time.ZonedDateTime

/**
 * Who the on-phone model is told it is. One prompt serves voice and typed
 * turns: the server's voice prompt (domovoi/clients/ollama.py) with the name
 * Domovoi — the only product name — plus what the phone cannot do, and that
 * it does have the phone's clock ([clockLine], given every turn).
 */
object Persona {
    const val NAME = "Domovoi"

    /** N1's one sentence for the phone facts (PhoneFacts.line): asked about,
     *  they are used; otherwise they are never brought up. */
    const val FACTS_SENTENCE: String =
        "You may also be given phone facts (what is playing, the home server connection, its rooms, " +
            "who you are talking with): use them only to answer a question about them, and never mention them otherwise."

    const val SYSTEM_PROMPT: String =
        "You are $NAME, a helpful voice assistant. Keep responses concise and " +
            "conversational — this is a voice interaction, so avoid markdown, bullet " +
            "lists, code blocks, and long structured responses. Speak naturally, in " +
            "short sentences. Most replies should be one to three sentences. You are " +
            "running on this phone with no internet. You cannot set timers, play music, " +
            "open files or look anything up online; if asked, say so in one sentence. " +
            "You do have the phone's clock: when its reading is given, use it to answer " +
            "questions about the time, the date or the day and to work out other dates " +
            "and times from it, and don't mention it otherwise. " +
            FACTS_SENTENCE

    /**
     * The phone's clock as the model is told it, read fresh for every turn:
     * "The phone's clock says it is Thursday, October 15, 2026, 3:42 PM,
     * Pacific Daylight Time (America/Los_Angeles, UTC-07:00)." The engine
     * puts it after [SYSTEM_PROMPT], or ahead of the question when it keeps
     * a conversation that was told an earlier reading (LlmEngine.reply).
     */
    fun clockLine(now: ZonedDateTime, style: ClockStyle): String =
        "The phone's clock says it is ${style.date(now)}, ${style.time(now)}, ${style.zoneName(now)} " +
            "(${now.zone.id}, ${ClockStyle.utcOffset(now)})."
}
