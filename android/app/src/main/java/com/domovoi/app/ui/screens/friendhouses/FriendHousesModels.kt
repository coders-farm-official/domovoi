package com.domovoi.app.ui.screens.friendhouses

import com.domovoi.app.friendhouses.FhChange
import com.domovoi.app.friendhouses.FhConversation
import com.domovoi.app.friendhouses.FhFriend
import com.domovoi.app.friendhouses.FhMessage
import com.domovoi.app.friendhouses.FhPaths
import com.domovoi.app.friendhouses.FhQuietHours
import com.domovoi.app.friendhouses.FhSharedList
import com.domovoi.app.friendhouses.FhStatus
import com.domovoi.app.friendhouses.fhChanges
import com.domovoi.app.friendhouses.FriendHousesApi
import com.domovoi.app.friendhouses.fhProblem
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.parseInstant
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.screens.home.plural
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.domovoi.app.net.ApiException
import kotlinx.coroutines.CancellationException
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.abs

// ---------------------------------------------------------------------------
// The Friend houses screen's words and logic, apart from Compose so the JVM
// tests can pin them. The words are the dashboard's (web/static/
// friend_houses.jsx), at phone width. The phone holds the household token,
// so it shows the dashboard's Messages and Shared lists, and none of its
// admin zone ("Friends and connections": requests, friends, friend files,
// this house, blocked houses), nor a conversation's "Save a copy" and
// "Delete", nor "share a list": no control, and no words for one.
// ---------------------------------------------------------------------------

object FhWords {
    const val TITLE = "Friend houses"

    const val FORMER_FRIEND = "No longer a friend: messages can't be sent to this house."

    const val NO_MESSAGES = "no messages yet"
    const val NO_MESSAGES_SUB = "messages with your friend houses arrive here"
    const val NO_SHARED = "no shared lists yet"
    const val LOAD_EARLIER = "Load earlier"

    // ---- shared lists (the dashboard's FhSharedListsCard) ----

    const val SHARE_EXPLAIN =
        "Changes either house makes show up in both, usually within a minute. Each house keeps its own copy and its own name for it."
    const val OFFERED = "offered to this house"
    const val SHARED_NOW = "shared now"
    const val ACCEPT = "Accept"
    const val DECLINE = "Decline"
    const val STOP_SHARING = "Stop sharing"
    const val RENAME = "give it another name here"
    const val CALL_IT = "Call it here"
    const val OPEN_IN_LISTS = "open in Lists"
    const val WAITING_FOR_THEM = "waiting for them to accept"
    const val DECLINE_TITLE = "Decline this list?"
    const val STOP_TITLE = "Stop sharing?"
    const val DECLINED = "list declined"
    const val STOPPED = "stopped sharing — you both keep your own copy"

    /** A list a friend offered this house: "The Smiths shared a list: “Camping”". */
    fun offered(friend: String, title: String) = "$friend shared a list: “$title”"

    /** A list shared now: "Groceries — shared with The Smiths" (ours) or
     *  "Camping — from The Smiths" (theirs). */
    fun shared(listName: String, friend: String, ours: Boolean) =
        if (ours) "$listName — shared with $friend" else "$listName — from $friend"

    /** Theirs, when its title there isn't its name here. */
    fun theyCallIt(title: String) = "they call it “$title”"

    fun declineBody(title: String?, friend: String) =
        "“${title?.takeIf { it.isNotEmpty() } ?: "This list"}” won’t be added here. $friend isn’t told."

    fun stopBody(listName: String, friend: String) =
        "Stop sharing $listName with $friend? You both keep your own copy."

    fun added(name: String) = "added to Lists as $name"

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
 *  never written to yet (none on the phone for now: its friends list is
 *  in the admin zone). Null when [house] is not a key a URL may carry. */
fun fhOpenConversation(house: String?, inbox: List<FhConversation>, friends: List<FhFriend> = emptyList()): FhConversation? {
    if (!FhPaths.isHouseKey(house)) return null
    inbox.firstOrNull { it.house == house }?.let { return it }
    val f = friends.firstOrNull { it.house == house } ?: return null
    return FhConversation(house = f.house, name = f.name, friendId = f.id)
}

/** A list a friend offered this house and nobody here has answered yet. */
fun fhIsOffer(s: FhSharedList): Boolean = s.origin == "them" && s.state == "invited"

/** The shared lists as the dashboard splits them: the offers to this house
 *  first, then the ones shared now (ours waiting for them among these);
 *  only those whose id a URL may carry. */
fun fhSharedSplit(lists: List<FhSharedList>): Pair<List<FhSharedList>, List<FhSharedList>> =
    lists.filter { FhPaths.isSpaceId(it.id) }.partition(::fhIsOffer)

/** The name a shared list goes by here: its list's name, else its title. */
fun fhListName(s: FhSharedList): String =
    s.listName?.takeIf { it.isNotEmpty() } ?: s.title?.takeIf { it.isNotEmpty() } ?: "this list"

/** How an accept, decline or stop sharing went: a toast to show, or a
 *  problem to show by the list ([rename]: the name is taken here, so ask
 *  for another, as the dashboard does on a 400). */
sealed interface FhDone {
    data class Ok(val toast: String) : FhDone
    data class Problem(val text: String, val rename: Boolean = false) : FhDone
}

private suspend fun fhDo(action: String, block: suspend () -> String): FhDone = try {
    FhDone.Ok(block())
} catch (e: CancellationException) {
    throw e
} catch (e: Exception) {
    FhDone.Problem(fhProblem(action, e), rename = e is ApiException && e.status == 400)
}

/** Accept [s] (a list offered to this house), as [name] when given. */
suspend fun fhAcceptList(api: FriendHousesApi, s: FhSharedList, name: String? = null): FhDone =
    fhDo("accept") {
        val added = api.acceptList(s.id, name)
        FhWords.added(
            added?.takeIf { it.isNotBlank() } ?: name?.trim()?.takeIf { it.isNotEmpty() }
                ?: s.title?.takeIf { it.isNotBlank() } ?: "a new list",
        )
    }

/**
 * A list offered to this house, as its row holds it: Accept under its own
 * title, or (by choice, or once the server says that name is taken here)
 * under the name typed; Decline. [busy] while either is in flight.
 */
class FhOffer(title: String?) {
    var naming by mutableStateOf(false)
    var name by mutableStateOf(title.orEmpty())
    var problem by mutableStateOf<String?>(null)
    var busy by mutableStateOf(false)
        private set

