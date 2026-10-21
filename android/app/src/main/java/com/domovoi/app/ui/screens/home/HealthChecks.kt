package com.domovoi.app.ui.screens.home

import com.domovoi.app.alerts.BannerTone
import com.domovoi.app.alerts.IncidentState
import com.domovoi.app.alerts.IncidentTier
import com.domovoi.app.alerts.shellBanner
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.shell.Route
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive

// ---------------------------------------------------------------------------
// The house's checks — ONE set for Settings → Health and Home's "needs
// attention" (web/static/health.jsx healthChecks, 2026-10-21). Health shows
// every check, green or not, in its part; Home lists the problems among
// them and each row opens Health. The app has no admin session, so the
// admin-only checks (updates, disk, approvals, adoption, the internet
// answer) are the dashboard's Health only: their reads are never made here.
// ---------------------------------------------------------------------------

/** Health's parts on this app, in order (the web adds Updates and Internet). */
internal enum class HealthPart(val key: String, val label: String) {
    Server("server", "Server"),
    Rooms("rooms", "Rooms"),
    Plugins("plugins", "Plugins"),
    Containers("containers", "Containers"),
}

internal data class HealthCheck(
    val key: String,
    val part: HealthPart,
    val label: String,
    val tone: Tone,
    val text: String,
    /** Where Health's row leads to fix it (a problem only); null for none. */
    val fix: HomeTarget? = null,
    val fixLabel: String? = null,
    /** When it happened (ISO), shown relative — "offline · 2h ago". */
    val at: String? = null,
    /** The rooms or plugins a problem row is about. */
    val items: List<String> = emptyList(),
    /** Where Home always listed this problem (it ranks err → warn, then by it). */
    val order: Int = 0,
) {
    val problem: Boolean get() = tone == Tone.Err || tone == Tone.Warn
}

private fun JsonElement?.truthy(): Boolean = when (this) {
    null, JsonNull -> false
    is JsonPrimitive -> if (isString) content.isNotEmpty() else content != "false" && content != "0"
    else -> true
}

private fun JsonElement?.nonEmptyList(): Boolean = (this as? JsonArray)?.isNotEmpty() == true

private const val WAITING = "waiting for the Domovoi server"
private const val UNREAD = "not checked yet"

/** Settings → Health, the section a Home row opens. */
internal val HEALTH_TARGET = HomeTarget.Settings("health")

/**
 * Every check this app can make, in Health's order. A read that hasn't
 * answered (null) is grey, never green. When the core or the database is
 * down that is the only story: every check that reads through them waits.
 */
