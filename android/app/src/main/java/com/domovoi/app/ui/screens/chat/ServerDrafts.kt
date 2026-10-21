package com.domovoi.app.ui.screens.chat

import com.domovoi.app.AppContainer
import com.domovoi.app.net.ApiException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/** A home-server chat reply's `draft` (web/backend/api/chat_drafts.py,
 *  V039): the draft and the version this turn wrote, or offered to print. */
@Serializable
internal data class ServerDraftRef(
    val id: Long = 0,
    val version: Int = 1,
    val kind: String = "letter",
    val title: String = "",
    val template: String = "letter-block",
    val words: Int = 0,
    val action: String = "create",
    val change: String = "",
    val parts: Int? = null,
)

/**
 * A home-server chat's draft, through `/api/chat/drafts/{id}`: its versions
 * are the household's (kept on the server), a save goes into the server's
 * Documents → From Chat with the server's own ATS self-check, and Print is
 * the server's Print sheet (the home's printer). Share downloads the file
 * once into the app's cache.
 */
internal class ServerDraftDoc(private val app: AppContainer, private val id: Long) : DraftDoc {
    override val key: String = "server:${app.api.absolute("")}#$id"
    override val saveWhere: String = "your home server's Documents → From Chat"

    @Volatile private var cache: DraftView? = null

    override suspend fun load(): DraftView = withContext(Dispatchers.IO) {
        val r = app.api.get("/api/chat/drafts/$id").jsonObject
        val d = r["draft"]!!.jsonObject
        fun JsonObject.s(k: String) = this[k]?.takeIf { it !is JsonNull }?.jsonPrimitive?.contentOrNull
        val versions = r["versions"]!!.jsonArray.map { e ->
            val v = e.jsonObject
            val changes = (v["changes"] as? JsonArray)?.map { op -> op.jsonArray[0].jsonPrimitive.content to op.jsonArray[1].jsonPrimitive.content }
            val saved = (v["saved"] as? JsonArray).orEmpty().map { s ->
                val o = s.jsonObject
                val check = o["check"] as? JsonObject
                "home server · ${o.s("rel_path").orEmpty()} · ${check?.s("line").orEmpty()}"
            }
            DraftVersionView(
                version = v["version"]!!.jsonPrimitive.intOrNull ?: 1,
                body = v.s("body").orEmpty(),
                display = v.s("display").orEmpty(),
                changes = changes,
                source = v.s("source") ?: "first",
                instruction = v.s("instruction"),
                saved = saved,
                basedOn = v["based_on"]?.takeIf { it !is JsonNull }?.jsonPrimitive?.intOrNull,
                // "edited by you" only on the install that made the edit.
                editedOn = if (v.s("device_id") == app.prefs.deviceId) null
                else v.s("device_name") ?: v.s("device_id") ?: "another device",
            )
        }
        DraftView(d.s("title").orEmpty(), d.s("kind") ?: "letter", d.s("format") ?: "docx", versions).also { cache = it }
    }

    override suspend fun firstLines(version: Int): String? =
        (cache ?: runCatching { load() }.getOrNull())?.versions?.firstOrNull { it.version == version }?.body?.let(::firstLinesOf)

    override suspend fun edit(text: String, base: Int): Int? = withContext(Dispatchers.IO) {
        try {
            val r = app.api.post("/api/chat/drafts/$id/versions", buildJsonObject {
                put("text", text)
                put("base_version", base)
                put("device_id", app.prefs.deviceId)
            }).jsonObject
            r["version"]?.jsonObject?.get("version")?.jsonPrimitive?.intOrNull ?: base
        } catch (e: ApiException) {
            if (e.status == 409) null else throw e
        }
    }

