package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.failureText
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.net.LocalSharedScreen
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray

/*
 * The Domovoi server, as admin on this phone (1.5.0; design §1's server
 * area): install an update, restart. The dashboard's Settings → About &
 * updates → Version card (web/static/settings.jsx VersionSection, and
 * restartDomovoiServer in components.jsx), cut to those two jobs: check for
 * updates (device tier), Update Domovoi (pull: admin security; on a server
 * whose update unit can restart unattended, pull then the restart, one
 * press), Restart to apply changes and Restart Domovoi (admin security).
 * Each mutation goes through AdminSession.act, after the dashboard's own
 * confirm. Offered only while this phone holds admin and never on a shared
 * screen; signed out, the About line about the server's build adds "or
 * sign in as admin on this phone".
 *
 * Left for the dashboard: the update channel switch, a Windows or Mac
 * install's host step (so Update Domovoi itself, where there is one),
 * signed-update badges, the last update's warnings and the restart-by-hand
 * command line.
 */

/** GET /api/config/version, the parts this card reads. */
data class ServerVersion(
    val version: String? = null,
    val sha: String? = null,
    val runningSha: String? = null,
    val checkoutSha: String? = null,
    val startedAt: String? = null,
    val restartRequired: Boolean = false,
    val codeRestartRequired: Boolean? = null,
    val restartInProgress: Boolean = false,
    val restartCapable: Boolean = false,
    val restartMode: String? = null,
    val restartHint: String? = null,
    val badSha: String? = null,
    val lastUpdate: LastUpdate? = null,
    val pluginsPending: List<String> = emptyList(),
    /** A Windows or Mac install's host side (`windows_side.present`):
     *  "windows", "mac" or null. */
    val hostSide: String? = null,
) {
    data class LastUpdate(
        val status: String? = null,
        val mode: String? = null,
        val startedAt: String? = null,
        val error: String? = null,
        val fromSha: String? = null,
        val toSha: String? = null,
    )

    /** `pendingRestart(v).code`. */
    val codePending: Boolean get() = codeRestartRequired ?: restartRequired
    val updateUnit: Boolean get() = restartMode == "update"
}

private fun JsonObject.str(k: String): String? = (this[k] as? JsonPrimitive)?.takeIf { it.isString }?.content
private fun JsonObject.bool(k: String): Boolean? = (this[k] as? JsonPrimitive)?.booleanOrNull
private fun JsonObject.any(k: String): String? = (this[k] as? JsonPrimitive)?.takeIf { it !is kotlinx.serialization.json.JsonNull }?.contentOrNull

fun parseServerVersion(e: JsonElement?): ServerVersion? {
    val o = e as? JsonObject ?: return null
    val last = (o["last_update"] as? JsonObject)?.let {
        ServerVersion.LastUpdate(it.str("status"), it.str("mode"), it.any("started_at"), it.str("error"), it.str("from_sha"), it.str("to_sha"))
    }
    val plugins = runCatching {
        o["plugins_pending_restart"]?.jsonArray?.mapNotNull { p ->
            val po = p as? JsonObject ?: return@mapNotNull null
            "${po.str("slug") ?: "?"} ${po.any("from_version") ?: "?"} → ${po.any("to_version") ?: "?"}"
        }
    }.getOrNull().orEmpty()
    val host = (o["windows_side"] as? JsonObject)?.takeIf { it.bool("present") == true }?.let { side ->
        val kind = side.str("kind") ?: "windows"
        // A Mac side without the updater is updated by hand: no host step.
        if (kind == "mac" && side.bool("updates") != true) null else kind
    }
    return ServerVersion(
        version = o.str("version"), sha = o.str("sha"), runningSha = o.str("running_sha"), checkoutSha = o.str("checkout_sha"),
        startedAt = o.any("started_at"),
        restartRequired = o.bool("restart_required") == true,
        codeRestartRequired = o.bool("code_restart_required"),
        restartInProgress = o.bool("restart_in_progress") == true,
        restartCapable = o.bool("restart_capable") == true,
        restartMode = o.str("restart_mode"), restartHint = o.str("restart_hint"), badSha = o.str("bad_sha"),
        lastUpdate = last, pluginsPending = plugins, hostSide = host,
    )
}

