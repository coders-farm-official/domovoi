package com.domovoi.app.away

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.sync.Mutex
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.longOrNull

/**
 * The plain lines away chat shows (the same words on the iPhone).
 */
object AwayText {
    const val CONNECTING = "Connecting to your house…"
    const val UNREACHABLE = "Can't reach your house."
    const val AWAY_OFF = "Chat from away is turned off on your house."
    const val INTERNET_OFF = "Your house's internet setting doesn't allow chat from away."
    const val AI_OFF = "Your house's AI is turned off."
    const val NOT_SET_UP = "Away chat isn't set up for this phone any more."
    const val SET_UP_AGAIN = "Away chat needs setting up again at home."
    const val PICTURE = "That picture couldn't be sent."
    const val BUSY = "Your house is busy. Try again shortly."
    const val WRONG = "Something went wrong."

    /** The offer, right after pairing and in Settings. */
    const val OFFER = "Chat with your house when you're away? You can turn this off any time."

    /** The line for an ERR [code] (§5, §8 item 6). */
    fun forErr(code: String): String = when (code) {
        "unsupported_picture" -> PICTURE
        "unavailable", "busy", "rate" -> BUSY
        "ai_off" -> AI_OFF
        else -> WRONG
    }

    /** The line for a house BYE [code] (§3.1), or a close without one. */
    fun forBye(code: String?): String = when (code) {
        "away_off" -> AWAY_OFF
        "revoked" -> NOT_SET_UP
        else -> UNREACHABLE
    }
}

/** A request the house refused, or a connection that ended under it. */
class AwayRefused(val code: String, val detail: String, val retryAfter: Long? = null) :
    Exception(AwayText.forErr(code)) {
    val line: String get() = AwayText.forErr(code)
}

/** The connection ended before the answer: [byeCode] is the house's BYE
 *  (`revoked`, `away_off`, …), null for a close without a word. */
class AwayDropped(val byeCode: String?) : java.io.IOException(AwayText.forBye(byeCode))

/** What a `chat.send` (or a `chat.stream` that found a reply running)
 *  brings, in order. */
sealed interface SendUpdate {
    /** The house stored the user turn: `{chat_id, message_id, user_message}`
     *  for a send; `{streaming: true, message_id}` for a stream. */
    data class Stored(val chatId: Long?, val messageId: Long?, val body: JsonObject) : SendUpdate

    /** More of the reply (a long one comes as several: they join up). */
    data class Delta(val text: String) : SendUpdate

    /** The stored reply. [tooLarge]: its row didn't fit one event and came
     *  without its text; read it from `chat.history` (§8 item 4). */
    data class Done(val message: JsonObject?, val tooLarge: Boolean) : SendUpdate

    /** The reply failed with a home chat error code (`vision_model_missing`,
     *  `ai_off`, `cancelled`, …) and its stored row, if any. */
    data class Failed(val code: String?, val detail: String, val message: JsonObject?) : SendUpdate

    /** `chat.stream` found nothing running: the reply is in history. */
    data object NotRunning : SendUpdate

    /** The house refused the request (an ERR): nothing was sent. */
    data class Refused(val code: String, val detail: String, val retryAfter: Long?) : SendUpdate

    /** The connection dropped mid-reply: the house goes on and stores it;
     *  `chat.stream` picks it up after reconnecting. */
    data class Dropped(val byeCode: String?) : SendUpdate
}

/**
 * The five requests (§4) over one [AwayConnection], with the phone's half
 * of §4.6: one running reply at a time on this phone ([replyLock]: a send,
 * or a stream picking one up; a second is refused here as `busy`), and at
 * most four open requests (a fifth waits in [AwayConnection.call]).
 */
class AwayClient(private val connection: suspend () -> AwayConnection) {
    private val replyLock = Mutex()

    private suspend fun single(request: JsonObject): JsonObject {
        val replies = connection().call(request)
        for (r in replies) {
            when (r) {
                is HouseReply.Resp -> return r.body
                is HouseReply.Err -> throw AwayRefused(r.code, r.detail, r.retryAfter)
                is HouseReply.Closed -> throw AwayDropped(r.code)
                is HouseReply.Event -> Unit
            }
        }
        throw AwayDropped(null)
    }

    /** `chat.list`: `{threads, next}`, newest first. */
    suspend fun list(archived: Boolean = false, cursor: String? = null): JsonObject =
        single(AwayWire.list(archived = archived, cursor = cursor))

    /** Every page of `chat.list`, joined (at most [maxPages]). */
    suspend fun listAll(archived: Boolean = false, maxPages: Int = 10): List<JsonObject> {
        val out = mutableListOf<JsonObject>()
        var cursor: String? = null
        repeat(maxPages) {
            val page = list(archived, cursor)
            (page["threads"] as? kotlinx.serialization.json.JsonArray)?.forEach { (it as? JsonObject)?.let(out::add) }
            cursor = (page["next"] as? JsonPrimitive)?.contentOrNull
            if (cursor == null) return out
        }
        return out
    }

