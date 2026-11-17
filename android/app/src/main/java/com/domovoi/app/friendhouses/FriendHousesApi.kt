package com.domovoi.app.friendhouses

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.failureText
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.IOException

// ---------------------------------------------------------------------------
// Friend Houses on the phone: "Serverless messaging and friend system
// between households" (the server's domovoi/friend_houses/, proxied by the
// dashboard's web/backend/api/friend_houses.py).
//
// What a household phone may do (owner decision 17): read the house inbox
// (one for the whole house), send a message, mark a conversation read, and
// see the friends and the shared lists. Everything about who the house
// trusts is an admin's, on the dashboard: the phone shows words for those,
// never a control (no misleading controls).
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
const val FH_DESCRIPTION = "Serverless messaging and friend system between households"

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
)

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
data class FhFriends(val friends: List<FhFriend> = emptyList())

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
) {
    val incoming: Boolean get() = direction != "out"
}

@Serializable
data class FhMessages(val messages: List<FhMessage> = emptyList())

@Serializable
data class FhSharedList(
    val id: String = "",
    /** active | invited (an admin hasn't accepted yet). */
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

    /** A conversation's page size (the dashboard's FH_PAGE). */
    const val PAGE = 50

    private val HOUSE = Regex("[0-9a-f]{64}")
    private val ID = Regex("[A-Za-z0-9_-]{1,64}")

    /** True for a house key a URL may carry: exactly 64 lowercase hex. */
    fun isHouseKey(value: String?): Boolean = value != null && HOUSE.matches(value)

    /** True for a friend id the server could have made (its _ID rule). */
    fun isFriendId(value: String?): Boolean = value != null && ID.matches(value)

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

    suspend fun friends(): List<FhFriend> = decode(FhPaths.FRIENDS, FhFriends.serializer()).friends

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

    private suspend fun <T> decode(path: String, s: kotlinx.serialization.KSerializer<T>): T =
        DomovoiJson.decodeFromJsonElement(s, api.get(path))
}
