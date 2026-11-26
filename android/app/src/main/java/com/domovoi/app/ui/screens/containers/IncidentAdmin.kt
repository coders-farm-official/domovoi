package com.domovoi.app.ui.screens.containers

import com.domovoi.app.alerts.CLASS_TAMPER
import com.domovoi.app.alerts.ContainerIncident
import com.domovoi.app.alerts.IncidentsApi
import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put

/*
 * Health incidents as admin on this phone (1.5.0; design §1's third area):
 * see the details, acknowledge a failure, clear tampering after review.
 * docs/API_REFERENCE.md §3.9a: GET /api/containers/incidents is a device
 * read, and with the admin Bearer it also carries `expected`, `found`,
 * `acknowledged_by`, `cleared_by` and `clear_note`; every row carries
 * `can_acknowledge` and `can_clear`. Acknowledge is admin (Bearer); clear is
 * the security tier, with an optional note of at most 500 characters, and
 * clearing the last open tamper incident lifts the guard's freeze.
 *
 * The household list (alerts/ContainerIncidents.kt) is what Health draws;
 * this is the admin half laid over it by id. Everything here is pure, for
 * the JVM tests, apart from the small [IncidentAdminApi].
 */

/** One incident's admin half, as the Bearer read gives it. */
data class IncidentAdminFields(
    val id: Long,
    val canAcknowledge: Boolean = false,
    val canClear: Boolean = false,
    /** What the guard expected, as (key, value) rows (the dashboard's CtrDetails). */
    val expected: List<Pair<String, String>> = emptyList(),
    val found: List<Pair<String, String>> = emptyList(),
    val acknowledgedBy: String? = null,
    val clearedBy: String? = null,
    val clearNote: String? = null,
)

/** Where Health is for admin: nothing (signed out, or a shared screen),
 *  kept today (one tap shows the details), or a live session. */
enum class IncidentAdminMode { NONE, KEPT, LIVE }

fun incidentAdminMode(state: AdminSession.State, shared: Boolean): IncidentAdminMode = when {
    shared -> IncidentAdminMode.NONE
    state is AdminSession.State.SignedIn -> IncidentAdminMode.LIVE
    state is AdminSession.State.Kept -> IncidentAdminMode.KEPT
    else -> IncidentAdminMode.NONE
}

private fun JsonObject.text(key: String): String? =
    (this[key] as? JsonPrimitive)?.takeIf { it !is JsonNull }?.content?.takeIf { it.isNotBlank() }

private fun JsonObject.flag(key: String): Boolean = (this[key] as? JsonPrimitive)?.booleanOrNull == true

/** An `expected` or `found` object as rows: empty values left out, a list
 *  joined with commas ("—" when empty), a nested object as its JSON. */
fun detailRows(el: JsonElement?): List<Pair<String, String>> {
    val o = el as? JsonObject ?: return emptyList()
    return o.entries.mapNotNull { (k, v) ->
        val shown = when (v) {
            is JsonNull -> null
            is JsonPrimitive -> v.content.takeIf { it.isNotEmpty() }
            is JsonArray -> v.joinToString(", ") { (it as? JsonPrimitive)?.content ?: it.toString() }.ifEmpty { "—" }
            is JsonObject -> v.toString()
        }
        shown?.let { k to it }
    }
}

/** The admin halves of a Bearer read, by incident id. Rows without an id
 *  are skipped; a payload that isn't one is an empty map. */
fun parseAdminIncidents(el: JsonElement?): Map<Long, IncidentAdminFields> {
    val list = (el as? JsonObject)?.get("incidents") as? JsonArray ?: return emptyMap()
    return list.mapNotNull { row ->
        val o = row as? JsonObject ?: return@mapNotNull null
        val id = (o["id"] as? JsonPrimitive)?.let { it.longOrNull ?: it.content.toLongOrNull() } ?: return@mapNotNull null
        IncidentAdminFields(
            id = id,
            canAcknowledge = o.flag("can_acknowledge"),
            canClear = o.flag("can_clear"),
            expected = detailRows(o["expected"]),
            found = detailRows(o["found"]),
            acknowledgedBy = o.text("acknowledged_by"),
            clearedBy = o.text("cleared_by"),
            clearNote = o.text("clear_note"),
        )
    }.associateBy { it.id }
}

/** What clearing answered: whether the freeze is still on, and the music
 *  player image it kept as the expected one (else null). */
data class ClearResult(val frozen: Boolean, val keptImage: String?)

fun parseClearResult(el: JsonElement?): ClearResult {
    val o = el as? JsonObject
    return ClearResult(
        frozen = o?.flag("frozen") == true,
        keptImage = o?.text("kept_image"),
    )
}

