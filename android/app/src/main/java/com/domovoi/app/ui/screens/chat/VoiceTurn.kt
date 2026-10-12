package com.domovoi.app.ui.screens.chat

import com.domovoi.app.AppContainer
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.isDeviceTokenRefusal
import com.domovoi.app.player.PlayItem
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

/*
 * One push-to-talk turn on the wire: the recording goes to this phone's own
 * server (POST /api/chat/threads/{id}/voice), which hands it to the core
 * (POST /v1/phone/voice, domovoi/phone_voice.py) with the household token
 * this phone sent. The answer is decoded here; what it asks the phone to do
 * (device actions) is turned into the app's own player calls.
 */

@Serializable
internal data class VoiceAudio(val mime: String? = null, val b64: String? = null)

@Serializable
internal data class VoiceSpeaker(
    val person_id: Long? = null,
    val name: String? = null,
    val confidence: Double? = null,
    val confident: Boolean = false,
)

@Serializable
internal data class VoiceError(val detail: String? = null, val code: String? = null)

/** The core's answer to a turn. Every field tolerates being missing. */
@Serializable
internal data class VoiceTurnReply(
    val heard: Boolean = false,
    val reason: String? = null,
    val transcript: String = "",
    val reply: String = "",
    val audio: VoiceAudio? = null,
    val speaker: VoiceSpeaker? = null,
    val expect_followup: Boolean = false,
    val actions: List<JsonObject> = emptyList(),
    val matched_handler: String? = null,
    val matched_path: String? = null,
    val session_id: String? = null,
    val room_id: String? = null,
    val messages: List<MessageRow> = emptyList(),
    val error: VoiceError? = null,
) {
    /** The reply's WAV, or null when the server sent none (TTS down). */
    fun wavBytes(): ByteArray? =
        audio?.b64?.takeIf { it.isNotEmpty() }?.let { runCatching { java.util.Base64.getMimeDecoder().decode(it) }.getOrNull() }
}

/** A refused turn: the server's own sentence and its code. */
internal class VoiceTurnException(val status: Int, message: String, val code: String?) : IOException(message)

/** What the server said a refusal was, or a plain fallback. */
internal fun voiceRefusal(status: Int, body: String): VoiceTurnException {
    val obj = runCatching { DomovoiJson.parseToJsonElement(body) as? JsonObject }.getOrNull()
    val detail = obj?.get("detail")?.let { (it as? JsonPrimitive)?.contentOrNull }
    val code = obj?.get("code")?.let { (it as? JsonPrimitive)?.contentOrNull }
    val text = detail?.takeIf { it.isNotBlank() } ?: when (status) {
        409 -> "Domovoi is still answering the last one."
        413 -> "That was too long — a voice message is at most 30 seconds."
        else -> "Domovoi couldn't take that ($status)."
    }
    return VoiceTurnException(status, text, code)
}

/** What the phone's own player is doing, for "what's playing". */
internal fun playerState(item: PlayItem?, playing: Boolean): JsonObject? {
    item ?: return null
    return buildJsonObject {
        put("title", item.title)
        item.artist?.let { put("artist", it) }
        put("kind", item.kind.name.lowercase())
        put("playing", playing)
    }
}

/** How long the server may take: speech-to-text, the router (a cold model
 *  can take most of a minute on an all-CPU box) and text-to-speech. Above
 *  the web hop's own 180 s so the server's 504 is what the phone reads. */
private const val VOICE_READ_TIMEOUT_SEC = 200L

/** Sends one recording (16 kHz mono PCM16) into [threadId]. */
internal suspend fun sendVoiceTurn(
    app: AppContainer,
    threadId: Long,
    pcm: ByteArray,
    player: JsonObject?,
): VoiceTurnReply = sendVoiceTurn(app.api, app.prefs.deviceId, threadId, pcm, player)

