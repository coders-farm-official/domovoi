package com.domovoi.app.friendhouses

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.failureText
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.IOException

// ---------------------------------------------------------------------------
// Friend Houses on the phone: "Message and share lists with friends’
// houses, house to house" (the server's domovoi/friend_houses/, proxied by the
// dashboard's web/backend/api/friend_houses.py).
//
// What a household phone may do (owner decision 17, widened 2026-11-23):
// read the house inbox (one for the whole house), send a message, mark a
// conversation read, see the friends and the shared lists, and accept,
// decline or stop sharing a shared list. Everything about who the house
// trusts (requests, friends, friend files, blocking, a friend's message
// choice, starting a share), and saving or deleting a conversation, is an
// admin's, on the dashboard: the phone shows no control for those, and no
// words standing in for one (no misleading controls).
//
// The household token goes in the X-Device-Token header, as every request
// of this app's ApiClient does (net/DeviceAuth.kt): no cookie (the client
// has no cookie jar), no `?device_token=`. The only things a URL here ever
// carries are the other house's 64-hex house key and numbers ([FhPaths]):
// a message, a name or a title travels only in a body.
//
// Every body, name, title and last_error comes from ANOTHER house and is
// hostile text: the screens show it as plain Text only (FriendHousesScreen
// FhPlain), nothing here logs it, and nothing puts it in an exception
// message.
// ---------------------------------------------------------------------------

/** How the app describes the feature, word for word, wherever it does. */
const val FH_DESCRIPTION = "Message and share lists with friends’ houses, house to house, with no company server in between"

/** The sentence a 403 shows, from any call: the household phone asked for
 *  something only an admin may do. */
const val FH_ADMIN_ONLY = "Only an admin can do that, on the dashboard."

/** A message is 1 to 2,000 characters (the server's MAX_MESSAGE_CHARS,
 *  counted as Python counts them: code points). */
const val FH_MESSAGE_MAX = 2000

/** The `GET /api/friend-houses` payload; only what the phone uses. */
@Serializable
data class FhStatus(
    /** off | stopped | starting | on | unavailable | error. */
    val status: String = "",
    /** A sentence when stopped or error, else null. */
    val detail: String? = null,
    @SerialName("house_name") val houseName: String? = null,
    /** Unread messages in the house inbox. */
    val unread: Int = 0,
    /** The week's trust changes, `[{kind, friend, list, at}]`, newest
     *  first; absent on an older server. Kept raw and read row by row
     *  ([fhChanges]): one malformed row must never fail the status read,
     *  which would hide the whole feature. */
    val changes: JsonElement? = null,
)

/** One of the week's trust changes: `kind` is `friend` (a new friend
 *  house) or `shared_list` (one of our lists shared with a friend);
 *  `friend` and `list` are the other house's words (hostile text) and may
 *  be null; `at` is this server's ISO time. */
data class FhChange(val kind: String, val friend: String?, val list: String?, val at: String?)

/** The kinds this build can say; any other row is skipped. */
val FH_CHANGE_KINDS = setOf("friend", "shared_list")

/** [status]'s changes this build can say, in the server's order (newest
 *  first); null when the server sent none at all (an older server). */
fun fhChanges(status: FhStatus): List<FhChange>? {
    val raw = status.changes as? JsonArray ?: return null
    return raw.mapNotNull { e ->
        val o = e as? JsonObject ?: return@mapNotNull null
        fun str(k: String) = (o[k] as? JsonPrimitive)?.takeIf { it.isString }?.content
        val kind = str("kind") ?: return@mapNotNull null
        if (kind !in FH_CHANGE_KINDS) return@mapNotNull null
        FhChange(kind, str("friend"), str("list"), str("at"))
    }
}

@Serializable
data class FhFriend(
    val id: String = "",
    /** What THIS household calls them. */
    val name: String = "",
    /** Their house key (64 hex): the key of their conversation. */
    val house: String = "",
    /** inbox | sender | read, set by an admin. */
    val alert: String = "inbox",
    val verified: Boolean = false,
    @SerialName("added_at") val addedAt: String? = null,
    /** Their house was heard from in the last 15 minutes. Null: a server
     *  before presence (2026-11-17), which sends neither field. */
    val online: Boolean? = null,
    /** When their house was last heard from; null: not reached yet. */
    @SerialName("last_seen_at") val lastSeenAt: String? = null,
)

@Serializable
data class FhFriends(
    val friends: List<FhFriend> = emptyList(),
    /** The house's quiet hours right now, `{rooms, until, read_later}` or
     *  null; absent on an older server. Kept raw and read by
     *  [fhQuietHours]: a malformed value must never fail the friends read. */
    @SerialName("quiet_hours") val quietHours: JsonElement? = null,
)

