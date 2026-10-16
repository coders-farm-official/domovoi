package com.domovoi.app.roommode

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put

/**
 * The room phone's side of the satellite protocol on `/v1/stream/{room}`
 * (docs/uml/satellite-protocol.md; the core's contract comment at the top
 * of domovoi/streaming.py). Frames only, no I/O: what the phone says, and
 * what it makes of what the core says. Checked against the shared fixtures
 * (docs/uml/satellite-fixtures.json) by RoomProtocolTest, which the core's
 * and the Pi's tests read too.
 */
object RoomProtocol {
    const val PROTOCOL_VERSION = "0.1"

    /** What the hello names the device (`satellites.board`, the dashboard's badge). */
    const val BOARD = "android_room"

    /** 30 ms of 16 kHz mono PCM16: what a Pi sends, and what the core counts. */
    const val FRAME_SAMPLES = 480
    const val FRAME_BYTES = FRAME_SAMPLES * 2
    const val FRAME_MS = 30
    const val SAMPLE_RATE = 16_000

    /**
     * Exactly what this client handles, as the hello's `supports` list
     * (domovoi/satellite_abilities.py): a device that sends the list gets
     * nothing it did not name. Never chat mode (v1 does not handle
     * `chat_start`). `full_duplex` only on a phone with an echo canceller:
     * it is what lets the core start a drop-in here, and a room that cannot
     * capture while it plays would howl.
     */
    fun supports(echoCanceller: Boolean): List<String> = buildList {
        if (echoCanceller) add("full_duplex")
        add("speech_pause")
        add("capture_control")
        add("announce_after_session_end")
        add("wake_models")
        add("sounds")
    }

    fun hello(
        roomId: String,
        wakeWord: String,
        pairingToken: String,
        approvalCode: String?,
        echoCanceller: Boolean,
        micEnabled: Boolean = true,
    ): JsonObject = buildJsonObject {
        put("type", "hello")
        put("room_id", roomId)
        put("wake_word", wakeWord)
        put("pairing_token", pairingToken)
        put("approval_code", approvalCode)
        put("sat_type", "voice")
        put("mic_enabled", micEnabled)
        put("board", BOARD)
        put("supports", JsonArray(supports(echoCanceller).map(::JsonPrimitive)))
    }

    // ── what the phone sends ────────────────────────────────────────────

    fun utteranceStart(trigger: String, utt: Int, backlogMs: Int, wakeMs: Int?): JsonObject = buildJsonObject {
        put("type", "utterance_start")
        put("trigger", trigger)
        put("utt", utt)
        put("backlog_ms", backlogMs)
        if (wakeMs != null) put("wake_ms", wakeMs)
    }

    fun volumeStatus(level: Int): JsonObject = buildJsonObject {
        put("type", "volume_status")
        put("level", level.coerceIn(0, 100))
    }

    fun musicFailed(streamUrl: String, reason: String, attempts: Int): JsonObject = buildJsonObject {
        put("type", "music_failed")
        put("stream_url", streamUrl.take(300))
        put("reason", reason.take(200))
        put("attempts", attempts)
    }

    val MUSIC_READY: JsonObject = buildJsonObject { put("type", "music_ready") }
    val DROPIN_END: JsonObject = buildJsonObject { put("type", "dropin_end") }

    /** A message type an older core would answer with `error` is sent only
     *  when its `ready.features` lists it (frame → feature). */
    val GATED_TYPES = mapOf(
        "speech_pause" to "speech_pause",
        "speech_resume" to "speech_pause",
        "music_failed" to "music_failed",
        "health" to "health",
        "log_push" to "log_push",
    )

    /** Whether [frame] may go to a core whose `ready` listed [features]. */
    fun maySend(frame: JsonObject, features: Set<String>): Boolean {
        val type = (frame["type"] as? JsonPrimitive)?.contentOrNull ?: return false
        val gate = GATED_TYPES[type] ?: return true
        return gate in features
    }

    /** `http(s)://h:p` → `ws(s)://h:p/v1/stream/<room>`. */
    fun streamUrl(coreUrl: String, roomId: String): String {
        val base = coreUrl.trimEnd('/')
        val ws = when {
            base.startsWith("https://") -> "wss://" + base.removePrefix("https://")
            base.startsWith("http://") -> "ws://" + base.removePrefix("http://")
            else -> base
        }
        return "$ws/v1/stream/$roomId"
    }

