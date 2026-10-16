package com.domovoi.app.net

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

/**
 * Sends a reply report ("report this reply", assistant/ReplyReport.kt) to
 * the Coders Farm HTTPS address the owner set in REPORT_DESTINATION, when
 * the person taps "send to coders farm". Nothing else ever goes there.
 *
 * Over the app's own client with its cleartext rule, but without the
 * household token (DeviceAuthInterceptor removed: the address is not the
 * household's server, and its token must never reach it) and without the
 * redirect follower (a 3xx is not "received"; a report is never re-posted to
 * wherever a redirect points). https only, checked again here. True only
 * for a 2xx answer.
 */
class ReportUpload internal constructor(base: OkHttpClient, private val allowHttpForTests: Boolean) {
    constructor(base: OkHttpClient) : this(base, allowHttpForTests = false)

    private val http: OkHttpClient = base.newBuilder()
        .apply {
            interceptors().removeAll { it is DeviceAuthInterceptor || it === RedirectPolicy.interceptor }
        }
        .followRedirects(false)
        .followSslRedirects(false)
        .pingInterval(0, TimeUnit.SECONDS)
        .callTimeout(20, TimeUnit.SECONDS)
        .build()

    suspend fun post(url: String, json: String): Boolean = withContext(Dispatchers.IO) {
        val https = url.startsWith("https://", ignoreCase = true)
        if (!https && !allowHttpForTests) return@withContext false
        val request = Request.Builder()
            .url(url)
            .header("Accept", "application/json")
            .post(json.toRequestBody(JSON))
            .build()
        runCatching { http.newCall(request).execute().use { it.code in 200..299 } }.getOrDefault(false)
    }

    private companion object {
        val JSON = "application/json; charset=utf-8".toMediaType()
    }
}