/** Quiet hours in force now, in the house's time: [rooms] is `all` or
 *  `some` (only some rooms are quiet), [until] is "HH:MM", and [readLater]
 *  says whether a held message is read out once they end. */
data class FhQuietHours(val rooms: String, val until: String, val readLater: Boolean)

/** [friends]' quiet hours, or null: none now, an older server, or a value
 *  this build can't read. A missing `read_later` is false. */
fun fhQuietHours(friends: FhFriends): FhQuietHours? {
    val o = friends.quietHours as? JsonObject ?: return null
    fun str(k: String) = (o[k] as? JsonPrimitive)?.takeIf { it.isString }?.content
    val until = str("until") ?: return null
    val later = (o["read_later"] as? JsonPrimitive)?.takeIf { !it.isString }?.content == "true"
    return FhQuietHours(rooms = str("rooms") ?: "all", until = until, readLater = later)
}

@Serializable
data class FhConversation(
    val house: String = "",
    val name: String? = null,
    /** Null: no longer a friend (read only, no compose). */
    @SerialName("friend_id") val friendId: String? = null,
    val unread: Int = 0,
    @SerialName("last_id") val lastId: Long = 0,
    @SerialName("not_delivered") val notDelivered: Int = 0,
)

@Serializable
data class FhInbox(
    val conversations: List<FhConversation> = emptyList(),
    val unread: Int = 0,
) {
    /** Newest first (the server's order, made certain), and only those a
     *  URL may name ([FhPaths.isHouseKey]). */
    fun newestFirst(): List<FhConversation> =
        conversations.filter { FhPaths.isHouseKey(it.house) }.sortedByDescending { it.lastId }
}

@Serializable
data class FhMessage(
    val id: Long = 0,
    /** in | out. */
    val direction: String = "in",
    val body: String? = null,
    /** in: unread | read; out: queued | sent | failed. */
    val state: String = "",
    /** Unix seconds, the SENDER's clock: display only. */
    @SerialName("sent_at") val sentAt: Long? = null,
    /** This house's clock. */
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("delivered_at") val deliveredAt: String? = null,
    @SerialName("last_error") val lastError: String? = null,
    /** An incoming message whose alert quiet hours held back: "quiet_hours",
     *  else null (and null from an older server). */
    val held: String? = null,
    /** When a room said that held message afterwards (ISO), else null. */
    @SerialName("read_out_at") val readOutAt: String? = null,
) {
    val incoming: Boolean get() = direction != "out"
}

@Serializable
data class FhMessages(val messages: List<FhMessage> = emptyList())

@Serializable
data class FhSharedList(
    val id: String = "",
    /** active | invited (not accepted yet: theirs offered to this house, or
     *  ours waiting for them). */
    val state: String = "",
    /** us: our list, shared with them; them: their list, here. */
    val origin: String = "",
    /** What the sharing house calls it. */
    val title: String? = null,
    @SerialName("list_id") val listId: Long? = null,
    @SerialName("list_name") val listName: String? = null,
    val friend: String? = null,
    @SerialName("friend_id") val friendId: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("waiting_to_send") val waitingToSend: Int = 0,
)

@Serializable
data class FhSharedLists(@SerialName("shared_lists") val sharedLists: List<FhSharedList> = emptyList())

@Serializable
data class FhSent(val id: Long = 0, val state: String = "queued", @SerialName("created_at") val createdAt: String? = null)

/** The routes, as paths. Only a house key that is 64 lowercase hex, and
 *  numbers, ever go into one. */
object FhPaths {
    const val STATUS = "/api/friend-houses"
    const val FRIENDS = "$STATUS/friends"
    const val CONVERSATIONS = "$STATUS/conversations"
    const val MESSAGES = "$STATUS/messages"
    const val SHARED_LISTS = "$STATUS/shared-lists"
    /** The inbox's unread count alone (Home's row); 404 before 1.4.1. */
    const val UNREAD = "$STATUS/unread"

    /** A conversation's page size (the dashboard's FH_PAGE). */
    const val PAGE = 50

    private val HOUSE = Regex("[0-9a-f]{64}")
    private val ID = Regex("[A-Za-z0-9_-]{1,64}")
    private val SPACE = Regex("[0-9a-f]{32}")

    /** True for a house key a URL may carry: exactly 64 lowercase hex. */
    fun isHouseKey(value: String?): Boolean = value != null && HOUSE.matches(value)

    /** True for a friend id the server could have made (its _ID rule). */
    fun isFriendId(value: String?): Boolean = value != null && ID.matches(value)

