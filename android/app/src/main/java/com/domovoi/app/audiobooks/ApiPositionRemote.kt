package com.domovoi.app.audiobooks

import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.player.ServerScope
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

/**
 * The connected server's slot for this phone in a book: `GET`/`POST
 * /api/audiobooks/{id}/position` with the phone's device id and listener,
 * as the player always used. [server] is the server [api] talks to now.
 */
class ApiPositionRemote(
    private val api: ApiClient,
    private val prefs: Prefs,
    override val server: ServerScope,
) : PositionRemote {

    private fun query(): String {
        val person = prefs.listenerPersonId.value
        return "?device_id=" + enc(prefs.deviceId) + (person?.let { "&person_id=" + enc(it) } ?: "")
    }

    override suspend fun get(bookId: Long): ServerPosition =
        parseServerPosition(api.get("/api/audiobooks/$bookId/position" + query()) as? JsonObject)

    override suspend fun put(bookId: Long, positionSec: Double, speed: Float) {
        api.post(
            "/api/audiobooks/$bookId/position",
            buildJsonObject {
                put("device_id", prefs.deviceId)
                put("position_sec", positionSec.toInt())
                prefs.listenerPersonId.value?.let { put("person_id", it) }
                put("speed", speed.toDouble())
            },
        )
    }

    companion object {
        private fun enc(s: String) = java.net.URLEncoder.encode(s, "UTF-8")

        /** The slot as the server answers it: `{position_sec, speed}` with
         *  `updated_at` when there is a row, without it when there is none. */
        fun parseServerPosition(obj: JsonObject?): ServerPosition = ServerPosition(
            positionSec = obj?.get("position_sec")?.jsonPrimitive?.doubleOrNull ?: 0.0,
            speed = obj?.get("speed")?.jsonPrimitive?.doubleOrNull?.toFloat() ?: 1f,
            updatedAt = obj?.get("updated_at")?.jsonPrimitive?.contentOrNull?.takeIf { it.isNotBlank() },
        )

        /** `GET /api/audiobooks/{id}/positions`: this person's places on
         *  other devices and rooms. */
        suspend fun others(api: ApiClient, prefs: Prefs, bookId: Long): List<OtherPosition> {
            val person = prefs.listenerPersonId.value
            val q = "?device_id=" + enc(prefs.deviceId) + (person?.let { "&person_id=" + enc(it) } ?: "")
            return parseOthers(api.get("/api/audiobooks/$bookId/positions$q"))
        }

        /** The list, bare or under `items` / `positions`. */
        fun parseOthers(json: kotlinx.serialization.json.JsonElement): List<OtherPosition> {
            val list = when (json) {
                is kotlinx.serialization.json.JsonArray -> json
                is JsonObject -> (json["items"] ?: json["positions"]) as? kotlinx.serialization.json.JsonArray
                else -> null
            } ?: return emptyList()
            return DomovoiJson.decodeFromJsonElement(ListSerializer(OtherPosition.serializer()), list)
        }
    }
}