/** POST /api/config/version/check's answer. */
data class UpdateCheck(
    val upstream: Boolean = false,
    val channel: String? = null,
    val behind: Int? = null,
    val ahead: Int? = null,
    val upstreamVersion: String? = null,
    val upstreamSha: String? = null,
    val latestRelease: String? = null,
    val error: String? = null,
)

fun parseUpdateCheck(e: JsonElement?): UpdateCheck {
    val o = e as? JsonObject ?: return UpdateCheck()
    return UpdateCheck(
        upstream = (o["upstream"] as? JsonPrimitive)?.let { it.booleanOrNull ?: it.contentOrNull?.isNotBlank() } == true,
        channel = o.str("channel"), behind = (o["behind"] as? JsonPrimitive)?.intOrNull, ahead = (o["ahead"] as? JsonPrimitive)?.intOrNull,
        upstreamVersion = o.str("upstream_version"), upstreamSha = o.str("upstream_sha"), latestRelease = o.str("latest_release"),
        error = o.str("error"),
    )
}

/** What the card offers, in the dashboard's order: a restart under way,
 *  a restart waiting, an update to pull (or one held back), a check. */
enum class ServerMode { Busy, Restart, Pull, Held, Check }

fun serverMode(v: ServerVersion, check: UpdateCheck?, following: Boolean): ServerMode = when {
    following || v.restartInProgress -> ServerMode.Busy
    v.restartRequired -> ServerMode.Restart
    (check?.takeIf { it.upstream }?.behind ?: 0) > 0 ->
        if (v.badSha != null && check?.upstreamSha == v.badSha) ServerMode.Held else ServerMode.Pull
    else -> ServerMode.Check
}

/** One press (pull, then the restart) where the update unit can run the
 *  restart unattended; else two steps. Never with a host side here. */
fun onePress(v: ServerVersion): Boolean = v.updateUnit && v.restartCapable && v.hostSide == null

/** `unitAppliedSha`. */
private fun unitAppliedSha(last: ServerVersion.LastUpdate?): String? {
    val sha = if (last?.status == "ok") last.toSha else last?.fromSha
    return sha?.takeIf { Regex("^[0-9a-f]{7,64}$").matches(it) }
}

/** `restartRunsFullUpdate`: why a plain restart pressed now would run the
 *  update unit's full update, or null. */
fun restartRunsFullUpdate(v: ServerVersion): String? {
    if (!v.updateUnit) return null
    val checkout = (v.checkoutSha ?: "").removeSuffix("-dirty")
    val pulled = if (v.codePending) "Pulled code is waiting to load (running ${v.runningSha ?: v.sha}, checked out $checkout)" else null
    val applied = unitAppliedSha(v.lastUpdate)
    if (applied != null && Regex("^[0-9a-f]{4,64}$").matches(checkout)) {
        if (applied.startsWith(checkout)) return null
        return pulled ?: "The checkout ($checkout) isn’t the commit the update unit last applied (${applied.take(7)})"
    }
    return pulled
}

object ServerUpdateWords {
    const val TITLE = "Domovoi server"
    const val SUB = "The Domovoi release this server runs, and installing its updates, as admin on this phone."
    const val BUILD_LINE_OR_PHONE =
        "The server's build / version identifiers live on the dashboard under About & updates, or sign in as admin on this phone."

    const val CHECK = "Check for updates"
    const val CHECKING = "Checking…"
    const val UPDATE = "Update Domovoi"
    const val PULLING = "Pulling…"
    const val RESTART_APPLY = "Restart to apply changes"
    const val RESTART = "Restart Domovoi"
    const val RESTARTING = "Restarting…"
    const val UPDATING = "Updating…"
    const val ALREADY = "already updating — this card follows it"

    private const val UPDATE_TEXT =
        "This backs up the database, updates dependencies and migrations if " +
            "they changed, and restarts domovoi-core and domovoi-web. If they " +
            "don’t come back healthy it rolls everything back. Voice is " +
            "unavailable meanwhile, usually for under a minute, longer when " +
            "dependencies change."