    /** True for a shared list's id a URL may carry: exactly 32 lowercase hex. */
    fun isSpaceId(value: String?): Boolean = value != null && SPACE.matches(value)

    private fun space(value: String): String {
        require(isSpaceId(value)) { "not a shared list id" }
        return value
    }

    /** One shared list: DELETE stops sharing it. */
    fun sharedList(id: String): String = "$SHARED_LISTS/${space(id)}"

    fun acceptList(id: String): String = "${sharedList(id)}/accept"

    fun declineList(id: String): String = "${sharedList(id)}/decline"

    private fun house(value: String): String {
        // The value is never in the message: it is not one of ours.
        require(isHouseKey(value)) { "not a house key" }
        return value
    }

    /** One conversation, oldest first: [limit] (1-200) messages, those
     *  before the id [before] when it is given. */
    fun conversation(house: String, before: Long? = null, limit: Int = PAGE): String {
        val n = limit.coerceIn(1, 200)
        val earlier = before?.takeIf { it >= 1 }?.let { "&before=$it" }.orEmpty()
        return "$CONVERSATIONS/${house(house)}?limit=$n$earlier"
    }

    fun markRead(house: String): String = "$CONVERSATIONS/${house(house)}/read"

    // ---- admin (1.5.0, phone admin sign-in; design §1's Friend Houses jobs) ----

    const val REQUESTS = "$STATUS/requests"
    const val BLOCKED = "$STATUS/blocked"

    private fun id(value: String): String {
        require(isFriendId(value)) { "not an id" }
        return value
    }

    fun acceptRequest(id: String): String = "$REQUESTS/${id(id)}/accept"

    fun declineRequest(id: String): String = "$REQUESTS/${id(id)}/decline"

    /** DELETE removes the friend. */
    fun friend(id: String): String = "$FRIENDS/${id(id)}"

    fun blockFriend(id: String): String = "${friend(id)}/block"

    /** DELETE unblocks the house. */
    fun blocked(house: String): String = "$BLOCKED/${house(house)}"

    /** DELETE (admin security): every message with that house, gone. */
    fun conversationWith(house: String): String = "$CONVERSATIONS/${house(house)}"

    /** GET (admin security, read): every message with that house as a file
     *  (`domovoi-friend-houses-export-v1`). */
    fun conversationFile(house: String): String = "${conversationWith(house)}/export"
}

/** A friend request (admin read): in `waiting` (for an admin here) or
 *  `accepted` (still telling them); out `unsent` or `sent`. Names and
 *  last_error came from another house: shown only through FhPlain. */
@Serializable
data class FhRequest(
    val id: String = "",
    val direction: String = "in",
    val state: String = "",
    @SerialName("their_name") val theirName: String? = null,
    val name: String? = null,
    val code: String? = null,
    val trying: Boolean? = null,
    @SerialName("last_error") val lastError: String? = null,
)

@Serializable
data class FhRequests(val requests: List<FhRequest> = emptyList())

@Serializable
data class FhBlockedHouse(
    val house: String = "",
    val name: String? = null,
    @SerialName("blocked_at") val blockedAt: String? = null,
)

@Serializable
data class FhBlockedList(val blocked: List<FhBlockedHouse> = emptyList())

/**
 * The admin routes the phone uses (accept or decline a request, remove,
 * block, unblock), each through the phone's admin sign-in: [headers] and
 * [onlyTo] come from AdminSession.call / act, never stored here.
 */
class FriendHousesAdminApi(private val api: ApiClient) {
    suspend fun requests(headers: Map<String, String>, onlyTo: String): List<FhRequest> =
        DomovoiJson.decodeFromString(FhRequests.serializer(), api.raw("GET", FhPaths.REQUESTS, headers = headers, onlyTo = onlyTo)).requests

    suspend fun blocked(headers: Map<String, String>, onlyTo: String): List<FhBlockedHouse> =
        DomovoiJson.decodeFromString(FhBlockedList.serializer(), api.raw("GET", FhPaths.BLOCKED, headers = headers, onlyTo = onlyTo)).blocked

    suspend fun accept(id: String, name: String, headers: Map<String, String>, onlyTo: String) {
        api.post(FhPaths.acceptRequest(id), buildJsonObject { put("name", name.trim()) }, headers, onlyTo = onlyTo)
    }

    suspend fun decline(id: String, headers: Map<String, String>, onlyTo: String) {
        api.post(FhPaths.declineRequest(id), buildJsonObject { }, headers, onlyTo = onlyTo)
    }

