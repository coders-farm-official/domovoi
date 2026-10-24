package com.domovoi.app.alerts

import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.longOrNull

// ---------------------------------------------------------------------------
// The container guard, the phone's half (docs/CONTAINER_GUARD.md). The core
// watches the Docker containers Domovoi expects and records incidents of two
// classes:
//
//   failure   a container missing, stopped, restarting or unhealthy, or
//             Docker itself down. Resolves by itself; an admin may
//             acknowledge it to hide the banner.
//   tamper    a different image, an unknown container under Domovoi's
//             names, risky settings. Pauses plugin installs and updates and
//             satellite upgrades until an admin clears it after review.
//
// What the web dashboard serves, by tier:
//
//   GET /api/containers/incidents   household (a paired phone): the open and
//                                   recently resolved incidents in plain
//                                   words, the banner and the freeze. Also
//                                   what `containers.changed` carries on the
//                                   state socket.
//   GET /api/containers/summary     open (no credential): only whether
//                                   something needs an admin, and its colour.
//
// Acknowledge and clear are the dashboard admin's; the app never offers
// them. Everything here is pure, for the JVM tests: the payload parsers,
// the shell's banner model and which incidents a phone notifies about.
// ---------------------------------------------------------------------------

/** The open tier's line, and the only words a shared screen shows. */
const val ADMIN_ATTENTION_TEXT = "Something needs an admin's attention."

const val CLASS_TAMPER = "tamper"
const val CLASS_FAILURE = "failure"

/** One incident as the household tier sees it. Every field but [id] may be
 *  missing from a payload; the parser fills what it can derive. */
data class ContainerIncident(
    val id: Long,
    /** "failure" or "tamper" (anything else reads as a failure). */
    val cls: String = CLASS_FAILURE,
    /** missing, stopped, restarting, unhealthy, oneshot_failed, daemon,
     *  image, unknown, config. */
    val kind: String? = null,
    /** "error" (red) or "warn" (amber). */
    val severity: String? = null,
    val label: String? = null,
    val summary: String? = null,
    val owner: String? = null,
    val container: String? = null,
    val openedAt: String? = null,
    val lastSeenAt: String? = null,
    val resolvedAt: String? = null,
    /** null, recovered, no_longer_expected, cleared. */
    val resolution: String? = null,
    val acknowledged: Boolean = false,
    val acknowledgedAt: String? = null,
    val clearedAt: String? = null,
    val open: Boolean = true,
    val freezes: Boolean = false,
) {
    val tamper: Boolean get() = cls == CLASS_TAMPER

    /** What the banner shows: an open tamper incident (only clearing hides
     *  it), or an open failure nobody has acknowledged. */
    val bannerWorthy: Boolean get() = open && (tamper || !acknowledged)

    /** Red: tampering, or a required container's failure. */
    val red: Boolean get() = tamper || severity == "error"
}

/** The banner the server computed for every page. */
data class IncidentBanner(
    val show: Boolean = false,
    /** "tamper", "error", "warn"; null while [show] is false. */
    val tone: String? = null,
    val text: String = "",
    val count: Int = 0,
    val incidentIds: List<Long> = emptyList(),
    val frozen: Boolean = false,
)

/** GET /api/containers/incidents, and the `containers.changed` event. */
data class IncidentsPayload(
    /** Open first, newest first; then those resolved in the last 24 h. */
    val incidents: List<ContainerIncident> = emptyList(),
    val banner: IncidentBanner = IncidentBanner(),
    val frozen: Boolean = false,
    val attention: Boolean = false,
)

/** GET /api/containers/summary: the open tier's whole answer. */
data class IncidentSummary(
    val attention: Boolean = false,
    /** "err" or "warn"; null with nothing to show. */
    val tone: String? = null,
    val text: String = "",
)

// ---- parsing ----------------------------------------------------------------

private fun JsonObject.prim(key: String): JsonPrimitive? = (this[key] as? JsonPrimitive)?.takeIf { it !is JsonNull }