    override suspend fun save(version: Int, format: String): DraftSaveResult = withContext(Dispatchers.IO) {
        val r = app.api.post("/api/chat/drafts/$id/save", buildJsonObject {
            put("version", version)
            put("format", format)
            put("device_id", app.prefs.deviceId)
        }).jsonObject
        val check = r["check"]?.jsonObject
        DraftSaveResult(
            "home server · Documents/${r["rel_path"]?.jsonPrimitive?.contentOrNull.orEmpty()}",
            check?.get("line")?.jsonPrimitive?.contentOrNull.orEmpty(),
            check?.get("ok")?.jsonPrimitive?.booleanOrNull == true,
        )
    }

    override suspend fun file(version: Int, format: String): Pair<String, ByteArray> = withContext(Dispatchers.IO) {
        val req = Request.Builder().url(app.api.absolute("/api/chat/drafts/$id/export?version=$version&format=$format"))
            .header("X-Requested-With", "DomovoiApp").get().build()
        app.api.http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) throw java.io.IOException("the home server said ${resp.code}")
            val title = (cache?.title ?: "Draft").replace(Regex("[\\\\/:*?\"<>|]+"), " ").trim().ifEmpty { "Draft" }
            "$title v$version.$format" to (resp.body?.bytes() ?: ByteArray(0))
        }
    }
}

/** A home-server chat's draft card: [ref] is the version its reply wrote
 *  (or offered to print); a newer one in the thread shows as "latest vN".
 *  Save puts the kind's default format into the server's From Chat. */
@androidx.compose.runtime.Composable
internal fun ServerDraftCard(
    ref: ServerDraftRef,
    line: String,
    transcript: List<LiveMessage>,
    doc: ServerDraftDoc,
    reloadKey: Int,
    canPrint: Boolean,
    onOpen: () -> Unit,
    onPrint: () -> Unit,
    onRevise: ((String) -> Unit)? = null,
) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val toast = com.domovoi.app.LocalToast.current
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    val newest = transcript.mapNotNull { it.draft }.filter { it.id == ref.id }.maxOfOrNull { it.version } ?: ref.version
    val first = androidx.compose.runtime.produceState<String?>(null, ref.id, ref.version, reloadKey) {
        value = runCatching { doc.firstLines(ref.version) }.getOrNull()
    }.value
    val format = if (ref.kind == "letter") "pdf" else "docx"
    DraftCard(
        DraftCardData(ref.id.toString(), ref.version, ref.kind, ref.title, ref.action, line,
            latest = newest.takeIf { it > ref.version }, firstLines = if (newest == ref.version) first else null),
        canPrint = canPrint,
        onOpen = onOpen,
        onSave = {
            scope.launch {
                runCatching { doc.save(ref.version, format) }
                    .onSuccess { toast("saved ${it.where} · ${it.check}") }
                    .onFailure { toast(com.domovoi.app.net.failureText("save", it)) }
            }
        },
        onShare = {
            scope.launch {
                runCatching { doc.file(ref.version, format) }
                    .onSuccess { (name, bytes) -> if (!shareDraftFile(context, name, bytes)) toast("no app on this phone can share it") }
                    .onFailure { toast(com.domovoi.app.net.failureText("share", it)) }
            }
        },
        onPrint = onPrint,
        onRevise = onRevise,
    )
}

/** "Send to home": the file into the home server's Documents → From Chat
 *  (the existing upload route, `folder=From Chat`); where it landed. */
internal suspend fun sendDraftHome(app: com.domovoi.app.AppContainer, name: String, bytes: ByteArray): String =
    withContext(Dispatchers.IO) {
        val type = if (name.endsWith(".pdf")) "application/pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        val form = okhttp3.MultipartBody.Builder().setType(okhttp3.MultipartBody.FORM)
            .addFormDataPart("files", name, bytes.toRequestBody(type.toMediaType()))
            .addFormDataPart("folder", "From Chat")
            .addFormDataPart("device_id", app.prefs.deviceId)
            .build()
        val r = app.api.upload("/api/documents/upload", form)
        (r as? kotlinx.serialization.json.JsonObject)?.get("saved")?.let { it as? kotlinx.serialization.json.JsonArray }
            ?.firstOrNull()?.let { (it as kotlinx.serialization.json.JsonPrimitive).content } ?: "From Chat/$name"
    }


