package com.domovoi.app.ui.screens.friendhouses

import com.domovoi.app.friendhouses.FhChange
import com.domovoi.app.friendhouses.FhConversation
import com.domovoi.app.friendhouses.FhFriend
import com.domovoi.app.friendhouses.FhMessage
import com.domovoi.app.friendhouses.FhPaths
import com.domovoi.app.friendhouses.FhSharedList
import com.domovoi.app.friendhouses.FhStatus
import com.domovoi.app.friendhouses.fhChanges
import com.domovoi.app.friendhouses.FriendHousesApi
import com.domovoi.app.friendhouses.fhProblem
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.parseInstant
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.screens.home.plural
import kotlinx.coroutines.CancellationException
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlin.math.abs

// ---------------------------------------------------------------------------
// The Friend houses screen's words and logic, apart from Compose so the JVM
// tests can pin them. The words are the dashboard's (web/static/
// friend_houses.jsx), at phone width. Everything an admin does there is a
// line of words here, never a control: the household token is refused it.
// ---------------------------------------------------------------------------

object FhWords {
    const val TITLE = "Friend houses"

    const val FRIENDS_ADMIN =
        "Only an admin can add, remove or block friends, compare the codes, or change what a new message does, on the dashboard."
    const val LISTS_ADMIN =
        "Only an admin can share a list, accept one or stop sharing, on the dashboard."
    const val CONVERSATION_ADMIN =
        "Only an admin can save a copy of these messages or delete them, on the dashboard."
    const val FORMER_FRIEND = "No longer a friend: messages can't be sent to this house."
    const val PHONE_FOLLOWS =
        "This phone follows the same choice: a badge for the inbox, a notification saying who it's from, or one with the message."

    const val NO_MESSAGES = "no messages yet"
    const val NO_MESSAGES_SUB = "press message on a friend to write one"
    const val NO_FRIENDS = "no friends yet"
    const val NO_FRIENDS_SUB = "An admin can add friend houses on the dashboard."
    const val NO_SHARED = "no shared lists"
    const val NOT_VERIFIED = "not verified"
    const val VERIFIED = "verified"
    const val LOAD_EARLIER = "Load earlier"

    /** A list a friend shared that an admin hasn't accepted yet. */
    fun invited(friend: String, title: String) = "$friend shared a list: $title. An admin can accept it in the dashboard."

    /** What a new message from this friend does, as words (the dashboard's
     *  three choices). The admin sets it; the phone only says it. */
    fun alert(level: String?): String = when (level) {
        "inbox" -> "New messages: only in the inbox"
        "sender" -> "New messages: the rooms say who it's from"
        "read" -> "New messages: read aloud in the rooms"
        else -> "New messages: as set on the dashboard"
    }

    const val THIS_WEEK = "This week"
    const val NOT_YOU =
        "Not you? Only an admin can remove or block the house and change the admin password, on the dashboard."

    /** At most this many of the week's changes are listed. */
    const val CHANGES_SHOWN = 5

    /** One of the week's changes in the dashboard's words, with its time
     *  when it can be shown: "The Smiths became a friend house · 2d ago",
     *  "\u201cGroceries\u201d was shared with The Patels · 5h ago". */
    fun change(c: FhChange): String? {
        val what = when (c.kind) {
            "friend" -> "${c.friend?.takeIf { it.isNotBlank() } ?: "A house"} became a friend house"
            "shared_list" -> "\u201c${c.list?.takeIf { it.isNotBlank() } ?: "A list"}\u201d was shared with " +
                (c.friend?.takeIf { it.isNotBlank() } ?: "a friend house")
            else -> return null
        }
        return fhIso(c.at)?.let { "$what \u00b7 ${relTime(it)}" } ?: what
    }

    /** The "This week" lines for [status]: newest first, at most
     *  [CHANGES_SHOWN]; empty when there is nothing to say (or an older
     *  server said nothing). */
    fun changes(status: FhStatus): List<String> =
        fhChanges(status).orEmpty()
            .sortedByDescending { fhEpochSec(parseInstant(fhIso(it.at))?.epochSecond) ?: -1L }
            .mapNotNull { change(it) }
            .take(CHANGES_SHOWN)

    const val ONLINE = "Online"
    const val NOT_REACHED = "Not reached yet"

    /** Whether the friend's HOUSE is running and reachable (never who is
     *  home): "Online", "Last seen <relTime>", "Not reached yet"; null for
     *  a server that doesn't say (show nothing). */
    fun presence(f: FhFriend): String? = when {
        f.online == null -> null
        f.online -> ONLINE
        f.lastSeenAt.isNullOrBlank() -> NOT_REACHED
        // A time that can't be shown says nothing rather than something wrong.
        fhIso(f.lastSeenAt) == null -> null
        else -> "Last seen ${relTime(f.lastSeenAt)}"
    }

    fun unread(n: Int) = "$n unread"
    fun notDelivered(n: Int) = "$n not delivered"
    fun waiting(n: Int) = "${plural(n, "change")} waiting to send"

    fun friendName(name: String?) = name?.takeIf { it.isNotBlank() } ?: "a former friend"
}

/** An outgoing message's state in plain words, with its tone; null for an
 *  incoming one. */
fun fhOutState(m: FhMessage): Pair<Tone, String>? {
    if (m.incoming) return null
    val err = m.lastError?.takeIf { it.isNotBlank() }
    return when (m.state) {
        "sent" -> Tone.Ok to "Delivered"
        "failed" -> Tone.Err to (if (err != null) "Not delivered: $err" else "Not delivered")
        else -> if (err != null) Tone.Warn to "Still trying: $err" else Tone.Idle to "Waiting to send"
    }
}

