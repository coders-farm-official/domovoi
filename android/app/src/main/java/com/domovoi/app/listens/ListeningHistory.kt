package com.domovoi.app.listens

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DeviceOwner
import com.domovoi.app.net.claimHeaders
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull
import java.time.Instant
import java.time.OffsetDateTime

/**
 * People → a person: "this phone" (whose it is, "this is me") and their
 * listening history (how much, clearing it). The words are the dashboard's
 * (web/static/people.jsx `PersonThisBrowser`, `PersonListening`) with
 * "phone" for "browser"; the rules are the server's
 * (web/backend/api/devices.py, music_listens.py), only mirrored here so the
 * app never offers a press the server would refuse.
 */

/** `GET /api/music/listens/summary`: the setting and counts, never a song. */
@Serializable
data class ListensSummary(
    val keep: String = "",
    @SerialName("keep_label") val keepLabel: String = "",
    val total: Int = 0,
    val household: Int = 0,
    val people: List<PersonListens> = emptyList(),
) {
    fun countFor(personId: Long): Int = people.firstOrNull { it.personId == personId }?.listens ?: 0
    val off: Boolean get() = keep == "off"
}

@Serializable
data class PersonListens(
    @SerialName("person_id") val personId: Long = 0,
    val name: String = "",
    val listens: Int = 0,
)

fun songsWord(n: Int): String = "$n song${if (n == 1) "" else "s"}"

/** The listening-history line on a person's page. */
fun listensLine(summary: ListensSummary, personId: Long, name: String): String {
    if (summary.off) return "Domovoi keeps no listening history (Settings → Privacy → Listening history)."
    val n = summary.countFor(personId)
    if (n == 0) return "nothing kept for $name"
    val kept = if (summary.keepLabel == "1 year") "a year" else summary.keepLabel
    return "${songsWord(n)} played for $name · kept $kept"
}

// ─── "This phone" ───────────────────────────────────────────────────────────

/** What the "this phone" block offers. */
enum class PhoneAction { ThisIsMe, NotMe, ItsTheirsInstead }

data class ThisPhoneBlock(val line: String, val action: PhoneAction?, val small: String)

/**
 * The block on [name]'s page ([personId]) for a phone that is [owner]'s
 * (the server's answer; null: nobody's), a [shared] screen or not, that
 * [keyHeld] a claim key there or not. [deviceCount]: the phones and
 * browsers that are [name]'s (`device_count` on `GET /api/people`).
 */
fun thisPhoneBlock(
    personId: Long,
    name: String,
    owner: DeviceOwner?,
    shared: Boolean,
    keyHeld: Boolean,
    deviceCount: Int,
): ThisPhoneBlock {
    val mine = !shared && owner != null && owner.id == personId
    val elsewhere = maxOf(0, deviceCount - if (mine) 1 else 0)
    val (line, action) = when {
        shared -> "This is a shared screen, so it isn’t anyone’s: what plays here counts for the whole household." to null
        mine && !keyHeld ->
            "This phone is $name’s, but it can’t prove it yet. Say “this is me” again to act as $name here." to
                PhoneAction.ThisIsMe
        mine ->
            "This phone is $name’s. What plays here counts as $name’s listening, and $name can clear it from here." to
                PhoneAction.NotMe
        owner != null -> "This phone is ${owner.name}’s." to PhoneAction.ItsTheirsInstead
        else ->
            "Is this $name’s phone? Then what plays here counts as $name’s listening, and $name can clear it from here." to
                PhoneAction.ThisIsMe
    }
    val other = if (mine) "other " else ""
    var small = if (elsewhere == 0) {
        "no ${other}phone or browser is $name’s"
    } else {
        val one = elsewhere == 1
        "$elsewhere ${other}phone${if (one) "" else "s"} or browser${if (one) "" else "s"} ${if (one) "is" else "are"} $name’s"
    }
    if (!shared && !mine && elsewhere > 0) small += " · an admin has to say yes to add this one"
    return ThisPhoneBlock(line, action, small)
}

