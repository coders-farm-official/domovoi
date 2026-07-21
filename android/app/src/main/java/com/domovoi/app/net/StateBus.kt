package com.domovoi.app.net

import com.domovoi.app.data.Prefs
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

data class WsEvent(val type: String, val payload: JsonElement?)

/**
 * Android analog of the web stateBus (web/static/data.js): one WebSocket to
 * /ws/state, subscribe-all on open ({"subscribe": []}), exponential-backoff
 * reconnect (1s -> x1.6 -> 15s cap, reset on open). Consumers filter by
 * event type client-side, exactly like the web hooks.
 */
class StateBus(private val api: ApiClient, private val prefs: Prefs) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val _events = MutableSharedFlow<WsEvent>(extraBufferCapacity = 256)
    val events: SharedFlow<WsEvent> = _events

    private val _connected = MutableStateFlow(false)
    val connected: StateFlow<Boolean> = _connected

    private var ws: WebSocket? = null
    private var reconnectJob: Job? = null
    private var delayMs = 1000L
    private var started = false
    private var currentBase = ""

    @Synchronized
    fun start() {
        if (started) return
        started = true
        currentBase = prefs.serverUrl.value
        connect()
        // Reconnect from scratch when the user changes the server URL.
        scope.launch {
            prefs.serverUrl.collect { url ->
                if (started && url != currentBase) {
                    currentBase = url
                    restart()
                }
            }
        }
    }

    @Synchronized
    private fun restart() {
        ws?.cancel()
        ws = null
        reconnectJob?.cancel()
        delayMs = 1000L
        connect()
    }

    private fun wsUrl(): String? {
        val base = prefs.serverUrl.value
        if (base.isBlank()) return null
        return base.replaceFirst("http://", "ws://").replaceFirst("https://", "wss://") + "/ws/state"
    }

    private fun connect() {
        val url = wsUrl() ?: run { scheduleReconnect(); return }
        val req = Request.Builder().url(url).build()
        ws = api.http.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                delayMs = 1000L
                _connected.value = true
                webSocket.send("""{"subscribe": []}""")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                runCatching {
                    val obj = DomovoiJson.parseToJsonElement(text).jsonObject
                    val type = obj["type"]?.jsonPrimitive?.content ?: return
                    _events.tryEmit(WsEvent(type, obj["data"]))
                }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = dropped()
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = dropped()
        })
    }

    private fun dropped() {
        _connected.value = false
        scheduleReconnect()
    }

    private fun scheduleReconnect() {
        if (!started) return
        reconnectJob?.cancel()
        reconnectJob = scope.launch {
            delay(delayMs)
            delayMs = (delayMs * 1.6).toLong().coerceAtMost(15_000L)
            connect()
        }
    }
}