internal fun healthChecks(
    health: HomeHealth?,
    rooms: List<HomeRoom>?,
    plugins: HomePlugins?,
    acq: HomeAcquisitions?,
    guard: IncidentState? = null,
): List<HealthCheck> {
    val out = mutableListOf<HealthCheck>()
    val dbDown = health?.db_reachable == false
    val coreDown = health?.domovoi_reachable == false
    val waits = dbDown || coreDown
    fun HealthCheck.waiting() = copy(tone = Tone.Idle, text = WAITING)
    fun HealthCheck.unread() = copy(tone = Tone.Idle, text = UNREAD)

    // ── server ──
    run {
        val c = HealthCheck("core", HealthPart.Server, "Domovoi server", Tone.Ok, "answering", order = 3)
        out += when {
            health == null -> c.unread()
            coreDown -> c.copy(tone = Tone.Err, text = "the Domovoi server isn't answering · rooms show the last known state")
            else -> c
        }
    }
    run {
        val c = HealthCheck("db", HealthPart.Server, "database", Tone.Ok, "answering", order = 2)
        out += when {
            health == null -> c.unread()
            dbDown -> c.copy(tone = Tone.Err, text = "the database isn't answering · nothing new can be saved")
            else -> c
        }
    }
    run {
        val c = HealthCheck("stt", HealthPart.Server, "speech recognition", Tone.Ok, "ready",
            fix = HomeTarget.Dashboard("#settings/models"), fixLabel = "models", order = 4)
        out += when {
            health == null -> c.unread()
            waits -> c.waiting()
            health.stt == "unavailable" -> c.copy(tone = Tone.Err,
                text = "speech recognition is off · the rooms can't understand anyone")
            health.stt == "fallback" -> c.copy(tone = Tone.Warn,
                text = "speech recognition is on its slower fallback model")
            health.stt == null -> c.copy(tone = Tone.Idle, text = "not reported by this server")
            else -> c
        }
    }

    // ── rooms ──
    val roomFix = HomeTarget.Screen(Route.Satellites)
    run {
        val c = HealthCheck("rooms", HealthPart.Rooms, "rooms online", Tone.Ok, "", fix = roomFix,
            fixLabel = "rooms", order = 91)
        out += when {
            waits -> c.waiting()
            rooms == null -> c.unread()
            rooms.isEmpty() -> c.copy(tone = Tone.Idle, text = "no rooms yet")
            else -> {
                val on = rooms.count { it.status == "online" }
                c.copy(tone = if (on > 0) Tone.Ok else Tone.Idle, text = "$on of ${rooms.size} online")
            }
        }
    }
    fun roomCheck(base: HealthCheck, pick: (List<HomeRoom>) -> List<HomeRoom>, problem: (List<HomeRoom>) -> HealthCheck, okText: String) {
        out += when {
            waits -> base.waiting()
            rooms == null -> base.unread()
            else -> {
                val hits = pick(rooms)
                if (hits.isEmpty()) base.copy(text = okText) else problem(hits).copy(items = hits.map { it.room_id })
            }
        }
    }
    val offlineBase = HealthCheck("offline", HealthPart.Rooms, "offline", Tone.Ok, "none", fix = roomFix,
        fixLabel = "rooms", order = 5)
    roomCheck(offlineBase, { l -> l.filter { it.status == "offline" } }, { off ->
        if (off.size == 1) offlineBase.copy(tone = Tone.Warn, at = off[0].last_connected_at,
            text = "${off[0].room_id} is offline · it can't hear anyone right now")
        else offlineBase.copy(tone = Tone.Warn, text = "${off.size} rooms offline")
    }, "none")
    val waitingBase = HealthCheck("waiting", HealthPart.Rooms, "set up, not connected yet", Tone.Ok, "none",
        fix = roomFix, fixLabel = "rooms", order = 6)
    roomCheck(waitingBase, { l -> l.filter { it.status == "waiting" } }, { w ->
        waitingBase.copy(tone = Tone.Warn,
            text = if (w.size == 1) "${w[0].room_id} was set up but hasn't connected yet"
            else "${w.size} rooms were set up but haven't connected yet")
    }, "none")
    val kioskBase = HealthCheck("kiosk", HealthPart.Rooms, "screens", Tone.Ok, "every screen is showing",
        fix = roomFix, fixLabel = "rooms", order = 7)
    roomCheck(kioskBase, { l -> l.filter { kioskDead(it) } }, { dead ->
        kioskBase.copy(tone = Tone.Warn,
            text = if (dead.size == 1) "the ${dead[0].room_id} screen stopped showing anything"
            else "${dead.size} screens stopped showing anything")
    }, "every screen is showing")

    // ── plugins ──
    run {
        // As the server sees them (the web page adds errors only its own
        // browser saw; this app loads no plugin scripts).
        val c = HealthCheck("plugins", HealthPart.Plugins, "plugins", Tone.Ok, "",
            fix = HomeTarget.Dashboard("#plugins"), fixLabel = "plugins", order = 8)
        out += when {
            waits -> c.waiting()
            plugins == null -> c.unread()
            else -> {
                val all = plugins.plugins.filter { it.status != "uninstalled" }
                val bad = LinkedHashMap<String, Pair<String, Tone>>()
                all.forEach { p ->
                    val enabled = p.enabled != false
                    val name = p.name?.takeIf { it.isNotEmpty() } ?: p.slug
                    val broken = p.status == "load_error" ||
                        (enabled && (p.web_load_error.truthy() || p.page_errors.nonEmptyList()))
                    if (broken) bad[p.slug] = name to Tone.Err
                    else if (enabled && p.status == "degraded") bad[p.slug] = name to Tone.Warn
                }
                val names = bad.values.map { it.first }
                when {
                    bad.size == 1 -> {
                        val (name, tone) = bad.values.first()
                        c.copy(tone = tone, items = names,
                            text = if (tone == Tone.Err) "the $name plugin failed to load" else "the $name plugin is degraded")
                    }
                    bad.size > 1 -> c.copy(
                        tone = if (bad.values.any { it.second == Tone.Err }) Tone.Err else Tone.Warn,
                        items = names, text = "${bad.size} plugins have problems")
                    all.isEmpty() -> c.copy(text = "none installed")
                    else -> c.copy(text = "${all.size} ${if (all.size == 1) "plugin" else "plugins"} · all working")
                }
            }
        }
    }
    run {
        // A media request is waiting and no provider plugin can fill it.
        // Never the request's own text.
        val c = HealthCheck("acq", HealthPart.Plugins, "media requests", Tone.Ok, "none stuck",
            fix = HomeTarget.Screen(Route.Music), fixLabel = "music", order = 9)
        out += when {
            waits -> c.waiting()
            acq == null -> c.unread()
            !acq.core_reachable -> c.copy(tone = Tone.Idle, text = "not checked · the Domovoi server is away")
            else -> {
                val stuck = acq.acquisitions.count { a ->
                    a.status == "pending" &&
                        (if (a.kind == "url") acq.can_fulfill_url == false else acq.can_fulfill_query == false)
                }
                if (stuck == 0) c
                else c.copy(tone = Tone.Warn,
                    text = if (stuck == 1) "a media request is waiting · no provider plugin can fill it"
                    else "$stuck media requests are waiting · no provider plugin can fill them")
            }
        }
    }

    // ── containers ──
    out += guardCheck(guard)
    return out
}