    suspend fun remove(friendId: String, headers: Map<String, String>, onlyTo: String) {
        api.delete(FhPaths.friend(friendId), null, headers, onlyTo = onlyTo)
    }

    /** Block: returns whether the house is moving to a new address. */
    suspend fun block(friendId: String, keepMessages: Boolean, newAddress: Boolean, headers: Map<String, String>, onlyTo: String): Boolean {
        val body = buildJsonObject {
            put("keep_messages", keepMessages)
            put("new_address", newAddress)
        }
        val r = api.post(FhPaths.blockFriend(friendId), body, headers, onlyTo = onlyTo) as? JsonObject
        return (r?.get("new_address") as? JsonPrimitive)?.content == "true"
    }

    suspend fun unblock(house: String, headers: Map<String, String>, onlyTo: String) {
        api.delete(FhPaths.blocked(house), null, headers, onlyTo = onlyTo)
    }

    /** A conversation as the server's file, as it sent it (Save a copy). */
    suspend fun conversationFile(house: String, headers: Map<String, String>, onlyTo: String): String =
        api.raw("GET", FhPaths.conversationFile(house), headers = headers, onlyTo = onlyTo)

    /** Delete every message with that house: how many went. */
    suspend fun deleteConversation(house: String, headers: Map<String, String>, onlyTo: String): Int {
        val r = api.delete(FhPaths.conversationWith(house), null, headers, onlyTo = onlyTo) as? JsonObject
        return (r?.get("deleted") as? JsonPrimitive)?.content?.toIntOrNull() ?: 0
    }
}

/**
 * What `GET /api/friend-houses` says about showing Friend Houses at all.
 *
 * SHOWN only on a 2xx whose `status` is on, starting, stopped or error
 * (stopped and error: on, but not running now; the inbox still reads and a
 * send queues). HIDDEN on off or unavailable (whatever `unread` says: a
 * house that was on keeps its messages when turned off), on anything that
 * isn't a status at all, and on 401, 403 (a server before F5 refuses the
 * household token here), 404 (a server before Friend Houses) and 501
 * (before setup). UNKNOWN: no answer, or a 5xx the core may be behind:
 * the last verdict for that server stands.
 */
sealed interface FhAvailability {
    data class Shown(val status: FhStatus) : FhAvailability
    data object Hidden : FhAvailability
    data object Unknown : FhAvailability
}

object FhGate {
    val SHOWN_STATUSES = setOf("on", "starting", "stopped", "error")
    val HIDING_CODES = setOf(401, 403, 404, 501)

    /** [code]: the HTTP status, null when nothing answered. */
    fun decide(code: Int?, body: String?): FhAvailability = when {
        code == null -> FhAvailability.Unknown
        code in 200..299 -> {
            val status = runCatching { DomovoiJson.decodeFromString(FhStatus.serializer(), body.orEmpty()) }.getOrNull()
            if (status != null && status.status in SHOWN_STATUSES) FhAvailability.Shown(status) else FhAvailability.Hidden
        }
        code in HIDING_CODES -> FhAvailability.Hidden
        // "Wait": says nothing about whether it is on.
        code == 429 -> FhAvailability.Unknown
        code in 400..499 -> FhAvailability.Hidden
        else -> FhAvailability.Unknown
    }

    /** The verdict that holds after [next], given the last one [previous]
     *  for the same server (null: none yet, or another server's). */
    fun settle(previous: FhAvailability?, next: FhAvailability): FhAvailability =
        if (next is FhAvailability.Unknown) previous ?: FhAvailability.Hidden else next
}

/** The status line a shown screen carries, or null for none: the server's
 *  own sentence when stopped or in error, and a word while starting. */
fun fhStatusLine(status: FhStatus): String? = when (status.status) {
    "stopped", "error" -> status.detail?.takeIf { it.isNotBlank() }
        ?: if (status.status == "error") "Friend Houses isn't running right now." else "Friend Houses is stopped right now."
    "starting" -> status.detail?.takeIf { it.isNotBlank() } ?: "Friend Houses is starting."
    else -> null
}

/**
 * What a failed call shows, as the dashboard shows it: a 403 is
 * [FH_ADMIN_ONLY]; a 400 or 404 is the server's `detail`, as written (a
 * sentence for a person); anything else is the app's usual failure line.
 * Never the request's own text.
 */
fun fhProblem(action: String, e: Throwable): String {
    if (e is ApiException) {
        if (e.status == 403) return FH_ADMIN_ONLY
        if (e.status == 400 || e.status == 404) detailOf(e.body)?.let { return it }
        return "$action failed (${e.status})"
    }
    return failureText(action, e)
}

/** The `detail` string of a JSON error body, or null (none, or not a
 *  sentence: FastAPI's validation list). */