private fun JsonObject.str(key: String): String? = prim(key)?.content?.takeIf { it.isNotBlank() }

private fun JsonObject.bool(key: String): Boolean? = prim(key)?.let { p ->
    p.booleanOrNull ?: when (p.content.lowercase()) {
        "1" -> true
        "0" -> false
        else -> null
    }
}

private fun JsonObject.long(key: String): Long? = prim(key)?.let { it.longOrNull ?: it.content.toLongOrNull() }

/** One incident, or null when it has no usable id. */
internal fun parseIncident(el: JsonElement?): ContainerIncident? {
    val o = el as? JsonObject ?: return null
    val id = o.long("id") ?: return null
    val resolvedAt = o.str("resolved_at")
    val open = o.bool("open") ?: (resolvedAt == null)
    val cls = if (o.str("class") == CLASS_TAMPER) CLASS_TAMPER else CLASS_FAILURE
    val acknowledgedAt = o.str("acknowledged_at")
    return ContainerIncident(
        id = id,
        cls = cls,
        kind = o.str("kind"),
        severity = o.str("severity"),
        label = o.str("label"),
        summary = o.str("summary"),
        owner = o.str("owner"),
        container = o.str("container"),
        openedAt = o.str("opened_at"),
        lastSeenAt = o.str("last_seen_at"),
        resolvedAt = resolvedAt,
        resolution = o.str("resolution"),
        acknowledged = o.bool("acknowledged") ?: (acknowledgedAt != null),
        acknowledgedAt = acknowledgedAt,
        clearedAt = o.str("cleared_at"),
        open = open,
        freezes = o.bool("freezes") ?: (cls == CLASS_TAMPER && open),
    )
}

/**
 * The banner from the incidents alone, the server's own rule
 * (domovoi/container_guard/views.py): only for a payload that came without
 * one, so a server that dropped the field still shows what is open.
 */
internal fun deriveBanner(incidents: List<ContainerIncident>): IncidentBanner {
    val frozen = incidents.any { it.open && it.tamper }
    val shown = incidents.filter { it.bannerWorthy }
    if (shown.isEmpty()) return IncidentBanner(frozen = frozen)
    val tone = when {
        shown.any { it.tamper } -> "tamper"
        shown.any { it.severity == "error" } -> "error"
        else -> "warn"
    }
    var text = if (shown.size == 1) {
        shown[0].summary ?: "A Domovoi container needs attention."
    } else {
        "${shown.size} problems with Domovoi's containers need attention."
    }
    if (tone == "tamper") {
        text += " Plugin installs, updates and room upgrades are paused until an admin reviews it."
    }
    return IncidentBanner(true, tone, text, shown.size, shown.map { it.id }, frozen)
}

private fun parseBanner(el: JsonElement?): IncidentBanner? {
    val o = el as? JsonObject ?: return null
    val ids = (o["incident_ids"] as? JsonArray).orEmpty()
        .mapNotNull { (it as? JsonPrimitive)?.let { p -> p.longOrNull ?: p.content.toLongOrNull() } }
    return IncidentBanner(
        show = o.bool("show") ?: false,
        tone = o.str("tone"),
        text = o.str("text") ?: "",
        count = o.long("count")?.toInt() ?: ids.size,
        incidentIds = ids,
        frozen = o.bool("frozen") ?: false,
    )
}

/**
 * The household payload, or null when [el] is not one: not an object, or
 * no `incidents` array (the web's test, too). Incidents without an id are
 * skipped; unknown fields are ignored.
 */
fun parseIncidents(el: JsonElement?): IncidentsPayload? {
    val o = el as? JsonObject ?: return null
    val list = o["incidents"] as? JsonArray ?: return null
    val incidents = list.mapNotNull { parseIncident(it) }
    val banner = parseBanner(o["banner"]) ?: deriveBanner(incidents)
    return IncidentsPayload(
        incidents = incidents,
        banner = banner,
        frozen = o.bool("frozen") ?: banner.frozen,
        attention = o.bool("attention") ?: banner.show,
    )
}