/**
 * The container guard's check (web health.jsx healthGuardCheck): one
 * tampering incident or more reads "signs of tampering in a Domovoi
 * container · risky changes are paused"; several failures, "N Domovoi
 * containers need attention"; one, its own words. Red for tampering and a
 * required container's failure, amber for an optional one's.
 */
internal fun guardCheck(state: IncidentState?): HealthCheck {
    val c = HealthCheck("containers", HealthPart.Containers, "Domovoi containers", Tone.Ok,
        "every container Domovoi expects looks right", order = 0)
    if (state == null || !state.loaded) return c.copy(tone = Tone.Idle, text = UNREAD)
    val banner = shellBanner(state, shared = false)
    if (!banner.show) return c
    val tone = if (banner.tone == BannerTone.WARN) Tone.Warn else Tone.Err
    val text = when {
        state.tier != IncidentTier.HOUSEHOLD -> "something needs an admin's attention"
        banner.tone == BannerTone.TAMPER -> "signs of tampering in a Domovoi container · risky changes are paused"
        else -> {
            val ids = state.payload?.banner?.incidentIds.orEmpty()
            if (ids.size > 1) "${ids.size} Domovoi containers need attention"
            else {
                val only = state.incidents.firstOrNull { it.id == ids.firstOrNull() }
                val words = (only?.summary ?: "a Domovoi container needs attention").trimEnd('.')
                words.replaceFirstChar { it.lowercase() }
            }
        }
    }
    return c.copy(tone = tone, text = text)
}

/** Home's row for a problem: its words, and Settings → Health. */
private fun HealthCheck.row() = AttentionRow(key, tone, text, HEALTH_TARGET, at = at)

/**
 * Every problem this app can name, ranked err → warn, then in the order
 * Home always listed them: the problems among [healthChecks]. Home never
 * fixes anything itself; each row opens Settings → Health.
 */
internal fun attentionRows(
    health: HomeHealth?,
    rooms: List<HomeRoom>?,
    plugins: HomePlugins?,
    acq: HomeAcquisitions?,
    guard: IncidentState? = null,
): List<AttentionRow> =
    healthChecks(health, rooms, plugins, acq, guard)
        .filter { it.problem }
        .sortedWith(compareBy({ if (it.tone == Tone.Err) 0 else 1 }, { it.order }))
        .map { it.row() }

/** The container guard's problem row, or null (web health.jsx healthGuardCheck). */
internal fun guardRow(state: IncidentState): AttentionRow? = guardCheck(state).takeIf { it.problem }?.row()