    // ── what the core sends ─────────────────────────────────────────────

    sealed class ServerFrame {
        data class Ready(val features: Set<String>, val roomId: String?, val botName: String?) : ServerFrame()
        data class Error(val reason: String?, val message: String?, val code: String?) : ServerFrame()
        data object Pong : ServerFrame()
        data class Transcript(val text: String) : ServerFrame()
        data class ResponseStart(val text: String?, val sampleRate: Int) : ServerFrame()
        data class ResponseEnd(val interrupted: Boolean, val expectFollowup: Boolean) : ServerFrame()
        data class SetVolume(val level: Int) : ServerFrame()
        data class MusicStart(val streamUrl: String?) : ServerFrame()
        data object MusicStop : ServerFrame()
        data class EndCapture(val utt: Int?) : ServerFrame()
        data object SoundsChanged : ServerFrame()
        data class SetWakeWord(val slug: String?, val threshold: Double?) : ServerFrame()
        data object WakeModelsChanged : ServerFrame()
        data class DropinStart(val peerRoom: String?, val peerLabel: String?, val sampleRate: Int, val fullDuplex: Boolean) : ServerFrame()
        data class DropinEnd(val reason: String?) : ServerFrame()

        /** A type this client does not handle (logged, ignored). */
        data class Other(val type: String?) : ServerFrame()
    }

    /** One text frame from the core; [ServerFrame.Other] for anything else. */
    fun parse(text: String): ServerFrame {
        val o = runCatching { Json.parseToJsonElement(text) as? JsonObject }.getOrNull()
            ?: return ServerFrame.Other(null)
        fun s(k: String): String? = (o[k] as? JsonPrimitive)?.takeIf { it.isString }?.content
        fun i(k: String): Int? = (o[k] as? JsonPrimitive)?.let { it.intOrNull ?: it.doubleOrNull?.toInt() }
        fun b(k: String): Boolean = (o[k] as? JsonPrimitive)?.booleanOrNull ?: false
        return when (val type = s("type")) {
            "ready" -> ServerFrame.Ready(
                features = (o["features"] as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.contentOrNull }?.toSet() ?: emptySet(),
                roomId = s("room_id"),
                botName = s("bot_name"),
            )
            "error" -> ServerFrame.Error(s("reason"), s("message"), s("code"))
            "pong" -> ServerFrame.Pong
            "transcript" -> ServerFrame.Transcript(s("text").orEmpty())
            "response_start" -> ServerFrame.ResponseStart(s("text"), (i("audio_sample_rate") ?: SAMPLE_RATE).coerceIn(8_000, 48_000))
            "response_end" -> ServerFrame.ResponseEnd(b("interrupted"), b("expect_followup"))
            "set_volume" -> ServerFrame.SetVolume((i("level") ?: 50).coerceIn(0, 100))
            "music_start" -> ServerFrame.MusicStart(s("stream_url"))
            "music_stop" -> ServerFrame.MusicStop
            "end_capture" -> ServerFrame.EndCapture(i("utt"))
            "sounds_changed" -> ServerFrame.SoundsChanged
            "set_wake_word" -> ServerFrame.SetWakeWord(s("slug"), (o["threshold"] as? JsonPrimitive)?.doubleOrNull)
            "wake_models_changed" -> ServerFrame.WakeModelsChanged
            "dropin_start" -> ServerFrame.DropinStart(
                s("peer_room"), s("peer_label"), i("audio_sample_rate") ?: SAMPLE_RATE, b("full_duplex"),
            )
            "dropin_end" -> ServerFrame.DropinEnd(s("reason"))
            else -> ServerFrame.Other(type)
        }
    }

    /** Only an http(s) URL with no whitespace or control characters is a
     *  stream (the Pi's `_is_stream_url`). */
    fun isStreamUrl(value: String?): Boolean {
        if (value.isNullOrEmpty()) return false
        if (!(value.startsWith("http://") || value.startsWith("https://"))) return false
        return value.none { it.isWhitespace() || it.code < 0x20 || it.code == 0x7f }
    }

    internal fun JsonObject.typeName(): String? = (this["type"] as? JsonPrimitive)?.contentOrNull

    internal fun longOf(e: JsonElement?): Long? = (e as? JsonPrimitive)?.longOrNull
}