internal fun detailOf(body: String?): String? = runCatching {
    ((DomovoiJson.parseToJsonElement(body.orEmpty()) as? JsonObject)?.get("detail") as? JsonPrimitive)
        ?.takeIf { it.isString }?.content?.takeIf { it.isNotBlank() }
}.getOrNull()

/** A message's length as the server counts it (code points). */
fun fhLength(text: String): Int = text.codePointCount(0, text.length)

/** Whether [text] may be sent: something besides spaces, at most
 *  [FH_MESSAGE_MAX] characters. */
fun fhSendable(text: String): Boolean = text.isNotBlank() && fhLength(text) <= FH_MESSAGE_MAX

/** The household routes, over the app's one client. */
class FriendHousesApi(private val api: ApiClient) {
    /**
     * The availability gate's read. Never raises the pairing screen and
     * never throws: a 401 or 403 HERE means "not available on this server"
     * (a server before F5), not a bad token.
     */
    suspend fun status(): FhAvailability = try {
        FhGate.decide(200, api.raw("GET", FhPaths.STATUS, trackPairing = false))
    } catch (e: CancellationException) {
        throw e
    } catch (e: ApiException) {
        FhGate.decide(e.status, e.body)
    } catch (e: IOException) {
        FhGate.decide(null, null)
    }

    suspend fun friends(): List<FhFriend> = roster().friends

    /** The friends payload whole: the friends and the house's quiet hours
     *  ([fhQuietHours]). */
    suspend fun roster(): FhFriends = decode(FhPaths.FRIENDS, FhFriends.serializer())

    suspend fun inbox(): FhInbox = decode(FhPaths.CONVERSATIONS, FhInbox.serializer())

    /** One page of a conversation, oldest first ([FhPaths.conversation]). */
    suspend fun conversation(house: String, before: Long? = null, limit: Int = FhPaths.PAGE): List<FhMessage> =
        decode(FhPaths.conversation(house, before, limit), FhMessages.serializer()).messages

    /** Mark that conversation read: how many were. */
    suspend fun markRead(house: String): Int {
        val r = api.post(FhPaths.markRead(house)) as? JsonObject
        return (r?.get("marked") as? JsonPrimitive)?.content?.toIntOrNull() ?: 0
    }

    /** Queue a message to a friend. The text goes in the body only. */
    suspend fun send(friendId: String, text: String): FhSent {
        require(FhPaths.isFriendId(friendId)) { "not a friend id" }
        val body = buildJsonObject {
            put("friend_id", friendId)
            put("text", text)
        }
        return DomovoiJson.decodeFromJsonElement(FhSent.serializer(), api.post(FhPaths.MESSAGES, body))
    }

    suspend fun sharedLists(): List<FhSharedList> =
        decode(FhPaths.SHARED_LISTS, FhSharedLists.serializer()).sharedLists

    /** Accept a list a friend offered this house: it becomes one of the
     *  house's lists, named [name] when given (a name already taken here is
     *  a 400 with the server's sentence). Returns the name it was added as,
     *  when the server says. */
    suspend fun acceptList(id: String, name: String? = null): String? {
        val body = buildJsonObject { name?.trim()?.takeIf { it.isNotEmpty() }?.let { put("name", it) } }
        val r = api.post(FhPaths.acceptList(id), body) as? JsonObject
        return (r?.get("name") as? JsonPrimitive)?.takeIf { it.isString }?.content
    }

    /** Decline a list a friend offered: not added, and they aren't told. */
    suspend fun declineList(id: String) {
        api.post(FhPaths.declineList(id))
    }

    /** Stop sharing a list (ours or theirs): both houses keep a copy. */
    suspend fun stopSharing(id: String) {
        api.delete(FhPaths.sharedList(id))
    }

    /**
     * The house inbox's unread count, for Home. Null where the server has
     * no such read for this phone: a server before it (404), or one that
     * refuses the household token here (401, 403), or before setup (501).
     * Never raises the pairing screen; anything else throws.
     */
    suspend fun unread(): Int? = try {
        val r = DomovoiJson.parseToJsonElement(api.raw("GET", FhPaths.UNREAD, trackPairing = false)) as? JsonObject
        (r?.get("unread") as? JsonPrimitive)?.content?.toIntOrNull()?.coerceAtLeast(0) ?: 0
    } catch (e: ApiException) {
        if (e.status in FhGate.HIDING_CODES) null else throw e
    }

    private suspend fun <T> decode(path: String, s: kotlinx.serialization.KSerializer<T>): T =
        DomovoiJson.decodeFromJsonElement(s, api.get(path))
}
