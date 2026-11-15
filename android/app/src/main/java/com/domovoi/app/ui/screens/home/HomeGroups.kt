package com.domovoi.app.ui.screens.home

import com.domovoi.app.ui.screens.music.GROUP_LEAVE_VERB
import com.domovoi.app.ui.screens.music.MusicGroup
import com.domovoi.app.ui.screens.music.MusicGroupsApi
import com.domovoi.app.ui.screens.music.apiErrorText
import com.domovoi.app.ui.screens.music.groupLeftToast
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray

/*
 * Groups (V060) on Home's room rows — web/static/home.jsx HomeGroupChoices.
 * What a room's share menu offers: other rooms (a named group, every room,
 * or one room) hear what plays here; this room hears what plays in another;
 * it leaves the group it is in. Built from the rooms read and the groups
 * read; the server says no (409, in words) when the music stopped meanwhile.
 * A quiet room only gets the joins. Pure, for the JVM tests.
 */

internal enum class GroupChoiceKind { Group, Everywhere, ShareRoom, Join, Leave }

/** One line of a room's share menu: what it says and the one call it makes. */
internal data class GroupChoice(
    val key: String,
    val kind: GroupChoiceKind,
    val label: String,
    val hint: String? = null,
    /** Under /api/music/groups/: shared, shared/join or shared/leave. */
    val path: String,
    val body: JsonObject,
    /** The rooms this touches, busy while it is out (the web's `[room, ...body.rooms]`). */
    val touched: List<String>,
)

/** The share menu for [r], among [rooms], with the household's named [groups]. */
internal fun homeGroupChoices(r: HomeRoom, rooms: List<HomeRoom>, groups: List<MusicGroup>): List<GroupChoice> {
    val room = r.room_id
    val g = r.now_playing?.group
    val inMine = (g?.rooms ?: listOf(room)).toSet()
    val playingHere = roomSong(r) != null
    val others = rooms.filter { it.room_id != room && it.online }
    val otherIds = others.map { it.room_id }.toSet()
    val out = mutableListOf<GroupChoice>()
    if (playingHere) {
        groups.forEach { gr ->
            val missing = gr.rooms.filter { it !in inMine && it in otherIds }
            if (missing.isNotEmpty()) {
                out += GroupChoice(
                    key = "group:${gr.id}", kind = GroupChoiceKind.Group,
                    label = "play in ${gr.name} too",
                    path = MusicGroupsApi.SHARED,
                    body = buildJsonObject { put("host_room", room); put("group_id", gr.id) },
                    touched = listOf(room),
                )
            }
        }
        if (others.any { it.room_id !in inMine }) {
            out += GroupChoice(
                key = "everywhere", kind = GroupChoiceKind.Everywhere,
                label = "play everywhere", hint = "every connected room hears it",
                path = MusicGroupsApi.SHARED,
                body = buildJsonObject { put("host_room", room); put("everywhere", true) },
                touched = listOf(room),
            )
        }
        others.filter { it.room_id !in inMine }.forEach { o ->
            out += GroupChoice(
                key = "share:${o.room_id}", kind = GroupChoiceKind.ShareRoom,
                label = "play in ${o.room_id} too",
                path = MusicGroupsApi.SHARED,
                body = buildJsonObject {
                    put("host_room", room)
                    putJsonArray("rooms") { add(o.room_id) }
                },
                touched = listOf(room, o.room_id),
            )
        }
    }
    others.filter { it.room_id !in inMine && roomSong(it) != null }.forEach { o ->
        out += GroupChoice(
            key = "join:${o.room_id}", kind = GroupChoiceKind.Join,
            label = "join ${o.room_id}", hint = "hear what plays in ${o.room_id}",
            path = MusicGroupsApi.JOIN,
            body = buildJsonObject { put("room_id", room); put("host_room", o.room_id) },
            touched = listOf(room),
        )
    }
    if (g != null) {
        out += GroupChoice(
            key = "leave", kind = GroupChoiceKind.Leave,
            label = GROUP_LEAVE_VERB, hint = "this room stops, the others play on",
            path = MusicGroupsApi.LEAVE,
            body = MusicGroupsApi.leaveBody(room),
            touched = listOf(room),
        )
    }
    return out
}

/** The toast once a choice went through (home.jsx onGroup). */
internal fun groupChoiceDoneToast(room: String, c: GroupChoice): String =
    if (c.kind == GroupChoiceKind.Leave) groupLeftToast(room) else "${c.label} · done"

/** ...and once it didn't: the server's own sentence (a 409 "nothing is
 *  playing in …" among them), after the web's verb. */
internal fun groupChoiceFailedToast(c: GroupChoice, e: Throwable): String =
    "${if (c.kind == GroupChoiceKind.Leave) GROUP_LEAVE_VERB else "share"} failed: ${apiErrorText(e)}"

/** TalkBack's name for a room's share button (home.jsx's aria-label). */
internal fun groupMenuLabel(room: String) = "play $room’s music in other rooms, or join another room"