/** [sendVoiceTurn] on a bare [ApiClient] (the JVM tests' shape). */
internal suspend fun sendVoiceTurn(
    api: ApiClient,
    deviceId: String,
    threadId: Long,
    pcm: ByteArray,
    player: JsonObject?,
): VoiceTurnReply = withContext(Dispatchers.IO) {
    val form = MultipartBody.Builder().setType(MultipartBody.FORM)
        .addFormDataPart("audio", "turn.pcm", pcm.toRequestBody("application/octet-stream".toMediaType()))
        .addFormDataPart("phone_id", deviceId)
        .apply { if (player != null) addFormDataPart("player", player.toString()) }
        .build()
    val req = Request.Builder()
        .url(api.absolute("/api/chat/threads/$threadId/voice"))
        // The same preflight-forcing header every write carries (WEB-6).
        .header("X-Requested-With", "DomovoiApp")
        .post(form)
        .build()
    val client = api.http.newBuilder().readTimeout(VOICE_READ_TIMEOUT_SEC, TimeUnit.SECONDS).build()
    client.newCall(req).execute().use { resp ->
        val body = resp.body?.string().orEmpty()
        if (!resp.isSuccessful) {
            api.notePossiblePairingRefusal(resp.code, body)
            if (resp.code == 401 || resp.code == 403) {
                throw ApiException(
                    resp.code, "${resp.code} ${resp.message}: ${body.take(200)}",
                    deviceTokenRequired = isDeviceTokenRefusal(resp.code, body), body = body.take(4096),
                )
            }
            throw voiceRefusal(resp.code, body)
        }
        DomovoiJson.decodeFromString(VoiceTurnReply.serializer(), body)
    }
}

// ---------------------------------------------------------------------------
// Device actions: what the phone itself does for a turn (Response.device_actions)
// ---------------------------------------------------------------------------

/** One thing the server asked this phone to do. */
internal sealed class DeviceAction {
    /** Play [items] on the phone, replacing what it plays. [resume]: a book
     *  or an episode, picked up where this phone's listener left off. */
    data class Play(val items: List<PlayItem>, val resume: Boolean = false) : DeviceAction()
    data object Pause : DeviceAction()
    data object Resume : DeviceAction()
    data object Stop : DeviceAction()
    data object Next : DeviceAction()
    data object Previous : DeviceAction()

    /** [change] "up" | "down" | "set" (with [percent]). */
    data class Volume(val change: String, val percent: Int? = null) : DeviceAction()

    /** Starts after the reply has been heard (a song should not play over
     *  "Playing X"); the rest act at once. */
    val afterReply: Boolean get() = this is Play
}

/** The actions the app understands, in order; anything else is skipped. */
internal fun parseDeviceActions(raw: List<JsonObject>): List<DeviceAction> = raw.mapNotNull { a ->
    when (a.str("type")) {
        "play" -> {
            val items = (a["items"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonObject)?.let(::playItemFor) }
            val resume = (a["resume"] as? JsonPrimitive)?.booleanOrNull == true
            if (items.isEmpty()) null else DeviceAction.Play(items, resume)
        }
        "pause" -> DeviceAction.Pause
        "resume" -> DeviceAction.Resume
        "stop" -> DeviceAction.Stop
        "next" -> DeviceAction.Next
        "previous" -> DeviceAction.Previous
        "volume" -> a.str("change")?.takeIf { it in setOf("up", "down", "set") }?.let { change ->
            val pct = (a["percent"] as? JsonPrimitive)?.intOrNull
            if (change == "set" && pct == null) null else DeviceAction.Volume(change, pct?.coerceIn(0, 100))
        }
        else -> null
    }
}

/** A queue entry from an action's item: the same item the app's own
 *  screens would make for that library track, episode, book or station. */
internal fun playItemFor(o: JsonObject): PlayItem? {
    val id = (o["id"] as? JsonPrimitive)?.longOrNull ?: return null
    val title = o.str("title") ?: return null
    val duration = (o["duration_sec"] as? JsonPrimitive)?.doubleOrNull
    return when (o.str("kind")) {
        "track" -> PlayItem.fromTrack(id, title, o.str("artist"), o.str("album"), duration)
        "episode" -> PlayItem.fromEpisode(id, title, o.str("artist"), duration, o.str("artwork"), emptyList())
        "book" -> PlayItem.fromBook(id, title, o.str("artist"), duration, o.str("artwork"), emptyList())
        "station" -> PlayItem.fromStation(id, title)
        else -> null
    }
}

private fun JsonObject.str(key: String): String? =
    (this[key] as? JsonPrimitive)?.takeIf { it.isString }?.contentOrNull?.takeIf { it.isNotBlank() }
