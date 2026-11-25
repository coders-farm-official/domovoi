package com.domovoi.app.ui.screens.home

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.ui.components.Tone
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

// ---------------------------------------------------------------------------
// Update notices (1.5.0): Home's "Domovoi 1.5.0 is available" row, the
// web's HomeUpdateRow (web/static/home.jsx). The server decides everything:
// only a newer SIGNED release the house is behind makes a notice, never a
// commit on main (domovoi/update_notice.py). The phone shows what
// GET /api/updates/notice answers, a device-tier read, so only a paired
// phone asks, and an unpaired one never shows the row.
//
// The phone has no admin sign-in on this branch, so every phone gets the
// household member's row: the words and "An admin can install it.", with no
// button. [updateNoticeRow]'s `admin` parameter is the seam for the admin's
// Update once the phone can sign an admin in.
// ---------------------------------------------------------------------------

/** How often coming back to Home re-reads the notice: the web's
 *  HOME_VERSION_GAP_MS (the notice changes once a day at most). */
internal const val HOME_UPDATE_NOTICE_GAP_MS = 10 * 60_000L

/** A notice as GET /api/updates/notice answers it, held to its shape. */
internal data class UpdateNotice(
    /** `X.Y.Z`, digits only, at most 4 per part (the server's TAG_RE). */
    val version: String,
    /** Up to three plain-text strings; empty when the release has none. */
    val highlights: List<String>,
    /** The update unit is running now. */
    val updating: Boolean,
)

internal object UpdateNotices {
    const val PATH = "/api/updates/notice"

    /** The web's HOME_VERSION_RE: at most 14 characters, so it can't break a line. */
    private val VERSION = Regex("""^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$""")
    const val HIGHLIGHTS_MAX = 3
    const val HIGHLIGHT_MAX_CHARS = 80

    /**
     * The read, lenient. Whatever the server ANSWERS decides: an older
     * server's 404, a refusal (401/403), any other 4xx, or a body that isn't
     * the shape is no notice, and a refusal never sends the phone to pairing
     * from here. No answer (no network, a 5xx, a 429) throws, so
     * [com.domovoi.app.net.rememberApi] keeps what Home showed and records
     * an error that nothing on Home draws: never an error on Home.
     */
    suspend fun read(api: ApiClient): UpdateNotice? {
        val body = try {
            api.raw("GET", PATH, trackPairing = false)
        } catch (e: ApiException) {
            if (e.status == 429 || e.status >= 500) throw e
            return null
        }
        return parse(body)
    }

    fun parse(body: String?): UpdateNotice? =
        body?.let { runCatching { DomovoiJson.parseToJsonElement(it) }.getOrNull() }?.let(::parse)

    /**
     * `{available: true, version, highlights, found_by, found_at, updating}`
     * is a notice; `{available: false, updating: false}`, or anything else,
     * is none. `available` must be the JSON boolean true, and `version`
     * `X.Y.Z`, or there is no row (web HomeUpdateRow).
     */
    fun parse(json: JsonElement): UpdateNotice? {
        val obj = json as? JsonObject ?: return null
        if (!isTrue(obj["available"])) return null
        val version = (obj["version"] as? JsonPrimitive)?.takeIf { it.isString }?.content
            ?.takeIf { VERSION.matches(it) } ?: return null
        return UpdateNotice(version, highlights(obj["highlights"]), isTrue(obj["updating"]))
    }

    /**
     * The release's highlights as plain text (the web's updateHighlights):
     * strings of 1 to 80 characters counted as code points (an emoji is one,
     * as the server counts them), the first three. A highlight holding a
     * control character (a line break included) is dropped rather than
     * drawn: each one is one plain line. Not a list: none.
     */
    fun highlights(json: JsonElement?): List<String> {
        val list = json as? JsonArray ?: return emptyList()
        return list.asSequence()
            .mapNotNull { (it as? JsonPrimitive)?.takeIf { p -> p.isString }?.content }
            .filter { it.isNotBlank() && it.codePointCount(0, it.length) <= HIGHLIGHT_MAX_CHARS && it.none(::isControl) }
            .take(HIGHLIGHTS_MAX)
            .toList()
    }

    private fun isTrue(e: JsonElement?): Boolean {
        val p = e as? JsonPrimitive ?: return false
        return !p.isString && p.content == "true"
    }

    private fun isControl(c: Char): Boolean =
        Character.isISOControl(c) || c == ' ' || c == ' '
}

/** A notice read, with the server it was read from. */
internal data class ServerNotice(val server: String, val notice: UpdateNotice?) {
    /** The notice, when it was read from [activeServer]; null otherwise. */
    fun noticeFor(activeServer: String): UpdateNotice? = notice.takeIf { server == activeServer }
}

/** The words of the update row, apart from the row itself (key, tone). */
internal data class UpdateNoticeLines(
    /** "Domovoi 1.5.0 is available", or "Domovoi is updating…". */
    val title: String,
    /** "An admin can install it." for everyone who can't; null for an admin. */
    val sub: String?,
    /** Plain-text bullets, at most three. */
    val highlights: List<String>,
    /** An admin's Update action. False on every phone today (see [updateNoticeRow]). */
    val canUpdate: Boolean,
)

internal object UpdateNoticeWords {
    fun available(version: String) = "Domovoi $version is available"
    const val UPDATING = "Domovoi is updating…"
    const val ADMIN_CAN_INSTALL = "An admin can install it."
}

/**
 * Home's update row (web HomeUpdateRow): null when there is no notice, and
 * always null on an unpaired phone (the read is device tier). While the
 * update runs, "Domovoi is updating…" alone.
 *
 * [admin] is the seam for the phone's admin sign-in, which lives on another
 * branch: an admin (not on a shared screen) gets the Update action and no
 * "An admin can install it.". No caller passes true yet, so every phone
 * shows the household member's row, with no button that can't do what it
 * says.
 */
internal fun updateNoticeRow(
    notice: UpdateNotice?,
    paired: Boolean,
    admin: Boolean = false,
    shared: Boolean = false,
): AttentionRow? {
    if (!paired || notice == null) return null
    val lines = if (notice.updating) {
        UpdateNoticeLines(UpdateNoticeWords.UPDATING, sub = null, highlights = emptyList(), canUpdate = false)
    } else {
        val canUpdate = admin && !shared
        UpdateNoticeLines(
            title = UpdateNoticeWords.available(notice.version),
            sub = if (canUpdate) null else UpdateNoticeWords.ADMIN_CAN_INSTALL,
            highlights = notice.highlights,
            canUpdate = canUpdate,
        )
    }
    return AttentionRow(key = "update-available", tone = Tone.Brand, text = lines.title, target = null, update = lines)
}