    /** The confirms, word for word (askConfirm's text: the question, a
     *  blank line, what happens). */
    const val PULL_CONFIRM =
        "Update Domovoi?\n\n" +
            "This puts the newest version on the Domovoi server but does not start it yet — " +
            "this panel will then offer the restart that does. Rooms can be " +
            "upgraded individually afterwards."
    const val ONE_PRESS_CONFIRM =
        "Update Domovoi?\n\n" +
            "Domovoi pulls the latest code, then backs up, applies it and restarts (the rooms, phones and " +
            "this dashboard lose Domovoi for a minute or two; it rolls back if it doesn’t come back healthy)."
    private const val PLAIN_CONFIRM =
        "Restart Domovoi now?\n\n" +
            "domovoi-core and domovoi-web restart, so changes to domovoi/.env " +
            "take effect. Voice and this dashboard are back in a few seconds, and " +
            "rooms reconnect on their own. Music playing in a room may stop; " +
            "ask for it again if it does."

    fun restartConfirm(v: ServerVersion, plain: Boolean): String {
        val full = if (plain) restartRunsFullUpdate(v) else null
        return when {
            full != null -> "Restart Domovoi now, as a full update?\n\n" +
                "$full, so this restart runs the update unit’s full update, not a quick restart. $UPDATE_TEXT"
            plain -> PLAIN_CONFIRM
            v.updateUnit -> "Apply the pulled code?\n\n$UPDATE_TEXT"
            else -> "Restart the Domovoi services to load the pulled code?\n\n" +
                "This bounces domovoi-core and domovoi-web. Voice is unavailable for " +
                "a few seconds and connected rooms reconnect on their own."
        }
    }

    /** A confirm as a dialog: its question as the title, the rest as the body. */
    fun split(text: String): Pair<String, String> {
        val at = text.indexOf("\n\n")
        return if (at < 0) text to "" else text.substring(0, at) to text.substring(at + 2)
    }

    fun codePending(restartCapable: Boolean): String =
        "New code is on disk but this process is still running the old modules" +
            if (restartCapable) " — restart below to load it." else " — restart the Domovoi server for it to take effect."

    fun pluginsPending(list: List<String>, restartCapable: Boolean): String =
        "Plugin upgrade${if (list.size == 1) "" else "s"} waiting for a restart: ${list.joinToString(", ")}" +
            if (restartCapable) " — restart below to finish." else " — restart the Domovoi server to finish."

    fun held(badSha: String): String =
        "The latest commit (${badSha.take(7)}) failed to apply on this server " +
            "and was rolled back, so it isn’t offered again. The next commit upstream will be."

    fun byHand(v: ServerVersion): String =
        (v.restartHint ?: "This host can’t restart itself").replace(Regex("[.\\s]*$"), ".") +
            " Restart the Domovoi services the way they were started."

    fun hostOnDashboard(kind: String): String =
        "This server has a ${if (kind == "mac") "Mac" else "Windows"} part too, so Update Domovoi is on the dashboard, which updates both."

    /** `versionCheckWords`. */
    fun checkWords(r: UpdateCheck?): String? {
        if (r == null || !r.upstream) return null
        val behind = r.behind ?: 0
        if (r.channel == "stable") {
            if (behind > 0) return "Domovoi ${r.upstreamVersion ?: "a newer release"} is available"
            if ((r.ahead ?: 0) > 0) return "newer than the latest release"
            return "the latest release"
        }
        return if (behind > 0) "$behind commit${if (behind == 1) "" else "s"} behind" else "up to date"
    }

    /** The check's toast. */
    fun checked(r: UpdateCheck): String = when {
        r.error != null -> "update check failed: ${r.error}"
        !r.upstream -> if (r.channel == "stable" && r.latestRelease == null) "no release has been published on the stable channel yet"
            else "no upstream configured — can’t check"
        else -> checkWords(r) ?: "up to date"
    }

    val RESULT_LABELS = mapOf(
        "ok" to "applied", "running" to "in progress", "rolled_back" to "rolled back", "rollback_failed" to "rollback failed",
        "failed" to "failed", "aborted" to "not applied", "refused" to "not applied",
    )
}

/** How a restart this phone asked for is going, from one read of the
 *  version (restartDomovoiServer's waitForServer). */
sealed interface RestartProgress {
    data object Waiting : RestartProgress
    data class Done(val ok: Boolean, val said: String) : RestartProgress
}

/** What was true at the press. */
data class RestartPress(val mode: String, val plain: Boolean, val previousRun: String?, val previousStart: String?) {
    val what: String get() = if (plain) "restart" else "update"
    val deadlineMs: Long get() = if (mode == "update") 15 * 60_000L else 90_000L