/** The open tier's answer, or null when [el] is not an object. */
fun parseSummary(el: JsonElement?): IncidentSummary? {
    val o = el as? JsonObject ?: return null
    val attention = o.bool("attention") ?: false
    return IncidentSummary(
        attention = attention,
        tone = o.str("tone"),
        text = o.str("text") ?: if (attention) ADMIN_ATTENTION_TEXT else "",
    )
}

// ---- what the phone holds ---------------------------------------------------

/** How much this phone may see: the household list, the open summary, or
 *  nothing (not read yet, no server, or a server older than the guard). */
enum class IncidentTier { NONE, OPEN, HOUSEHOLD }

/** What the Containers screen shows: the guest line, the household list, or nothing yet. */
enum class ContainersView { GUEST, HOUSEHOLD, NONE }

/** A [shared] screen (the kitchen tablet) gets the guest line whatever it
 *  holds — like the banner, never what failed (review L1). */
fun containersView(tier: IncidentTier, shared: Boolean): ContainersView = when {
    shared && tier != IncidentTier.NONE -> ContainersView.GUEST
    tier == IncidentTier.HOUSEHOLD -> ContainersView.HOUSEHOLD
    tier == IncidentTier.OPEN -> ContainersView.GUEST
    else -> ContainersView.NONE
}

/** What the shell's banner and the Containers screen read
 *  (IncidentAlerts.state). */
data class IncidentState(
    /** The server this describes (alerts/FireModels.kt serverKey). */
    val serverKey: String? = null,
    val tier: IncidentTier = IncidentTier.NONE,
    /** The server has answered at least once (either read, or a 404). */
    val loaded: Boolean = false,
    /** The household payload, on [IncidentTier.HOUSEHOLD]. */
    val payload: IncidentsPayload? = null,
    /** The open summary, on [IncidentTier.OPEN]. */
    val summary: IncidentSummary? = null,
) {
    val incidents: List<ContainerIncident> get() = payload?.incidents.orEmpty()
    val open: List<ContainerIncident> get() = incidents.filter { it.open }
    val resolved: List<ContainerIncident> get() = incidents.filterNot { it.open }
    val frozen: Boolean get() = payload?.frozen == true
}

/** The banner's colour: red for tampering and errors, amber for a warning. */
enum class BannerTone(val red: Boolean) { TAMPER(true), ERROR(true), WARN(false) }

/** The strip at the top of every shell. */
data class ShellBanner(
    val show: Boolean = false,
    val tone: BannerTone? = null,
    val text: String = "",
    val frozen: Boolean = false,
)

private fun toneOf(raw: String?): BannerTone = when (raw) {
    "tamper" -> BannerTone.TAMPER
    "warn" -> BannerTone.WARN
    else -> BannerTone.ERROR
}

/**
 * The shell's banner. The household tier uses the server's banner as given;
 * the open tier its summary ("err" red, "warn" amber). On a [shared] screen
 * (the kitchen tablet, net/SharedScreen.kt) the words are only
 * [ADMIN_ATTENTION_TEXT], like Home's problem rows there: a passer-by learns
 * that an admin is needed, not which container or why.
 */
fun shellBanner(state: IncidentState, shared: Boolean): ShellBanner {
    val hidden = ShellBanner()
    return when (state.tier) {
        IncidentTier.HOUSEHOLD -> {
            val b = state.payload?.banner ?: return hidden
            if (!b.show) return hidden
            ShellBanner(
                show = true,
                tone = toneOf(b.tone),
                text = if (shared) ADMIN_ATTENTION_TEXT else b.text.ifBlank { ADMIN_ATTENTION_TEXT },
                frozen = b.frozen || state.frozen,
            )
        }
        IncidentTier.OPEN -> {
            val s = state.summary ?: return hidden
            if (!s.attention) return hidden
            ShellBanner(
                show = true,
                tone = if (s.tone == "warn") BannerTone.WARN else BannerTone.ERROR,
                text = if (shared) ADMIN_ATTENTION_TEXT else s.text.ifBlank { ADMIN_ATTENTION_TEXT },
            )
        }
        IncidentTier.NONE -> hidden
    }
}