    /** `chat.history`: `{thread, messages, more}`, oldest first in the page. */
    suspend fun history(chatId: Long, beforeId: Long? = null): JsonObject = single(AwayWire.history(chatId, beforeId))

    /** `chat.cancel`: whether a running reply was stopped. */
    suspend fun cancel(chatId: Long): Boolean =
        (single(AwayWire.cancel(chatId))["cancelled"] as? JsonPrimitive)?.booleanOrNull == true

    /**
     * `chat.send`: [text] (empty only with a [picture], a JPEG already
     * made small: [AwayPicture]) into [chatId] (null: a new chat, private
     * to this phone). [clientId] makes a resend after a drop safe: the
     * house answers the first send again (§4.3).
     */
    fun send(chatId: Long?, text: String, picture: ByteArray? = null, clientId: String = AwayWire.newClientId()): Flow<SendUpdate> =
        flow {
            if (AwayWire.codePoints(text) > AwayWire.MAX_TEXT) {
                emit(SendUpdate.Refused("too_large", "the message is too long", null))
                return@flow
            }
            if (picture != null && picture.size > AwayWire.MAX_PICTURE) {
                emit(SendUpdate.Refused("too_large", "the picture is larger than 1 MiB", null))
                return@flow
            }
            val info = picture?.let { PictureInfo.jpeg(it) }
            // One running reply on this phone: a second send is refused
            // here, as the house would (busy), not queued.
            if (!replyLock.tryLock()) {
                emit(SendUpdate.Refused("busy", "a reply is still being written", null))
                return@flow
            }
            try {
                val replies = try {
                    connection().call(AwayWire.send(chatId, clientId, text, info), picture)
                } catch (e: TooLarge) {
                    emit(SendUpdate.Refused("too_large", "the message is too long", null))
                    return@flow
                } catch (e: AwayDropped) {
                    // Never reached the house (silence, Tor, no keys): said, not thrown.
                    emit(SendUpdate.Dropped(e.byeCode))
                    return@flow
                }
                follow(replies) { emit(it) }
            } finally {
                replyLock.unlock()
            }
        }

    /** `chat.stream`: pick up this phone's reply still being written in
     *  [chatId] (after a drop, or reopening the chat). */
    fun stream(chatId: Long): Flow<SendUpdate> = flow {
        if (!replyLock.tryLock()) {
            emit(SendUpdate.Refused("busy", "a reply is still being written", null))
            return@flow
        }
        try {
            val replies = try {
                connection().call(AwayWire.stream(chatId))
            } catch (e: AwayDropped) {
                emit(SendUpdate.Dropped(e.byeCode))
                return@flow
            }
            follow(replies) { emit(it) }
        } finally {
            replyLock.unlock()
        }
    }

    private suspend fun follow(replies: kotlinx.coroutines.channels.ReceiveChannel<HouseReply>, emit: suspend (SendUpdate) -> Unit) {
        for (r in replies) {
            when (r) {
                is HouseReply.Resp -> {
                    val b = r.body
                    if ((b["streaming"] as? JsonPrimitive)?.booleanOrNull == false) {
                        emit(SendUpdate.NotRunning)
                        return
                    }
                    emit(SendUpdate.Stored((b["chat_id"] as? JsonPrimitive)?.longOrNull, (b["message_id"] as? JsonPrimitive)?.longOrNull, b))
                }
                is HouseReply.Event -> {
                    val b = r.body
                    when (r.type) {
                        "delta" -> emit(SendUpdate.Delta((b["text"] as? JsonPrimitive)?.contentOrNull.orEmpty()))
                        "done" -> {
                            val m = b["message"] as? JsonObject
                            val big = (b["too_large"] as? JsonPrimitive)?.booleanOrNull == true ||
                                (m?.get("too_large") as? JsonPrimitive)?.booleanOrNull == true
                            emit(SendUpdate.Done(m, big))
                            return
                        }
                        "error" -> {
                            emit(
                                SendUpdate.Failed(
                                    (b["code"] as? JsonPrimitive)?.contentOrNull,
                                    (b["detail"] as? JsonPrimitive)?.contentOrNull.orEmpty(),
                                    b["message"] as? JsonObject,
                                ),
                            )
                            return
                        }
                    }
                }
                is HouseReply.Err -> {
                    emit(SendUpdate.Refused(r.code, r.detail, r.retryAfter))
                    return
                }
                is HouseReply.Closed -> {
                    emit(SendUpdate.Dropped(r.code))
                    return
                }
            }
        }
        emit(SendUpdate.Dropped(null))
    }
}