    fun after(v: ServerVersion, wentAway: Boolean): RestartProgress {
        val run = v.lastUpdate
        val newRun = run != null && run.startedAt != previousRun && run.status != "running"
        if (mode == "update" && newRun && run!!.status != "ok") {
            return RestartProgress.Done(false, run.error
                ?: "$what ${ServerUpdateWords.RESULT_LABELS[run.status] ?: run.status} — see journalctl -u domovoi-update")
        }
        val startedAgain = if (previousStart != null && v.startedAt != null) v.startedAt != previousStart else wentAway
        if (!v.restartRequired && !v.restartInProgress && (if (mode == "update") newRun else startedAgain)) {
            return RestartProgress.Done(true, "restarted — now running ${v.runningSha ?: v.sha ?: "new code"}")
        }
        return RestartProgress.Waiting
    }

    fun tooLong(): String = if (mode == "update") "the $what is taking longer than expected — check journalctl -u domovoi-update"
        else "restart is taking longer than expected — check the service by hand"
}

/** The routes. Mutations take [headers] and [onlyTo] from AdminSession. */
class ServerUpdateApi(private val api: ApiClient) {
    suspend fun version(): ServerVersion? = parseServerVersion(api.get(VERSION))
    suspend fun check(): UpdateCheck = parseUpdateCheck(api.post("$VERSION/check", buildJsonObject { }))
    suspend fun pull(headers: Map<String, String>, onlyTo: String): JsonObject? =
        api.post("$VERSION/pull", buildJsonObject { }, headers, onlyTo = onlyTo) as? JsonObject
    suspend fun restart(headers: Map<String, String>, onlyTo: String): JsonObject? =
        api.post("$VERSION/restart", buildJsonObject { }, headers, onlyTo = onlyTo) as? JsonObject

    companion object {
        const val VERSION = "/api/config/version"
    }
}

/** Only a connection-level failure (no answer) can mean the bounce cut the
 *  restart's request off: not a refusal the server sent, and not one of the
 *  requests this app chose not to send. */
fun cutOffMidRequest(e: Throwable): Boolean =
    e is java.io.IOException && e !is com.domovoi.app.net.ApiException &&
        e !is com.domovoi.app.net.ServerChangedException && e !is com.domovoi.app.net.ServerIdentityException &&
        e !is com.domovoi.app.net.TokenThrottledException && e !is java.net.UnknownServiceException

/** Whether the server card is offered: admin held, not a shared screen. */
fun serverAdminHeld(state: AdminSession.State, shared: Boolean): Boolean =
    !shared && state !is AdminSession.State.SignedOut