// ---- the details screen's words ---------------------------------------------

/** The `kind` in a few words (the web Containers page's CtrKindWords). */
fun kindWords(kind: String?): String = when (kind) {
    "missing" -> "missing"
    "stopped" -> "stopped"
    "restarting" -> "restarting"
    "unhealthy" -> "unhealthy"
    "oneshot_failed" -> "failed"
    "daemon" -> "docker down"
    "image" -> "different image"
    "unknown" -> "unknown container"
    "config" -> "risky settings"
    "outdated" -> "older image"
    null -> "problem"
    else -> kind.replace('_', ' ')
}

/**
 * The meta line under an incident: "#12 · tampering · different image ·
 * domovoi-searxng · opened 2m ago", then "acknowledged" for an open one an
 * admin acknowledged, or "cleared"/"resolved" and when for a closed one.
 * [rel] formats a timestamp ("2m ago"; ui/components/Format.kt relTime).
 */
fun incidentMeta(inc: ContainerIncident, rel: (String?) -> String): String {
    val parts = mutableListOf("#${inc.id}", if (inc.tamper) "tampering" else "failure", kindWords(inc.kind))
    inc.container?.let { parts += it }
    parts += "opened ${rel(inc.openedAt)}"
    if (inc.open) {
        if (inc.acknowledged) parts += "acknowledged"
    } else {
        val how = if (inc.resolution == "cleared") "cleared" else "resolved"
        parts += "$how ${rel(inc.resolvedAt ?: inc.clearedAt)}"
    }
    return parts.joinToString(" · ")
}

/** What a tamper incident holds back, while one is open (the web's list). */
val FREEZE_LIST = listOf(
    "plugin installs, upgrades and enabling a plugin",
    "room software upgrades and the plugin payloads they carry",
    "preparing a room's SD card",
    "pulling and applying Domovoi updates",
)

// ---- the notification's words -----------------------------------------------

/** The lock screen's version, and a shared screen's only one. */
const val INCIDENT_PUBLIC_TITLE = "Domovoi needs an admin's attention"

/** What one incident's notification says (IncidentNotifier posts it). */
data class IncidentContent(
    val incidentId: Long,
    val title: String,
    /** The incident's summary; null on a shared screen. */
    val text: String?,
    val publicTitle: String = INCIDENT_PUBLIC_TITLE,
    /** When it opened (epoch ms), for the notification's time. */
    val whenMs: Long? = null,
)

fun incidentTitle(inc: ContainerIncident): String = when {
    inc.tamper -> "Signs of tampering"
    inc.kind == "outdated" -> "A container needs recreating"
    else -> "A container failed"
}

/** On a [shared] screen (the kitchen tablet) the private version is the
 *  public one: no container, no words, locked or not. */
fun incidentContent(inc: ContainerIncident, shared: Boolean): IncidentContent {
    val whenMs = isoMs(inc.openedAt)
    if (shared) return IncidentContent(inc.id, INCIDENT_PUBLIC_TITLE, null, whenMs = whenMs)
    val text = inc.summary
        ?: listOfNotNull(inc.label, kindWords(inc.kind)).joinToString(" · ")
    return IncidentContent(inc.id, incidentTitle(inc), text, whenMs = whenMs)
}

/** The notification tag (the id is the incident id): one slot per incident
 *  per server, apart from the timer alerts' `timer_fire:` slots. */
fun incidentTag(serverKey: String): String = "container_incident:$serverKey"

// ---- which incidents a phone notifies about ---------------------------------

/** The notified book keeps at most this many incidents per server. */
internal const val NOTIFIED_MAX = 200

