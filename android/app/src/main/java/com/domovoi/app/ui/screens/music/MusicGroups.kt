package com.domovoi.app.ui.screens.music

import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import com.domovoi.app.LocalApp
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.ApiState
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.add

/*
 * Groups (V060): rooms sharing one music queue. The web is the spec
 * (web/static/home.jsx HomeGroupChoices / HomeGroupChipText, music.jsx's
 * queue header, satellites.jsx MusicGroupsCard), through the web's
 * /api/music/groups proxy (web/backend/api/music_groups.py):
 *
 *  - a named group is a lasting set of rooms the household made ("upstairs");
 *  - a shared queue is what plays now: the host room's player, heard in every
 *    room of `rooms` (host first). Its `name` is the named group's,
 *    "Everywhere", or null for rooms joined one by one.
 *
 * A member room's now-playing and queue are already the host's (the server
 * answers so); nothing here recomputes them. Every control is the
 * household's: hidden on a phone that isn't paired, and when the groups read
 * says this phone is not a household device (401/403) or the server is older
 * than groups (404) — silently, never as an error ([groupsFor]).
 */

/** The shared queue a room hears (`NowPlayingGroup`, web/backend/schemas.py). */
@Serializable
data class NowPlayingGroup(
    /** The shared queue's id (for `POST …/shared/{id}/end`). */
    val id: String = "",
    /** The named group's ("Upstairs"), "Everywhere", or null. */
    val name: String? = null,
    @SerialName("host_room") val hostRoom: String = "",
    /** Every room hearing it, the host first. */
    val rooms: List<String> = emptyList(),
    @SerialName("group_id") val groupId: Long? = null,
    val everywhere: Boolean = false,
)

/** A named group the household made. */
@Serializable
data class MusicGroup(
    val id: Long = 0,
    val name: String = "",
    val rooms: List<String> = emptyList(),
)

/** `GET /api/music/groups`: the named groups and the shared queues playing now. */
@Serializable
data class MusicGroupsList(
    val groups: List<MusicGroup> = emptyList(),
    val shared: List<NowPlayingGroup> = emptyList(),
)

/** The groups calls. Each is ONE request. */
internal class MusicGroupsApi(private val api: ApiClient) {

    /** A household read. Untracked: a refusal here hides the groups, it
     *  never sends the phone to the pairing screen. */
    suspend fun list(): MusicGroupsList = api.getUntracked(BASE).decode()

    /** Two or more rooms. A 400 says why the name can't be used, in words. */
    suspend fun create(name: String, rooms: List<String>) {
        api.post(BASE, groupBody(name, rooms))
    }

    suspend fun update(id: Long, name: String, rooms: List<String>) {
        api.patch("$BASE/$id", groupBody(name, rooms))
    }

    suspend fun delete(id: Long) {
        api.delete("$BASE/$id")
    }

    /** One of the share actions ([GroupAction]): start, join or leave. */
    suspend fun send(path: String, body: JsonObject) {
        api.post("$BASE/$path", body)
    }

    companion object {
        const val BASE = "/api/music/groups"
        const val SHARED = "shared"
        const val JOIN = "shared/join"
        const val LEAVE = "shared/leave"

        fun groupBody(name: String, rooms: List<String>) = buildJsonObject {
            put("name", name)
            putJsonArray("rooms") { rooms.forEach { add(it) } }
        }

        fun leaveBody(room: String) = buildJsonObject { put("room_id", room) }
    }
}

/**
 * One groups read, with the server it was asked of. [list] is null when the
 * server said no to this phone (401/403: not a household device) or doesn't
 * know groups (404: an older server); [refused] says which kind of null it is.
 */
internal data class GroupsRead(val server: String, val list: MusicGroupsList?, val refused: Boolean = false)

/** Statuses that hide the feature quietly: not the household's view, or an older server. */
internal fun groupsHiddenStatus(status: Int): Boolean = status == 401 || status == 403 || status == 404

/** Reads the groups for [server]; a hiding refusal is an answer, not an error. */
internal suspend fun readGroups(api: MusicGroupsApi, server: String): GroupsRead = try {
    GroupsRead(server, api.list())
} catch (e: ApiException) {
    if (groupsHiddenStatus(e.status)) GroupsRead(server, null, refused = true) else throw e
}