private val UNDER_WAY = Regex("under way|already running|in progress", RegexOption.IGNORE_CASE)

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ServerUpdateCard() {
    val app = LocalApp.current
    val context = LocalContext.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val api = remember(app) { ServerUpdateApi(app.api) }
    var version by remember { mutableStateOf<ServerVersion?>(null) }
    var check by remember { mutableStateOf<UpdateCheck?>(null) }
    var checking by remember { mutableStateOf(false) }
    var pulling by remember { mutableStateOf(false) }
    /** A restart this card asked for and is following: plain or not. */
    var following by remember { mutableStateOf<RestartPress?>(null) }
    var confirm by remember { mutableStateOf<String?>(null) }
    var problem by remember { mutableStateOf<String?>(null) }
    var tick by remember { mutableIntStateOf(0) }
    DisposableEffect(context) {
        app.adminLocker.promptHost = context
        onDispose { if (app.adminLocker.promptHost === context) app.adminLocker.promptHost = null }
    }
    LaunchedEffect(tick) {
        version = runCatching { api.version() }.getOrNull() ?: version
    }

    fun failed(action: String, e: Exception): String =
        if (e is AdminSession.AdminUnavailable) e.message ?: "admin isn't available" else failureText(action, e)

    /** Wait for the server to come back (or say what went wrong). */
    suspend fun follow(press: RestartPress) {
        following = press
        val until = System.currentTimeMillis() + press.deadlineMs
        var wentAway = false
        try {
            while (System.currentTimeMillis() < until) {
                delay(2_000)
                val v = try { api.version() } catch (e: CancellationException) { throw e } catch (e: Exception) { wentAway = true; null }
                if (v == null) continue
                when (val p = press.after(v, wentAway)) {
                    is RestartProgress.Done -> { toast(p.said); version = v; check = null; return }
                    RestartProgress.Waiting -> Unit
                }
            }
            toast(press.tooLong())
        } finally {
            following = null
            tick++
        }
    }

    /** The restart itself (asked already). */
    suspend fun restart(plain: Boolean) {
        val before = runCatching { api.version() }.getOrNull() ?: version ?: return
        if (before.restartInProgress) {
            toast("${if (before.updateUnit) "an update" else "a restart"} is already under way — waiting for it to finish")
            follow(RestartPress(if (before.updateUnit) "update" else "restart", plain, before.lastUpdate?.startedAt, before.startedAt))
            return
        }
        val full = plain && restartRunsFullUpdate(before) != null
        val kind = if (before.updateUnit) "update" else "restart"
        val runsPlain = plain && !full
        try {
            val res = app.admin.act { h, srv -> api.restart(h, srv) }
            val ok = (res?.get("ok") as? JsonPrimitive)?.booleanOrNull == true
            val busy = (res?.get("in_progress") as? JsonPrimitive)?.booleanOrNull == true
            when {
                ok -> {
                    val mode = (res?.get("mode") as? JsonPrimitive)?.contentOrNull ?: kind
                    toast(if (mode == "update" && !runsPlain) "updating…" else "restarting…")
                    follow(RestartPress(mode, runsPlain, before.lastUpdate?.startedAt, before.startedAt))
                }
                busy -> follow(RestartPress(kind, runsPlain, before.lastUpdate?.startedAt, before.startedAt))
                else -> toast("restart failed: ${(res?.get("error") as? JsonPrimitive)?.contentOrNull ?: "unknown"}")
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if (cutOffMidRequest(e)) {
                // Cut off mid-request: the bounce, most likely. Follow it.
                toast(if (kind == "update" && !runsPlain) "updating…" else "restarting…")
                follow(RestartPress(kind, runsPlain, before.lastUpdate?.startedAt, before.startedAt))
                return
            }
            problem = failed("restart", e)
            toast(problem!!)
        }
    }

    suspend fun alreadyUpdating(): Boolean {
        val now = runCatching { api.version() }.getOrNull()
        if (now?.restartInProgress == true) {
            toast(ServerUpdateWords.ALREADY)
            version = now
            return true
        }
        return false
    }

    /** Update Domovoi: the pull, then (one press) the restart. */
    suspend fun update(onePress: Boolean) {
        pulling = true
        problem = null
        val res = try {
            if (alreadyUpdating()) return
            app.admin.act { h, srv -> api.pull(h, srv) }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            problem = failed(if (onePress) "update" else "pull", e)
            toast(problem!!)
            return
        } finally {
            pulling = false
        }
        val notice = (res?.get("notice") as? JsonPrimitive)?.contentOrNull?.let { "$it. " }.orEmpty()
        val pulled = (res?.get("pulled") as? JsonPrimitive)?.booleanOrNull == true
        val error = (res?.get("error") as? JsonPrimitive)?.contentOrNull
        when {
            !pulled && error != null && UNDER_WAY.containsMatchIn(error) -> { toast(ServerUpdateWords.ALREADY); tick++ }
            !pulled -> toast("$notice${if (onePress) "update" else "pull"} failed: ${error ?: "unknown"}")
            onePress -> { check = null; restart(plain = false) }
            else -> {
                val sha = (res?.get("new_sha") as? JsonPrimitive)?.contentOrNull
                toast("${notice}pulled${sha?.let { " — now $it" }.orEmpty()}. Restart to apply.")
                check = null
                tick++
            }
        }
    }

    PanelCard(ServerUpdateWords.TITLE, ServerUpdateWords.SUB) {
        val v = version
        if (v == null) {
            Text("reading the server's version…", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            return@PanelCard
        }
        val mode = serverMode(v, check, following != null)
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                v.version ?: v.sha ?: "—",
                style = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
                color = Domovoi.colors.fg,
            )
            v.lastUpdate?.status?.let { s ->
                Text("last update: ${ServerUpdateWords.RESULT_LABELS[s] ?: s}", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            }
            if (mode != ServerMode.Busy) {
                if (v.codePending && v.restartRequired) {
                    Text(ServerUpdateWords.codePending(v.restartCapable), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.warn)
                }
                if (v.pluginsPending.isNotEmpty()) {
                    Text(ServerUpdateWords.pluginsPending(v.pluginsPending, v.restartCapable), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.warn)
                }
            }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                when (mode) {
                    ServerMode.Busy -> Button(enabled = false, onClick = {}) {
                        val updating = following?.let { !it.plain && it.mode == "update" } ?: v.updateUnit
                        Text(if (updating) ServerUpdateWords.UPDATING else ServerUpdateWords.RESTARTING)
                    }
                    ServerMode.Restart -> if (v.restartCapable) {
                        Button(onClick = { confirm = "apply" }) { Text(ServerUpdateWords.RESTART_APPLY) }
                    }
                    ServerMode.Pull -> if (v.hostSide == null) {
                        Button(enabled = !pulling, onClick = { confirm = if (onePress(v)) "onePress" else "pull" }) {
                            Text(if (pulling) ServerUpdateWords.PULLING else ServerUpdateWords.UPDATE)
                        }
                    }
                    ServerMode.Check, ServerMode.Held -> OutlinedButton(enabled = !checking, onClick = {
                        checking = true
                        scope.launch {
                            try {
                                val r = api.check()
                                check = r
                                toast(ServerUpdateWords.checked(r))
                            } catch (e: CancellationException) {
                                throw e
                            } catch (e: Exception) {
                                toast(failureText("update check", e))
                            } finally {
                                checking = false
                            }
                        }
                    }) { Text(if (checking) ServerUpdateWords.CHECKING else ServerUpdateWords.CHECK) }
                }
                if (v.restartCapable && mode != ServerMode.Restart && mode != ServerMode.Busy) {
                    OutlinedButton(enabled = !pulling, onClick = { confirm = "plain" }) { Text(ServerUpdateWords.RESTART) }
                }
            }
            if (mode != ServerMode.Restart && mode != ServerMode.Busy) {
                ServerUpdateWords.checkWords(check)?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall,
                        color = if ((check?.behind ?: 0) > 0) Domovoi.colors.warn else Domovoi.colors.ok)
                }
            }
            if (mode == ServerMode.Held && v.badSha != null) {
                Text(ServerUpdateWords.held(v.badSha), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.warn)
            }
            if (mode == ServerMode.Pull && v.hostSide != null) {
                Text(ServerUpdateWords.hostOnDashboard(v.hostSide), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            }
            if (!v.restartCapable && mode != ServerMode.Busy && (mode == ServerMode.Restart || v.restartRequired)) {
                Text(ServerUpdateWords.byHand(v), style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgFaint)
            }
            problem?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.err) }
        }
        val asking = confirm
        if (asking != null) {
            val text = when (asking) {
                "pull" -> ServerUpdateWords.PULL_CONFIRM
                "onePress" -> ServerUpdateWords.ONE_PRESS_CONFIRM
                "plain" -> ServerUpdateWords.restartConfirm(v, plain = true)
                else -> ServerUpdateWords.restartConfirm(v, plain = false)
            }
            val (title, body) = ServerUpdateWords.split(text)
            ConfirmDialog(
                title = title,
                body = body,
                confirmLabel = when (asking) { "pull", "onePress" -> "update"; else -> "restart" },
                onConfirm = {
                    scope.launch {
                        when (asking) {
                            "pull" -> update(onePress = false)
                            "onePress" -> update(onePress = true)
                            "plain" -> restart(plain = true)
                            else -> restart(plain = false)
                        }
                    }
                },
                onDismiss = { confirm = null },
            )
        }
    }
}

/** The About card's line about the server's build: on a shared screen as
 *  it always was; signed out, it names this phone's admin sign-in too. */
fun serverBuildLine(state: AdminSession.State, shared: Boolean): String? = when {
    serverAdminHeld(state, shared) -> null
    shared -> SERVER_BUILD_LINE
    else -> ServerUpdateWords.BUILD_LINE_OR_PHONE
}

internal const val SERVER_BUILD_LINE = "The server's build / version identifiers live on the dashboard under About & updates."

/** Read the admin state and the shared-screen flag where the About panel is. */
@Composable
internal fun rememberServerAdmin(): Pair<AdminSession.State, Boolean> {
    val state by LocalApp.current.admin.state.collectAsState()
    return state to LocalSharedScreen.current
}