/**
 * How the book names an incident: its id and when it opened, so a server
 * whose incident ids started again (a rebuilt database on the same address)
 * does not have its new incident #3 hushed by an old one.
 */
internal fun incidentMark(inc: ContainerIncident): String =
    "${inc.id}|${isoMs(inc.openedAt) ?: inc.openedAt.orEmpty()}"

/** What to post now, and the server's book afterwards. */
data class NotifyPlan(val post: List<ContainerIncident>, val book: List<String>)

/**
 * Which of [incidents] to post a notification for, given the marks already
 * notified for THIS server ([notified]; empty on first contact).
 *
 *  * One notification per incident, ever: an incident in the book is never
 *    posted again, however long it stays open or however often it is read.
 *  * Only what the banner shows posts: an open tamper incident, or an open
 *    failure nobody has acknowledged. A resolved incident never posts.
 *  * An acknowledged failure is not posted and not remembered, so if its
 *    problem changes (the server drops the acknowledgement) it posts then,
 *    once.
 *  * First contact is the same rule: what the banner shows now posts, then
 *    is remembered.
 *
 * The book keeps the newest [NOTIFIED_MAX] marks.
 */
fun planNotifications(incidents: List<ContainerIncident>, notified: List<String>): NotifyPlan {
    val known = notified.toHashSet()
    val post = incidents
        .filter { it.bannerWorthy && incidentMark(it) !in known }
        .distinctBy { incidentMark(it) }
        .sortedBy { it.id }
    if (post.isEmpty()) return NotifyPlan(emptyList(), notified.takeLast(NOTIFIED_MAX))
    return NotifyPlan(post, (notified + post.map { incidentMark(it) }).takeLast(NOTIFIED_MAX))
}

/** The incident id a book mark ([incidentMark]) names, or null. */
internal fun markId(mark: String): Long? = mark.substringBefore('|').toLongOrNull()

/** What to withdraw now, and the server's withdrawn marks afterwards. */
data class WithdrawPlan(val withdraw: List<Long>, val withdrawn: List<String>)

/**
 * Which posted notifications to take back, given the household list
 * ([incidents]: every open incident, and those resolved in the last day),
 * the marks posted for THIS server ([notified]) and those already taken
 * back ([withdrawn]).
 *
 *  * A posted incident that is no longer open — it resolved, or an admin
 *    cleared it — is withdrawn from the shade, once.
 *  * One the list no longer carries at all (resolved over a day ago, or a
 *    rebuilt database) is withdrawn the same way.
 *  * An open incident keeps its notification, acknowledged or not.
 *  * Its mark stays in the book, so a read that started before the
 *    incident resolved and lands after the push that said so can't post it
 *    a second time.
 *
 * Only the household list can say this: the open summary names no
 * incident, so it never withdraws anything.
 */
fun planWithdrawals(
    incidents: List<ContainerIncident>,
    notified: List<String>,
    withdrawn: List<String>,
): WithdrawPlan {
    val openMarks = incidents.filter { it.open }.map { incidentMark(it) }.toHashSet()
    val already = withdrawn.toHashSet()
    val now = notified.filter { it !in openMarks && it !in already }.distinct()
    // Only marks still in the book are worth remembering as withdrawn.
    val inBook = notified.toHashSet()
    val kept = (withdrawn + now).filter { it in inBook }.distinct()
    return WithdrawPlan(now.mapNotNull { markId(it) }.distinct(), kept)
}

private val notifiedSerializer = MapSerializer(String.serializer(), ListSerializer(String.serializer()))

/** Never throws: a corrupt or absent blob is an empty book. */
internal fun decodeNotified(raw: String?): Map<String, List<String>> =
    runCatching { Json.decodeFromString(notifiedSerializer, raw ?: "{}") }.getOrDefault(emptyMap())

internal fun encodeNotified(book: Map<String, List<String>>): String = Json.encodeToString(notifiedSerializer, book)