// ─── Clearing ─────────────────────────────────────────────────────────────

/** How long after "this is me" clearing stays an admin's (the server's FRESH_CLAIM). */
const val FRESH_CLAIM_MS = 10 * 60 * 1000L

/** What pressing "clear" may do on this phone. */
sealed class ClearCheck {
    /** This phone may clear it itself: confirm, then DELETE. */
    data object Allowed : ClearCheck()

    /** It may not: say [sentence] and [why], and offer [offers]. */
    data class NotHere(val sentence: String, val why: String, val offers: List<ClearOffer>) : ClearCheck()
}

enum class ClearOffer { ThisIsMe, OpenDashboard }

/** Whether a claim made at [claimedAt] (ISO) is still fresh at [nowMs]; an
 *  unknown or unreadable time counts as fresh (the web's rule). */
fun claimIsFresh(claimedAt: String?, nowMs: Long): Boolean {
    val t = claimedAt?.trim()?.replace(' ', 'T')?.let { s ->
        runCatching { OffsetDateTime.parse(s).toInstant().toEpochMilli() }.getOrNull()
            ?: runCatching { Instant.parse(s).toEpochMilli() }.getOrNull()
    } ?: return true
    return nowMs - t < FRESH_CLAIM_MS
}

/**
 * Whether this phone may clear [name]'s listening history itself: it is
 * [name]'s (not a shared screen), holds its claim key, and said "this is
 * me" more than 10 minutes ago — the three checks the server makes. Else
 * the dashboard's sentence, the reason, and what can be done.
 */
fun clearCheck(
    personId: Long,
    name: String,
    owner: DeviceOwner?,
    shared: Boolean,
    keyHeld: Boolean,
    nowMs: Long,
): ClearCheck {
    val mine = !shared && owner != null && owner.id == personId
    if (mine && keyHeld && !claimIsFresh(owner!!.claimedAt, nowMs)) return ClearCheck.Allowed
    val why = when {
        shared -> "This is a shared screen, so it can’t be anyone’s."
        mine && !keyHeld -> "This phone is $name’s but can’t prove it yet: say “this is me” again on this page."
        mine -> "This phone said it was $name’s less than 10 minutes ago. Until then only an admin can clear it."
        owner != null -> "This phone is ${owner.name}’s."
        else -> "If this is $name’s phone, say “this is me” on this page; from 10 minutes later $name can clear it from here."
    }
    // "this is me" helps unless this is a shared screen, or the phone is
    // already theirs with its key (then only the 10 minutes stand between).
    val offers = buildList {
        if (!shared && !(mine && keyHeld)) add(ClearOffer.ThisIsMe)
        add(ClearOffer.OpenDashboard)
    }
    return ClearCheck.NotHere(
        sentence = "Each person clears their own. Only $name’s own phone or browser, or an admin, can clear it.",
        why = why,
        offers = offers,
    )
}

/** The confirm dialog's words. */
fun clearConfirmText(name: String, count: Int): String =
    "This deletes the ${songsWord(count)} played for $name in every room and on every phone and browser. " +
        "Up next stops learning from them. Songs played for the whole household and for other people stay."

/**
 * `DELETE /api/music/listens/people/{personId}` from this phone at
 * [server], as its person: `X-Device-Id` and the claim key. Only after
 * [clearCheck] said [ClearCheck.Allowed] and the person confirmed. The
 * number of listens deleted. Throws [com.domovoi.app.net.ApiException]
 * (a 403 carries the server's sentence).
 */
suspend fun clearListeningHistory(
    api: ApiClient,
    server: String,
    deviceId: String,
    claimKey: String?,
    personId: Long,
): Int {
    val answer = api.delete(
        "/api/music/listens/people/$personId",
        headers = claimHeaders(deviceId, claimKey),
        onlyTo = server,
    )
    return ((answer as? JsonObject)?.get("deleted") as? JsonPrimitive)?.intOrNull ?: 0
}
