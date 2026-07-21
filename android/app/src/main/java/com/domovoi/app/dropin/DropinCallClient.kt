package com.domovoi.app.dropin

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.contentOrNull
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.net.URI
import kotlin.concurrent.thread

/**
 * Phone side of a drop-in call — the counterpart of
 * domovoi/phone_dropin.py. One WebSocket to
 * ws://<domovoi>/v1/dropin/{room}: we stream 16 kHz mono PCM16 mic
 * frames up (voice-communication source = hardware AEC, which is what lets
 * the phone claim full-duplex), and play the room's relayed audio + chimes
 * straight into an AudioTrack.
 */
class DropinCallClient(
    private val context: Context,
    private val api: ApiClient,
    private val prefs: Prefs,
) {
    sealed class CallState {
        data object Idle : CallState()
        data object Connecting : CallState()
        data class Live(val peerRoom: String) : CallState()
        data class Ended(val reason: String) : CallState()
        data class Failed(val message: String) : CallState()
    }

    private val _state = MutableStateFlow<CallState>(CallState.Idle)
    val state: StateFlow<CallState> = _state

    private val _muted = MutableStateFlow(false)
    val muted: StateFlow<Boolean> = _muted

    private var ws: WebSocket? = null
    private var record: AudioRecord? = null
    private var track: AudioTrack? = null
    private var aec: AcousticEchoCanceler? = null
    @Volatile private var running = false
    private var prevAudioMode = AudioManager.MODE_NORMAL

    private val sampleRate = 16_000
    private val frameBytes = 960 // 30 ms of 16 kHz mono PCM16, matching the Pi

    /** Resolve the core drop-in WS URL via the web backend's discovery
     * endpoint, then open the call. Must hold RECORD_AUDIO permission. */
    suspend fun start(roomId: String) {
        if (_state.value is CallState.Connecting || _state.value is CallState.Live) return
        _state.value = CallState.Connecting
        _muted.value = false

        val url = runCatching { resolveWsUrl(roomId) }.getOrElse {
            _state.value = CallState.Failed(it.message ?: "couldn't reach the server")
            return
        }

        startPlayback()
        ws = api.http.newWebSocket(
            Request.Builder().url(url).build(),
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    startMic()
                }

                override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                    val t = track ?: return
                    val b = bytes.toByteArray()
                    runCatching { t.write(b, 0, b.size) }
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    val obj = runCatching { DomovoiJson.parseToJsonElement(text).jsonObject }
                        .getOrNull() ?: return
                    when (obj["type"]?.jsonPrimitive?.contentOrNull) {
                        "dropin_start" -> {
                            val peer = obj["peer_room"]?.jsonPrimitive?.contentOrNull ?: roomId
                            _state.value = CallState.Live(peer)
                        }
                        "dropin_end" -> {
                            val reason = obj["reason"]?.jsonPrimitive?.contentOrNull ?: "ended"
                            teardown()
                            _state.value = CallState.Ended(reason)
                        }
                        "error" -> {
                            val code = obj["code"]?.jsonPrimitive?.contentOrNull ?: "refused"
                            teardown()
                            _state.value = CallState.Failed(friendlyRefusal(code))
                        }
                    }
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    if (_state.value is CallState.Connecting || _state.value is CallState.Live) {
                        teardown()
                        _state.value = CallState.Failed(t.message ?: "connection lost")
                    }
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    if (_state.value is CallState.Live) {
                        teardown()
                        _state.value = CallState.Ended("closed")
                    }
                }
            },
        )
    }

    private suspend fun resolveWsUrl(roomId: String): String {
        val info = api.get("/api/satellites/$roomId/dropin/phone-info").jsonObject
        val webHost = URI(prefs.serverUrl.value).host ?: "localhost"
        val host = info["domovoi_host"]?.jsonPrimitive?.contentOrNull ?: webHost
        val port = info["domovoi_port"]?.jsonPrimitive?.intOrNull ?: 6370
        val scheme = if (prefs.serverUrl.value.startsWith("https")) "wss" else "ws"
        return "$scheme://$host:$port/v1/dropin/$roomId?phone_id=${prefs.deviceId}"
    }

    private fun startPlayback() {
        val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        prevAudioMode = audioManager.mode
        audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
        @Suppress("DEPRECATION")
        audioManager.isSpeakerphoneOn = true

        val minOut = AudioTrack.getMinBufferSize(
            sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT,
        )
        track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(sampleRate)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build()
            )
            .setBufferSizeInBytes(maxOf(minOut * 2, frameBytes * 8))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
            .also { it.play() }
    }

    @SuppressLint("MissingPermission") // caller gates on RECORD_AUDIO
    private fun startMic() {
        val minIn = AudioRecord.getMinBufferSize(
            sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
        )
        val rec = AudioRecord(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minIn * 2, frameBytes * 8),
        )
        if (AcousticEchoCanceler.isAvailable()) {
            aec = AcousticEchoCanceler.create(rec.audioSessionId)?.also { it.enabled = true }
        }
        rec.startRecording()
        record = rec
        running = true
        thread(name = "dropin-mic") {
            val buf = ByteArray(frameBytes)
            while (running) {
                val n = rec.read(buf, 0, buf.size)
                if (n <= 0) continue
                if (_muted.value) continue
                // Send exactly what was read — the bridge relays verbatim.
                ws?.send(buf.toByteString(0, n))
            }
        }
    }

    fun setMuted(v: Boolean) {
        _muted.value = v
    }

    fun hangUp() {
        runCatching { ws?.send("""{"type":"dropin_end"}""") }
        teardown()
        if (_state.value !is CallState.Failed) _state.value = CallState.Ended("ended")
    }

    fun reset() {
        teardown()
        _state.value = CallState.Idle
    }

    private fun teardown() {
        running = false
        runCatching { record?.stop() }
        runCatching { record?.release() }
        record = null
        runCatching { aec?.release() }
        aec = null
        runCatching { track?.stop() }
        runCatching { track?.release() }
        track = null
        runCatching { ws?.close(1000, "bye") }
        ws = null
        val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        @Suppress("DEPRECATION")
        audioManager.isSpeakerphoneOn = false
        audioManager.mode = prevAudioMode
    }

    private fun friendlyRefusal(code: String): String = when (code) {
        "target_offline" -> "that room isn't connected"
        "target_no_aec" -> "that room's mic can't do drop-in (needs the XVF3800 array)"
        "target_busy" -> "that room is already in a call"
        "initiator_busy" -> "this phone is already in a call"
        "disabled" -> "drop-in is turned off in settings"
        else -> "refused: $code"
    }
}