/** More than this apart, an incoming message also shows the sender's time. */
const val FH_CLOCK_SKEW_SEC = 5 * 60L

/** The latest time this screen will format: 9999-12-31T23:59:59Z. Every
 *  time on the wire is checked against 0..this before it reaches an
 *  Instant, arithmetic or a formatter: `sent_at` is the OTHER house's,
 *  signed by it and stored unchecked, and a value like 2^62 would throw
 *  in the formatter on every open of that conversation. */
const val FH_MAX_EPOCH_SEC = 253_402_300_799L

/** [sec] when it is a time this screen can show (0..[FH_MAX_EPOCH_SEC]), else null. */
fun fhEpochSec(sec: Long?): Long? = sec?.takeIf { it in 0..FH_MAX_EPOCH_SEC }

/** An ISO time from the server when it parses to a time this screen can
 *  show, else null ([fhEpochSec]). */
fun fhIso(iso: String?): String? = iso?.takeIf { fhEpochSec(parseInstant(it)?.epochSecond) != null }

/** "4m ago" for a server time, or "—" for one that is missing, unreadable
 *  or out of range: never a throw. */
fun fhRelTime(iso: String?): String = fhIso(iso)?.let { relTime(it) } ?: "—"

/** The sender's own clock, when it disagrees with this house's by more
 *  than five minutes: "sent <time> (their clock)"; else null, and null for
 *  a `sent_at` outside 0..[FH_MAX_EPOCH_SEC]. */
fun fhTheirClock(m: FhMessage, zone: ZoneId = ZoneId.systemDefault()): String? {
    if (!m.incoming) return null
    val here = fhEpochSec(parseInstant(m.createdAt)?.epochSecond) ?: return null
    val theirs = fhEpochSec(m.sentAt) ?: return null
    if (abs(theirs - here) <= FH_CLOCK_SKEW_SEC) return null
    val at = java.time.Instant.ofEpochSecond(theirs)
    return "sent ${DateTimeFormatter.ofPattern("MMM d, h:mm a").withZone(zone).format(at)} (their clock)"
}

/** A conversation as the screen opens it: from the inbox, else a friend
 *  never written to yet. Null when [house] is not a key a URL may carry. */
fun fhOpenConversation(house: String?, inbox: List<FhConversation>, friends: List<FhFriend>): FhConversation? {
    if (!FhPaths.isHouseKey(house)) return null
    inbox.firstOrNull { it.house == house }?.let { return it }
    val f = friends.firstOrNull { it.house == house } ?: return null
    return FhConversation(house = f.house, name = f.name, friendId = f.id)
}

/** The shared lists the screen shows: active first, then the offers. */
fun fhSharedOrder(lists: List<FhSharedList>): List<FhSharedList> =
    lists.filter { it.state == "active" } + lists.filter { it.state == "invited" }

/** What a read gives the screen: the data, or a sentence to show (a 403's
 *  is [com.domovoi.app.friendhouses.FH_ADMIN_ONLY]). */
sealed interface FhRead<out T> {
    data class Ok<T>(val value: T) : FhRead<T>
    data class Problem(val text: String) : FhRead<Nothing>
}

suspend fun <T> fhRead(action: String, block: suspend () -> T): FhRead<T> = try {
    FhRead.Ok(block())
} catch (e: CancellationException) {
    throw e
} catch (e: Exception) {
    FhRead.Problem(fhProblem(action, e))
}

/** One conversation as loaded so far: oldest first, by id. */
data class FhThread(
    val messages: List<FhMessage> = emptyList(),
    /** Earlier messages to load ("Load earlier"). */
    val more: Boolean = false,
    val loaded: Boolean = false,
)

/** [rows] merged into [current] by id (a re-read replaces what it holds),
 *  oldest first: the newest is at the bottom. */
fun fhMerge(current: List<FhMessage>, rows: List<FhMessage>): List<FhMessage> {
    val byId = LinkedHashMap<Long, FhMessage>()
    current.forEach { byId[it.id] = it }
    rows.forEach { byId[it.id] = it }
    return byId.values.sortedBy { it.id }
}

/** Loading a conversation: the latest page, earlier pages by `before`, and
 *  marking it read. */
class FhThreadLoader(private val api: FriendHousesApi, val house: String) {
    /** The newest page, merged in. [first]: the open, which also decides
     *  whether there are earlier ones. Returns the thread and whether an
     *  incoming message is still unread (mark it read). */
    suspend fun latest(thread: FhThread, first: Boolean): Pair<FhThread, Boolean> {
        val rows = api.conversation(house, limit = FhPaths.PAGE)
        val merged = fhMerge(thread.messages, rows)
        val more = if (first) rows.size >= FhPaths.PAGE else thread.more
        val unread = rows.any { it.incoming && it.state == "unread" }
        return FhThread(merged, more, loaded = true) to unread
    }

    /** The page before the oldest held. */
    suspend fun earlier(thread: FhThread): FhThread {
        val oldest = thread.messages.minOfOrNull { it.id } ?: return thread
        val rows = api.conversation(house, before = oldest, limit = FhPaths.PAGE)
        return FhThread(fhMerge(thread.messages, rows), more = rows.size >= FhPaths.PAGE, loaded = true)
    }

    suspend fun markRead(): Int = api.markRead(house)
}
