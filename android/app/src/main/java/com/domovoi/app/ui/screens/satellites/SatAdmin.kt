package com.domovoi.app.ui.screens.satellites

import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.ApiClient
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

/*
 * A room as admin on this phone (1.5.0; design §1's fourth area): restart it,
 * upgrade its software, change its settings. All three are admin on the
 * server (docs/API_REFERENCE.md §3.7: restart `require_admin_mutation`,
 * upgrade and the settings save `require_admin_security`, Bearer-only), so
 * they are offered only while this phone holds admin (net/AdminSession.kt:
 * signed in, or kept today) and never on a shared screen, and each goes
 * through AdminSession.act (the fingerprint or face first when needed).
 * Signed out, the phone shows what it always did, and the "on the
 * dashboard" lines add "or sign in as admin on this phone".
 *
 * Left for the dashboard: the Advanced settings (a wrong audio device, LED
 * or mic-gain value can leave the room without a mic or sound until someone
 * fixes config.toml on the device), reset pairing, removing a room, the mic
 * DSP presets, the room's voice, a video room's display, moving or renaming
 * a device, diagnostics and logs, and support sessions.
 */

/** Whether a room's admin controls are offered: admin held here (signed in
 *  or kept today), and not a shared screen. */
fun roomAdminHeld(state: AdminSession.State, shared: Boolean): Boolean =
    !shared && state !is AdminSession.State.SignedOut

/** What the device declared (S0): a Pi sends no list, so everything. */
fun satCan(s: Satellite, ability: String): Boolean = s.supports?.contains(ability) ?: true

object RoomAdminWords {
    /** The maintenance line, signed out (not on a shared screen). */
    const val MAINTENANCE_OR_PHONE =
        "restarting and upgrading a room are done from the dashboard on a computer, or sign in as admin on this phone"

    /** The settings line, signed out (not on a shared screen). */
    const val CONFIG_OR_PHONE =
        "what this room is set to. Changing any of it is done from the dashboard on a computer, " +
            "or sign in as admin on this phone."

    /** Above the editor, while admin is held. */
    const val CONFIG_EDIT =
        "Saving sends the changes to the room, which restarts to apply them: it drops offline for a few seconds."

    /** The Advanced settings stay read-only on the phone. */
    const val ADVANCED_ON_DASHBOARD =
        "These can take the room offline (a wrong audio device, LED or mic-gain value leaves it without a mic or " +
            "sound until its config is fixed on the device), so they are changed from the dashboard on a computer."

    const val RESTART = "Restart room"
    const val UPGRADE = "Upgrade room"
    const val UPGRADING = "Upgrading…"
    const val SAVE = "Save and restart"
    const val SAVING = "Saving…"
    const val CANCEL = "cancel"
    const val UNDO = "undo changes"

    /** The dashboard's confirm, word for word (satellites.jsx restartSat). */
    fun restartConfirm(room: String): String =
        "Restart the $room room? It'll drop offline for a few seconds while the service bounces."

    /** The dashboard's confirm, word for word (satellites.jsx upgradeSat). */
    fun upgradeConfirm(room: String, coreSha: String?): String =
        "Upgrade the $room room to ${coreSha ?: "the latest domovoi code"}?\n\n" +
            "It pulls the new room software from the Domovoi server, backs up its current " +
            "tree, and restarts — dropping offline for a few seconds. If the new code won't " +
            "start, it rolls back automatically.\n\n" +
            "Some updates finish at the next boot: if the Health section of its Settings tab then says " +
            "\"reboot needed\", unplug it for a few seconds and plug it back in."

    /** The dashboard's confirm, word for word (satellites.jsx RoomSettingsBody). */
    fun saveConfirm(room: String): String =
        "Save and restart the $room room? It'll drop offline for a few seconds while it applies the change."

    fun offline(room: String) = "$room is offline"
    fun upgradingAlready(room: String) = "$room is upgrading — it restarts itself when the new code is in"
    fun restarting(room: String) = "restarting $room…"
    fun upgrading(room: String) = "upgrading $room…"

    /** A device that restarts or updates itself (it didn't offer the ability). */
    fun notOffered(canRestart: Boolean, canUpgrade: Boolean): String? = when {
        canRestart && canUpgrade -> null
        !canRestart && !canUpgrade -> "this device restarts and updates itself"
        !canRestart -> "this device restarts itself"
        else -> "this device updates itself"
    }

    /** What a save did, from the server's answer. */
    fun saved(room: String, r: ConfigSaveResult): String = when {
        r.restarting && r.rejected.isEmpty() -> "$room restarting to apply…"
        r.restarting -> "$room restarting to apply… (${r.rejected.size} not saved)"
        r.rejected.isNotEmpty() -> "nothing saved: ${r.rejected.values.first()}"
        else -> "nothing to save"
    }
}

// ---- the settings editor's rules (the dashboard's ConfigField) -------------

/** A field the phone may change: not Advanced, a kind it can edit, and one
 *  the room's software has (`supported` false: upgrade it first). */
