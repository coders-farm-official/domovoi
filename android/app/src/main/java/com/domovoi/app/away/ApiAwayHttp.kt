package com.domovoi.app.away

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/**
 * Away chat's setup over the app's own client: the household token and the
 * device key go on as the interceptor decides (net/DeviceAuth.kt; the key
 * rides on `/api/away`, net/DeviceKey.kt), only to [server] (a call made
 * after the app moved to another server is not sent). The device id goes
 * along as the wire note asks; the house resolves the phone from its key.
 */
class ApiAwayHttp(private val api: ApiClient, private val server: String, private val deviceId: String) : AwayHttp {
    private val headers get() = mapOf("X-Domovoi-Device-Id" to deviceId)

    private inline fun call(block: () -> JsonElement): JsonElement = try {
        block()
    } catch (e: ApiException) {
        throw AwayHttpError(e.status, e.body)
    }

    override suspend fun post(path: String, body: JsonObject): JsonElement =
        call { api.post(path, body, headers, onlyTo = server) }

    override suspend fun get(path: String): JsonElement = try {
        api.raw("GET", path, trackPairing = false, headers = headers, onlyTo = server).let {
            if (it.isBlank()) kotlinx.serialization.json.JsonNull else com.domovoi.app.net.DomovoiJson.parseToJsonElement(it)
        }
    } catch (e: ApiException) {
        throw AwayHttpError(e.status, e.body)
    }

    override suspend fun delete(path: String): JsonElement = call { api.delete(path, null, headers, onlyTo = server) }
}
