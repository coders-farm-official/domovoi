package com.domovoi.app.ui.screens.printing

import android.graphics.BitmapFactory
import android.util.Base64
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.decode
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * The /api/printers, /api/print and /api/scan calls (the web backend on
 * :6369, device tier; ApiClient adds the household token and
 * X-Requested-With). Bodies are built by hand: DomovoiJson drops nulls, and
 * the household switches need an explicit null ("follow the printer").
 */
internal class PrintIo(private val api: ApiClient) {

    suspend fun printers(refresh: Boolean = false): PrintersResponse =
        api.get(if (refresh) "/api/printers?refresh=1" else "/api/printers").decode()

    suspend fun discover(lookAgain: Boolean): DiscoverResponse =
        api.post("/api/printers/discover", buildJsonObject { put("look_again", lookAgain) }).decode()

    /** Add a found printer ([key]) or one typed by [address]. */
    suspend fun add(key: String? = null, address: String? = null, name: String?, roomId: String?): Printer =
        api.post(
            "/api/printers",
            buildJsonObject {
                key?.let { put("key", it) }
                address?.let { put("address", it) }
                name?.trim()?.takeIf { it.isNotEmpty() }?.let { put("name", it) }
                roomId?.let { put("room_id", it) }
            },
        ).decode()

    suspend fun setDefault(id: Long, on: Boolean): Printer =
        api.patch("/api/printers/$id", buildJsonObject { put("is_default", on) }).decode()

    suspend fun setRooms(id: Long, rooms: List<String>): Printer =
        api.patch(
            "/api/printers/$id",
            buildJsonObject { put("rooms", buildJsonArray { rooms.forEach { add(JsonPrimitive(it)) } }) },
        ).decode()

    suspend fun setTwoSidedDefault(id: Long, v: Boolean?): Printer =
        api.patch("/api/printers/$id", JsonObject(mapOf("two_sided_default" to tri(v)))).decode()

    suspend fun setColorDefault(id: Long, v: Boolean?): Printer =
        api.patch("/api/printers/$id", JsonObject(mapOf("color_default" to tri(v)))).decode()

    suspend fun identify(id: Long) { api.post("/api/printers/$id/identify") }

    suspend fun testPage(id: Long): PrintJob = api.post("/api/printers/$id/test-page").decode()

    suspend fun jobs(printerId: Long, limit: Int = 10): List<PrintJob> =
        api.get("/api/print/jobs?printer_id=$printerId&limit=$limit").decode<PrintJobsResponse>().jobs

    suspend fun job(id: Long): PrintJob = api.get("/api/print/jobs/$id").decode()

    suspend fun cancelJob(id: Long): PrintJob = api.post("/api/print/jobs/$id/cancel").decode()

    suspend fun preview(libraryId: String, path: String, printerId: Long?, options: PrintOptions): PrintPreview =
        preview(PrintTarget.File(libraryId, path), printerId, options)

    suspend fun print(libraryId: String, path: String, printerId: Long?, options: PrintOptions): PrintJob =
        print(PrintTarget.File(libraryId, path), printerId, options)

    suspend fun preview(target: PrintTarget, printerId: Long?, options: PrintOptions): PrintPreview =
        api.post(target.previewPath, target.body(printerId, options)).decode()

    suspend fun print(target: PrintTarget, printerId: Long?, options: PrintOptions): PrintJob =
        api.post(target.submitPath, target.body(printerId, options)).decode()

    suspend fun startScan(printerId: Long?, source: String, color: String?, resolution: Int?): ScanJob =
        api.post(
            "/api/scan/jobs",
            buildJsonObject {
                printerId?.let { put("printer_id", it) }
                put("source", source)
                color?.let { put("color", it) }
                resolution?.let { put("resolution", it) }
            },
        ).decode()

    suspend fun scan(id: String): ScanJob = api.get("/api/scan/jobs/${enc(id)}").decode()
    suspend fun scanNext(id: String): ScanJob = api.post("/api/scan/jobs/${enc(id)}/next").decode()
    suspend fun scanFinish(id: String): ScanJob = api.post("/api/scan/jobs/${enc(id)}/finish").decode()
    suspend fun scanCancel(id: String): ScanJob = api.post("/api/scan/jobs/${enc(id)}/cancel").decode()

    private fun enc(s: String) = android.net.Uri.encode(s)

    private fun tri(v: Boolean?) = if (v == null) JsonNull else JsonPrimitive(v)
}

/** What the Print sheet prints: a file in Documents or Files, or a version
 *  of a home-server chat's draft (documents plan D4), each through its own
 *  routes; the options and the preview are the same. */
internal sealed interface PrintTarget {
    val previewPath: String
    val submitPath: String
    fun body(printerId: Long?, options: PrintOptions): JsonObject

    data class File(val libraryId: String, val path: String) : PrintTarget {
        override val previewPath = "/api/print/preview"
        override val submitPath = "/api/print/jobs"
        override fun body(printerId: Long?, options: PrintOptions) = printBody(libraryId, path, printerId, options)
    }

    data class ChatDraft(val draftId: Long, val version: Int) : PrintTarget {
        override val previewPath = "/api/chat/drafts/$draftId/print/preview"
        override val submitPath = "/api/chat/drafts/$draftId/print/jobs"
        override fun body(printerId: Long?, options: PrintOptions) = printBody(null, null, printerId, options, version)
    }
}

internal fun printBody(libraryId: String?, path: String?, printerId: Long?, options: PrintOptions, version: Int? = null): JsonObject =
    buildJsonObject {
        libraryId?.let { put("library_id", it) }
        path?.let { put("path", it) }
        version?.let { put("version", it) }
        put("printer_id", printerId?.let { JsonPrimitive(it) } ?: JsonNull)
        put("options", buildJsonObject {
            options.pages?.trim()?.takeIf { it.isNotEmpty() }?.let { put("pages", it) }
            options.copies?.let { put("copies", it) }
            options.sides?.let { put("sides", it) }
            options.color?.let { put("color", it) }
            options.media?.let { put("media", it) }
        })
    }

/** A thumbnail's pixels, or null when the src isn't a base64 image (never fetched). */
internal fun decodeThumb(src: String): ImageBitmap? {
    val b64 = dataUriBase64(src) ?: return null
    return runCatching {
        val bytes = Base64.decode(b64, Base64.DEFAULT)
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
    }.getOrNull()
}