/**
 * The groups the controls may offer, or null when every group control is
 * hidden: an unpaired phone, no answer yet, an answer for another server
 * (read-after-await: server A's groups never show under server B), a
 * refusal (401/403/404), or a failed read (nothing the phone could offer
 * would be known to work).
 */
internal fun groupsFor(read: GroupsRead?, server: String, paired: Boolean): MusicGroupsList? {
    if (!paired || read == null || read.server != server) return null
    return read.list
}

/** The groups read, re-read when the music moves (the web's useApiObject). */
@Composable
internal fun rememberMusicGroups(): ApiState<GroupsRead?> {
    val app = LocalApp.current
    val token by app.prefs.deviceToken.collectAsState()
    val paired = !token.isNullOrBlank()
    return rememberApi(paired, eventTypes = GROUP_EVENTS) { a ->
        if (!paired) return@rememberApi null
        // The server is captured before the await: the answer is filed under it.
        val at = a.prefs.serverUrl.value
        readGroups(MusicGroupsApi(a.api), at)
    }
}

internal val GROUP_EVENTS = setOf("music.now_playing.changed")

// ─── Words (the web's, exactly) ──────────────────────────────────────────

/** The chip on a room in a group (home.jsx HomeGroupChipText): the group's
 *  name, or the rooms it plays with. Null outside a group. */
internal fun groupChipText(room: String, g: NowPlayingGroup?): String? {
    if (g == null) return null
    val others = g.rooms.filter { it != room }
    val with = if (others.isNotEmpty()) "with ${others.joinToString(", ")}" else ""
    val name = g.name?.takeIf { it.isNotEmpty() }
    return when {
        name != null -> if (with.isNotEmpty()) "$name · $with" else name
        with.isNotEmpty() -> with
        else -> null
    }
}

/** The queue tab's line (music.jsx): "Upstairs · shared with 2 other rooms
 *  (garage, office)". Null outside a group. */
internal fun queueGroupLine(room: String, g: NowPlayingGroup?): String? {
    if (g == null) return null
    val others = g.rooms.filter { it != room }
    if (others.isEmpty()) return null
    val name = g.name?.takeIf { it.isNotEmpty() }?.let { "$it · " }.orEmpty()
    val n = others.size
    return "${name}shared with $n other room${if (n == 1) "" else "s"} (${others.joinToString(", ")})"
}

internal const val GROUP_LEAVE_VERB = "leave the group"
internal fun groupLeftToast(room: String) = "$room left the group"

/** The Settings → Rooms card's explainer (satellites.jsx MusicGroupsCard). */
internal const val GROUPS_EXPLAINER =
    "Rooms in a group play the same music together: say “play jazz upstairs”, or use a room’s " +
        "share button on Home. “Everywhere” needs no group. Each room keeps its own volume. Speakers " +
        "you can hear from the same spot may sound a little out of step."

/** "no groups yet", or why there can't be any yet. */
internal fun groupsEmptyText(roomCount: Int) =
    if (roomCount >= 2) "no groups yet" else "set up two rooms to make a group"

/** The group form's save is ready: a name, two or more rooms, nothing in flight. */
internal fun groupFormReady(name: String, picked: Set<String>, busy: Boolean) =
    name.trim().isNotEmpty() && picked.size >= 2 && !busy

/** The rooms a group's form offers: every room, plus any the group holds
 *  that is gone from the roster, A→Z. */
internal fun groupFormRooms(roomIds: List<String>, group: MusicGroup?): List<String> =
    (roomIds + group?.rooms.orEmpty()).distinct().sorted()

internal const val GROUP_NAME_MAX = 40

/**
 * What a failed group save says: a 400 is the server's own sentence, shown
 * as it is (a name that sounds like a radio station); anything else is the
 * usual "save the group failed: …".
 */
internal fun groupSaveFailure(e: Throwable): String =
    if (e is ApiException && e.status == 400) apiErrorText(e)
    else "save the group failed: ${apiErrorText(e)}"

internal fun groupSavedToast(name: String, made: Boolean) = if (made) "group $name made" else "group $name saved"
internal fun groupDeletedToast(name: String) = "group $name deleted"
internal fun groupDeleteTitle(name: String) = "Delete the group $name?"
internal const val GROUP_DELETE_BODY = "Music playing in its rooms keeps playing."
