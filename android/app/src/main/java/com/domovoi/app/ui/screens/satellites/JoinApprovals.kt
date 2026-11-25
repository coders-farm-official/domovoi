package com.domovoi.app.ui.screens.satellites

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/*
 * Rooms and devices asking to join (1.5.0; docs/API_REFERENCE.md §3.7a,
 * "Approval"): the first admin job on the phone (design §1). A device that
 * was set up shows a six-digit code; the admin types it here, picks the room
 * it is in, and the server binds it. Household words only: no ids are shown.
 */

/** One request, as GET /api/satellites/approvals lists it. */
@Serializable
data class JoinRequest(
    val satellite_id: String? = null,
    /** Repeats [satellite_id] for older servers. */
    val room_id: String = "",
    val requested_room: String? = null,
    val requested_room_name: String? = null,
    val has_code: Boolean = false,
    val proof: Boolean = false,
    val waiting_for_device: Boolean = false,
    val code_rejected: Boolean = false,
    val board: String? = null,
    val sat_type: String? = null,
    val last_seen_at: String? = null,
) {
    /** The id the approve and reject paths take. */
    val id: String get() = satellite_id ?: room_id
}

/** A room, as GET /api/rooms lists it (only what the picker needs). */
@Serializable
data class JoinRoom(val room_id: String, val name: String = "")

@Serializable
data class JoinRoomList(val rooms: List<JoinRoom> = emptyList())

/** Which room the device goes in: one that exists, or a new one. */
sealed interface JoinChoice {
    data class Existing(val roomId: String, val name: String) : JoinChoice
    data class NewRoom(val name: String) : JoinChoice
}

/** "A phone", "A Raspberry Pi", "A room device": what is asking. */
fun joinDeviceWords(r: JoinRequest): String = when {
    r.board == "android_room" -> "An Android phone or tablet"
    r.board == "ios_room" -> "An iPhone or iPad"
    r.board?.contains("pi", ignoreCase = true) == true -> "A Raspberry Pi"
    r.sat_type == "pc" || r.board?.contains("pc", ignoreCase = true) == true -> "A computer"
    else -> "A room device"
}

/** A name for a new room from the device's id: "kitchen_2" → "Kitchen 2". */
fun joinNewRoomName(r: JoinRequest): String =
    r.id.replace('_', ' ').replace('-', ' ').trim().replaceFirstChar { it.uppercase() }.ifBlank { "New room" }

/** The picker's choices: every room, then a new one. */
fun joinChoices(r: JoinRequest, rooms: List<JoinRoom>): List<JoinChoice> =
    rooms.map { JoinChoice.Existing(it.room_id, it.name.ifBlank { "Unnamed room" }) } + JoinChoice.NewRoom(joinNewRoomName(r))

/** What the picker starts on (§3.7a): the room the device asked for when it
 *  exists with a name, else a new room named after the device. */
fun joinPreselect(r: JoinRequest, rooms: List<JoinRoom>): JoinChoice {
    val asked = r.requested_room?.let { id -> rooms.firstOrNull { it.room_id == id } }
    return if (asked != null && !r.requested_room_name.isNullOrBlank()) {
        JoinChoice.Existing(asked.room_id, asked.name.ifBlank { r.requested_room_name })
    } else {
        JoinChoice.NewRoom(joinNewRoomName(r))
    }
}

/** The approve body: the code and exactly one room choice. */
fun joinApproveBody(code: String, choice: JoinChoice): JsonObject = buildJsonObject {
    put("code", code)
    when (choice) {
        is JoinChoice.Existing -> put("room_id", choice.roomId)
        is JoinChoice.NewRoom -> put("new_room_name", choice.name.trim())
    }
}

/** Six digits (the server refuses anything else). */
fun joinCodeOk(code: String): Boolean = code.length == 6 && code.all { it in '0'..'9' }

/** The line under a request: what is happening with it. */
fun joinStatus(r: JoinRequest): String? = when {
    r.waiting_for_device -> "Code sent: the device checks it on its next visit, within ten minutes."
    r.code_rejected -> "The last code typed wasn't this device's. Read it again from the device."
    else -> null
}

/** What the server's answer to approve means, in plain words. */
fun joinApprovedText(waitingForDevice: Boolean, choice: JoinChoice): String {
    val room = when (choice) {
        is JoinChoice.Existing -> choice.name
        is JoinChoice.NewRoom -> choice.name.trim()
    }
    return if (waitingForDevice) "Code sent. The device joins $room once it checks the code, within ten minutes."
    else "Added to $room."
}

/** A refused approve, in plain words (§3.7a, §2.5). */
fun joinRefusalText(status: Int, detail: String?): String = when (status) {
    422 -> detail?.takeIf { !it.contains("code", ignoreCase = true) } ?: "That code doesn't match the one the device shows."
    429 -> "Too many tries for this device. Wait five minutes and try again."
    409 -> detail ?: "This request isn't waiting any more."
    404 -> "That room is gone. Pick another."
    else -> detail ?: "That didn't work (HTTP $status)."
}
