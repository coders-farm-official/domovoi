package com.domovoi.app.ui.screens.home

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import com.domovoi.app.net.failureText
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.net.URLEncoder
import java.time.ZoneId

/**
 * Home's calls — the same web-backend endpoints web/static/home.jsx uses,
 * through the app's one [ApiClient] (which carries X-Device-Token). Reads
 * are the open ones; the presses (pause, cancel, announce) are device tier,
 * and a refusal there sends the phone to the pairing screen like any other.
 * Plain functions over the client so the JVM tests can drive them against
 * MockWebServer.
 */
internal object HomeApi {

    suspend fun config(api: ApiClient): HomeConfig = api.get("/api/config").decode()

    suspend fun health(api: ApiClient): HomeHealth = api.get("/api/health").decode()

    suspend fun rooms(api: ApiClient): List<HomeRoom> = api.get("/api/satellites").decode()

    suspend fun timers(api: ApiClient): HomeTimerList = api.get("/api/timers").decode()

    suspend fun plugins(api: ApiClient): HomePlugins = api.get("/api/plugins").decode()

    suspend fun acquisitions(api: ApiClient): HomeAcquisitions =
        api.get("/api/acquisitions?status=pending&limit=100").decode()

    suspend fun calendar(api: ApiClient, dayStartMs: Long, zone: ZoneId): List<HomeEvent> =
        api.get(calendarPath(dayStartMs, zone)).decode()

    suspend fun manual(api: ApiClient): HomeManual = api.get("/api/capabilities/manual").decode()

    /** pause / resume / stop one room — the Music page's transport. */
    suspend fun roomAction(api: ApiClient, room: String, verb: String) {
        api.post("/api/music/$verb/${enc(room)}")
    }

    /** A quiet room's "play": favorites, shuffled. Playlist 0 is the
     *  virtual Favorites list. */
    suspend fun playFavorites(api: ApiClient, room: String) {
        api.post("/api/music/play-playlist", buildJsonObject {
            put("room_id", room)
            put("playlist_id", 0)
            put("shuffle", true)
        })
    }

    /** DELETE /api/timers/{id}, which also reaches a timer set with no room. */
    suspend fun cancelTimer(api: ApiClient, t: HomeTimer, shared: Boolean): CancelOutcome = try {
        api.delete("/api/timers/${t.id}")
        CancelOutcome.Cancelled("cancelled ${timerNoun(t, shared)}")
    } catch (e: CancellationException) {
        throw e
    } catch (e: ApiException) {
        // It fired (or somebody else cancelled it) between the read and the tap.
        if (e.status == 404) CancelOutcome.Gone("that one already finished")
        else CancelOutcome.Failed(failureText("cancel", e))
    } catch (e: Exception) {
        CancelOutcome.Failed(failureText("cancel", e))
    }

    /** Stop every room in [rooms] at once; each succeeds or fails on its own. */
    suspend fun stopRooms(api: ApiClient, rooms: List<String>): StopOutcome = coroutineScope {
        val results = rooms.map { room ->
            async {
                try {
                    roomAction(api, room, "stop")
                    room to null
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    room to e
                }
            }
        }.awaitAll()
        StopOutcome(
            stopped = results.filter { it.second == null }.map { it.first },
            failed = results.mapNotNull { (room, e) -> e?.let { room to it } },
        )
    }

    /** Say something in every room; the answer lists the rooms that took it. */
    suspend fun announceAll(api: ApiClient, message: String): List<String> =
        api.post("/api/satellites/announce-all", buildJsonObject { put("message", message) })
            .decode<HomeAnnounceResult>().announced_to

    private fun enc(s: String): String = URLEncoder.encode(s, "UTF-8").replace("+", "%20")
}

/** How a cancel went, with the toast that says so. */
internal sealed interface CancelOutcome {
    val toast: String
    data class Cancelled(override val toast: String) : CancelOutcome
    data class Gone(override val toast: String) : CancelOutcome
    data class Failed(override val toast: String) : CancelOutcome
}

internal data class StopOutcome(
    val stopped: List<String>,
    val failed: List<Pair<String, Throwable>>,
)

/** "stopped 3 rooms", "stopped 2 of 3 rooms · stop failed: …", or just the failure. */
internal fun stopAllToast(total: Int, outcome: StopOutcome): String {
    if (outcome.failed.isEmpty()) return "stopped ${plural(total, "room")}"
    val said = failureText("stop", outcome.failed.first().second)
    return if (outcome.failed.size < total) {
        "stopped ${total - outcome.failed.size} of $total rooms · $said"
    } else {
        said
    }
}

/** An honest announce toast: a 200 can still mean dead connections under
 *  the server's active-sessions map, so it reads the rooms that took it. */
internal fun announceToast(delivered: List<String>, onlineCount: Int): String = when {
    delivered.isEmpty() -> "broadcast queued but no satellites accepted it (dead connections?)"
    delivered.size < onlineCount ->
        "broadcast partial — ${delivered.size}/$onlineCount reached (${delivered.joinToString(", ")})"
    else -> "broadcasted to ${plural(delivered.size, "satellite")}"
}

/** A `satellites.wifi.changed` payload: room → its new Wi-Fi report.
 *  Null when it isn't one (the push is advisory; a bad one changes nothing). */
internal fun decodeWifiPush(payload: JsonElement?): Map<String, HomeWifi>? {
    val obj = payload as? JsonObject ?: return null
    return obj.mapNotNull { (room, v) ->
        (v as? JsonObject)
            ?.let { runCatching { DomovoiJson.decodeFromJsonElement(HomeWifi.serializer(), it) }.getOrNull() }
            ?.let { room to it }
    }.toMap()
}