/** A clear note the server takes: at most 500 characters, blank as none. */
fun clearNoteBody(note: String): JsonObject = buildJsonObject {
    val trimmed = note.trim()
    if (trimmed.isEmpty()) put("note", JsonNull) else put("note", trimmed.take(IncidentAdminWords.NOTE_MAX))
}

object IncidentAdminWords {
    /** The Containers part's line while signed out (not on a shared screen). */
    const val DASHBOARD_OR_PHONE =
        "an admin acknowledges or clears these on the dashboard's Settings → Health, or sign in as admin on this phone"

    /** The no-details card's line while signed out (not on a shared screen). */
    const val DETAILS_OR_PHONE = "an admin sees the details on the dashboard, or sign in as admin on this phone"

    /** Kept only: one tap asks for the fingerprint or face, then shows them. */
    const val UNLOCK = "show details with your fingerprint or face"

    /** Signed in: what the incidents offer here. */
    const val LIVE =
        "as admin on this phone: acknowledge a failure, or clear tampering after review"

    const val EXPECTED = "expected"
    const val FOUND = "found"
    const val ACKNOWLEDGE = "acknowledge"
    const val ACKNOWLEDGE_HINT = "hides the banner until something new happens; it still resolves by itself"
    const val CLEAR = "clear after review"
    const val CLEAR_HINT = "risky changes stay paused until every tampering incident is cleared"
    const val CLEAR_TITLE = "Clear this tampering incident?"
    const val CLEAR_BODY =
        "Clear this only once you know why it happened: you swapped the image yourself, or you removed what " +
            "shouldn't be there. If it is still there it stays accepted; anything different opens a new incident. " +
            "Clearing the last one resumes the changes it paused."
    const val NOTE_LABEL = "what you checked (optional)"
    const val NOTE_MAX = 500
    const val CLEAR_CONFIRM = "clear"
    const val CANCEL = "cancel"
    const val ACKNOWLEDGED = "acknowledged"

    /** "acknowledged by …", "cleared by … · the note", or null. */
    fun who(f: IncidentAdminFields): String? {
        val parts = mutableListOf<String>()
        f.acknowledgedBy?.let { parts += "acknowledged by $it" }
        f.clearedBy?.let { parts += "cleared by $it" + (f.clearNote?.let { n -> " · $n" } ?: "") }
        return parts.takeIf { it.isNotEmpty() }?.joinToString(" · ")
    }

    /** What clearing did, from the server's answer. */
    fun cleared(r: ClearResult): String {
        val head = if (r.frozen) "cleared · $CLEAR_HINT" else "cleared · paused changes resume"
        return if (r.keptImage != null) "$head · the music player image it found is now the one expected" else head
    }

    /** A refused acknowledge or clear, in plain words. */
    fun refusal(status: Int, detail: String?): String = when (status) {
        409 -> detail ?: "this incident changed; Health was read again"
        404 -> "that incident is gone"
        501 -> "set up an admin on the dashboard first"
        else -> detail ?: "that didn't work (HTTP $status)"
    }
}

/** Whether [inc] is one the phone offers to clear (a tamper incident still
 *  open) — the server's `can_clear`, checked against the household row so a
 *  stale admin read never offers it on a resolved one. */
fun offersClear(inc: ContainerIncident, f: IncidentAdminFields?): Boolean =
    f?.canClear == true && inc.open && inc.cls == CLASS_TAMPER

fun offersAcknowledge(inc: ContainerIncident, f: IncidentAdminFields?): Boolean =
    f?.canAcknowledge == true && inc.open && !inc.tamper && !inc.acknowledged

/** The admin routes Health uses, each through the phone's admin sign-in:
 *  [headers] and [onlyTo] come from AdminSession.call / act per call. */
class IncidentAdminApi(private val api: ApiClient) {
    suspend fun read(headers: Map<String, String>, onlyTo: String): Map<Long, IncidentAdminFields> =
        parseAdminIncidents(DomovoiJson.parseToJsonElement(api.raw("GET", IncidentsApi.INCIDENTS_PATH, headers = headers, onlyTo = onlyTo)))

    suspend fun acknowledge(id: Long, headers: Map<String, String>, onlyTo: String) {
        api.post("${IncidentsApi.INCIDENTS_PATH}/$id/acknowledge", buildJsonObject { }, headers, onlyTo = onlyTo)
    }

    suspend fun clear(id: Long, note: String, headers: Map<String, String>, onlyTo: String): ClearResult =
        parseClearResult(api.post("${IncidentsApi.INCIDENTS_PATH}/$id/clear", clearNoteBody(note), headers, onlyTo = onlyTo))
}
