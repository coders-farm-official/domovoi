package com.domovoi.app.ui.screens.files

import com.domovoi.app.AppContainer
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import com.domovoi.app.net.failureText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Files → Documents → New: the dashboard's "+ New" menu (files.jsx
 * NEW_KINDS), limited to what this app opens in-app. Drawing is left out:
 * the app has no drawing editor, and a new drawing would open nowhere.
 */
internal enum class NewDocKind(
    /** `kind` for POST /api/documents/create. */
    val api: String,
    val label: String,
    val hint: String,
    /** "New document name", as the dashboard asks. */
    val prompt: String,
    val placeholder: String,
) {
    Document("doc", "Document", "Fonts, pictures, tables", "new document", "e.g. Letter to school"),
    Spreadsheet("sheet", "Spreadsheet", "Workbook · .xlsx", "new spreadsheet", "e.g. Budget"),
    Text("text", "Text file", "Plain text · name as typed", "new text file", "e.g. shopping.txt"),
}

/** The server's answer (documents.py DocumentRow): the fields this app uses. */
@Serializable
internal data class CreatedDocument(
    @SerialName("rel_path") val relPath: String,
    val name: String = "",
    val ext: String = "",
    val size: Long = 0,
    val category: String = "text",
)

/** The create request: name as typed (the server applies the name rules
 *  and the kind's extension) and this phone's id, so a blocked device is
 *  refused as it is for uploads. */
internal fun newDocumentBody(name: String, kind: NewDocKind, deviceId: String): JsonObject = buildJsonObject {
    put("name", name.trim())
    put("kind", kind.api)
    if (deviceId.length in 3..64) put("device_id", deviceId)
}

/** The new file as a Files row, so it opens exactly as tapping it would. */
internal fun CreatedDocument.asEntry(): FileEntry {
    val fileName = relPath.substringAfterLast('/')
    return FileEntry(name = fileName, rel = relPath, kind = fileKind(fileName, false), size = size)
}

/** Why a create was refused, in plain words. A name already taken says so
 *  and asks for another; any other refusal is the server's own sentence
 *  (bad name, read-only, this device blocked). */
internal fun createFailureMessage(status: Int?, detail: String?): String {
    val d = detail?.trim().orEmpty()
    return when {
        status == 409 -> {
            val taken = d.removeSuffix(" already exists").takeIf { it != d && it.isNotBlank() }
            if (taken != null) "There's already a file called \"$taken\". Pick another name."
            else "A file with that name already exists. Pick another name."
        }
        d.isNotEmpty() -> d
        status == 401 || status == 403 -> "This device can't make files in Documents."
        else -> "Couldn't make the file."
    }
}

internal fun createFailureText(e: Throwable): String {
    if (e is ApiException) {
        val detail = runCatching {
            ((DomovoiJson.parseToJsonElement(e.body) as? JsonObject)?.get("detail") as? JsonPrimitive)
                ?.takeIf { it.isString }?.content
        }.getOrNull()
        return createFailureMessage(e.status, detail)
    }
    return failureText("create", e)
}

internal suspend fun createDocument(app: AppContainer, name: String, kind: NewDocKind): CreatedDocument =
    withContext(Dispatchers.IO) {
        app.api.post("/api/documents/create", newDocumentBody(name, kind, app.prefs.deviceId)).decode<CreatedDocument>()
    }