    /** Accept may be pressed: not in flight, and a name when naming. */
    val canAccept: Boolean get() = !busy && !(naming && name.isBlank())

    suspend fun accept(send: suspend (name: String?) -> FhDone) {
        if (!canAccept) return
        busy = true
        problem = null
        try {
            val done = send(if (naming) name.trim() else null)
            if (done is FhDone.Problem) {
                problem = done.text
                if (done.rename) naming = true
            }
        } finally {
            busy = false
        }
    }

    suspend fun decline(send: suspend () -> FhDone) {
        if (busy) return
        busy = true
        problem = null
        try {
            (send() as? FhDone.Problem)?.let { problem = it.text }
        } finally {
            busy = false
        }
    }
}

suspend fun fhDeclineList(api: FriendHousesApi, s: FhSharedList): FhDone =
    fhDo("decline") { api.declineList(s.id); FhWords.DECLINED }

suspend fun fhStopSharing(api: FriendHousesApi, s: FhSharedList): FhDone =
    fhDo("stop sharing") { api.stopSharing(s.id); FhWords.STOPPED }

// ---------------------------------------------------------------------------
// Quiet hours (the dashboard's fhClock, fhHeldText and fhQuietNote).
// ---------------------------------------------------------------------------

private val FH_CLOCK = DateTimeFormatter.ofPattern("h:mm a", Locale.US)
private val FH_HHMM = Regex("""(\d{1,2}):(\d{2})""")

/** "7:00am", the app's clock words (Home's and the calendar's). */
private fun clockWords(t: LocalTime): String = t.format(FH_CLOCK).lowercase(Locale.US).replace(" ", "")

/** "07:00" (the house's clock) as the app shows a time: "7:00am"; null
 *  when it isn't one. */
fun fhClock(hhmm: String?): String? {
    val (h, m) = FH_HHMM.matchEntire(hhmm.orEmpty())?.destructured ?: return null
    return runCatching { clockWords(LocalTime.of(h.toInt(), m.toInt())) }.getOrNull()
}

/** A server time on the phone's clock: "7:00am"; null when it can't be shown. */
fun fhClockOf(iso: String?, zone: ZoneId = ZoneId.systemDefault()): String? =
    parseInstant(fhIso(iso))?.let { clockWords(it.atZone(zone).toLocalTime()) }

/** What an incoming message says about quiet hours: "held for quiet hours"
 *  or "held for quiet hours · read out at 7:00am"; null otherwise. */
fun fhHeldText(m: FhMessage, zone: ZoneId = ZoneId.systemDefault()): String? {
    if (!m.incoming || m.held != "quiet_hours") return null
    val at = fhClockOf(m.readOutAt, zone)
    return if (at != null) "held for quiet hours · read out at $at" else "held for quiet hours"
}

/**
 * Why a friend's messages aren't being said right now, for under a friend's
 * "when a message comes from them" choice: only a choice that speaks in
 * the rooms (`read`, `sender`), and only while quiet hours hold. Null
 * otherwise. The phone shows no friend card for now (the friends list is in
 * the admin zone), so nothing draws it yet.
 */
fun fhQuietNote(quiet: FhQuietHours?, alert: String?): String? {
    if (quiet == null || (alert != "read" && alert != "sender")) return null
    val until = fhClock(quiet.until) ?: return null
    val said = if (alert == "read") "read out" else "announced"
    return when {
        quiet.rooms == "some" && quiet.readLater ->
            "Quiet hours in some rooms until $until: new messages will be $said there at $until."
        quiet.rooms == "some" ->
            "Quiet hours in some rooms until $until: new messages aren’t $said there, and won’t be later."
        quiet.readLater -> "Quiet hours until $until: new messages will be $said at $until."
        else -> "Quiet hours until $until: new messages wait in the inbox and won’t be $said later."
    }
}

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