fun SatConfigField.editableHere(): Boolean =
    section != "advanced" && supported != false && type in EDITABLE_TYPES

private val EDITABLE_TYPES = setOf("bool", "choice", "int", "float", "str")

/** Why an edited value can't be saved, or null (`configFieldProblem`,
 *  components.jsx). Only numbers have rules; a number being typed is text
 *  until it is saved. */
fun configEditProblem(f: SatConfigField, value: JsonPrimitive?): String? {
    if (f.type != "int" && f.type != "float") return null
    if (value == null || value is JsonNull) return null
    val n = value.content.trim().toDoubleOrNull()?.takeIf { it.isFinite() }
        ?: return if (f.type == "int") "expected a whole number" else "expected a number"
    if (f.type == "int" && n != Math.floor(n)) return "expected a whole number"
    f.min?.let { if (n < it) return "must be at least ${num(it)}" }
    f.max?.let { if (n > it) return "must be at most ${num(it)}" }
    return null
}

private fun num(d: Double): String = if (d == Math.floor(d) && !d.isInfinite()) d.toLong().toString() else d.toString()

/** A typed number as the value the dashboard would send: an int field's a
 *  whole number, a float's a decimal; text that isn't one stays text (and
 *  [configEditProblem] says so). Blank is no value. */
fun typedNumber(f: SatConfigField, text: String): JsonPrimitive? {
    val t = text.trim()
    if (t.isEmpty()) return null
    val d = t.toDoubleOrNull()?.takeIf { it.isFinite() } ?: return JsonPrimitive(t)
    return if (f.type == "int" && d == Math.floor(d) && kotlin.math.abs(d) < 1e15) JsonPrimitive(d.toLong()) else JsonPrimitive(d)
}

/** The edits that can't be saved yet. */
fun configEditProblems(fields: List<SatConfigField>, edits: Map<String, JsonPrimitive?>): List<String> =
    edits.keys.filter { name -> fields.firstOrNull { it.name == name }?.let { configEditProblem(it, edits[name]) } != null }

/** Only the changes, as the server takes them (a typed number as a
 *  number): an edit equal to the reported value is no change. */
fun configChanges(fields: List<SatConfigField>, edits: Map<String, JsonPrimitive?>): Map<String, JsonPrimitive> =
    edits.mapNotNull { (name, typed) ->
        val f = fields.firstOrNull { it.name == name } ?: return@mapNotNull null
        if (typed == null || !f.editableHere()) return@mapNotNull null
        val v = if (f.type == "int" || f.type == "float") typedNumber(f, typed.content) ?: return@mapNotNull null else typed
        if (sameValue(f.value, v)) null else name to v
    }.toMap()

private fun sameValue(a: JsonElement?, b: JsonPrimitive): Boolean {
    val p = a as? JsonPrimitive ?: return false
    if (p.isString != b.isString) return p.doubleOrNull != null && p.doubleOrNull == b.doubleOrNull
    return p.content == b.content || (p.doubleOrNull != null && p.doubleOrNull == b.doubleOrNull)
}

/** The PATCH body: `{"changes": {...}}`. */
fun configSaveBody(changes: Map<String, JsonPrimitive>): JsonObject = buildJsonObject {
    put("changes", JsonObject(changes))
}

/** PATCH /api/satellites/{room}/config's answer: `{sent, rejected, restarting}`. */
data class ConfigSaveResult(val sent: List<String>, val rejected: Map<String, String>, val restarting: Boolean)

fun parseConfigSave(e: JsonElement?): ConfigSaveResult {
    val o = e as? JsonObject
    return ConfigSaveResult(
        sent = runCatching { o?.get("sent")?.jsonArray?.map { it.jsonPrimitive.content } }.getOrNull().orEmpty(),
        rejected = (o?.get("rejected") as? JsonObject).orEmpty()
            .mapValues { (_, v) -> (v as? JsonPrimitive)?.content.orEmpty() },
        restarting = (o?.get("restarting") as? JsonPrimitive)?.booleanOrNull == true,
    )
}

// ---- the routes, each through the phone's admin sign-in ---------------------

/** [headers] and [onlyTo] come from AdminSession.call / act, per call. */
class RoomAdminApi(private val api: ApiClient) {
    suspend fun restart(room: String, headers: Map<String, String>, onlyTo: String) {
        api.post(satPath(room, "/restart"), buildJsonObject { }, headers, onlyTo = onlyTo)
    }

    suspend fun upgrade(room: String, headers: Map<String, String>, onlyTo: String, nowMs: Long = System.currentTimeMillis()): SatUpgradePress =
        requestSatUpgrade(api, room, nowMs, headers, onlyTo)

    suspend fun saveConfig(room: String, changes: Map<String, JsonPrimitive>, headers: Map<String, String>, onlyTo: String): ConfigSaveResult =
        parseConfigSave(api.patch(satPath(room, "/config"), configSaveBody(changes), headers, onlyTo = onlyTo))
}
