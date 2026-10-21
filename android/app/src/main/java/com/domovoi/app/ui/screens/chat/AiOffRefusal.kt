package com.domovoi.app.ui.screens.chat

import com.domovoi.app.net.ApiException

/** [ApiException.refusal] when the household's AI features are off
 *  (the core's ai_features.REFUSAL_VALUE). */
internal const val AI_OFF_REFUSAL = "ai-off"

/** The start of the server's own sentence for that refusal
 *  (ai_features.TURNED_OFF_REASON), for a 409 whose header got lost. */
internal const val AI_OFF_DETAIL = "AI features are turned off for this home"

/** What a person reads when the server's chat turned a message or a picture
 *  away because the household's AI features are off, instead of "409 Conflict". */
const val AI_CHAT_OFF_TEXT = "AI chat is turned off on your home server"

/**
 * True when the server refused because its AI features are off: the
 * `X-Domovoi-Refusal: ai-off` header, or (a proxy that drops headers) a
 * 409 carrying the server's sentence for it. A 409 for anything else
 * ("still answering the last one") is not this.
 */
internal fun isAiOffRefusal(status: Int, refusal: String?, body: String?): Boolean =
    refusal?.trim().equals(AI_OFF_REFUSAL, ignoreCase = true) ||
        (status == 409 && body?.contains(AI_OFF_DETAIL) == true)

internal fun isAiOffRefusal(e: Throwable): Boolean =
    e is ApiException && isAiOffRefusal(e.status, e.refusal, e.body)

/** The error a send that never reached the model shows in the reply's
 *  place: plain words for the AI-off refusal, else the status line. */
internal fun sendFailureLine(status: Int, reason: String, refusal: String?, body: String?): String =
    if (isAiOffRefusal(status, refusal, body)) AI_CHAT_OFF_TEXT else "$status $reason".trim()
