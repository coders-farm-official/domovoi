package com.domovoi.app.friendhouses

import com.domovoi.app.alerts.SYNC_BUDGET_MS
import com.domovoi.app.ui.screens.home.plural
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.withTimeoutOrNull

// ---------------------------------------------------------------------------
// Friend Houses notifications on the phone, with Android at arm's length
// (the shape of alerts/IncidentEngine.kt): the decision, and the background
// sync's tick that feeds it. FriendHouses.kt wires it to the notification
// shade; the JVM tests to a fake sink and a MockWebServer.
//
// Each friend's `alert` level (set by an admin on the dashboard) already
// governs what the rooms do with a new message; the phone follows it
// (owner decision 2: least intrusive by default):
//
//   inbox (the default)  the in-app unread badge only, no notification
//   sender               "A message from <name>", with no text
//   read                 the name and the message, cut to about 200
//                        characters, control characters taken out
//
// An alert level this build doesn't know notifies nothing (never louder).
//
// No push, nothing hosted: the existing background sync's tick
// (alerts/TimerSync.kt TimerSyncReceiver, about every 15 minutes, on Wi-Fi
// or Ethernet only) asks `GET /api/friend-houses` for `unread`, and only
// when that changed, the inbox, then the new messages of the conversations
// whose friend asked for more than the inbox.
//
// Nothing here logs a name, a title or a message: only counts and the
// tick's outcome ([FhTick]).
// ---------------------------------------------------------------------------

/** Notification text is cut to about this many characters. */
const val FH_NOTE_TEXT_MAX = 200

/** A name in a notification's title, likewise. */
const val FH_NOTE_NAME_MAX = 60

/** One conversation's notification. [house] is its 64-hex house key (the
 *  notification's tag); [newestId] the newest message it covers. */
data class FhNote(
    val house: String,
    val title: String,
    /** Null for the `sender` level: the name only. */
    val text: String?,
    val count: Int,
    val newestId: Long,
)

object FhNotify {
    /**
     * What a notification never carries: control characters (C0, DEL, C1),
     * every format character (Unicode Cf: the bidi marks, embeddings,
     * overrides and isolates, zero-width spaces and joiners U+200B-200D,
     * U+2060-2064, the BOM U+FEFF, the tag characters U+E0001-E007F, the
     * soft hyphen), and the line and paragraph separators U+2028/2029.
     * The server strips the bidi and control ones already.
     */
    internal fun isUnsafe(c: Int): Boolean {
        val type = Character.getType(c)
        return type == Character.CONTROL.toInt() || type == Character.FORMAT.toInt() ||
            type == Character.LINE_SEPARATOR.toInt() || type == Character.PARAGRAPH_SEPARATOR.toInt() ||
            c < 0x20 || c in 0x7F..0x9F || c in 0x200B..0x200F || c in 0x202A..0x202E ||
            c in 0x2060..0x206F || c == 0xFEFF || c in 0xE0000..0xE007F || c == 0x061C
    }

    /** Message text for a notification: control and format characters out,
     *  line breaks kept (a tab or a carriage return becomes a space). */
    fun cleanText(raw: String): String {
        val out = StringBuilder(raw.length)
        var i = 0
        while (i < raw.length) {
            val c = raw.codePointAt(i)
            when {
                c == '\n'.code -> out.append('\n')
                c == '\t'.code || c == '\r'.code -> out.append(' ')
                isUnsafe(c) -> Unit
                else -> out.appendCodePoint(c)
            }
            i += Character.charCount(c)
        }
        return out.toString().trim()
    }

    /** A name for a title: one line, control characters out, cut short. */
    fun cleanName(raw: String?): String {
        val one = cleanText(raw.orEmpty()).replace('\n', ' ').replace(Regex(" {2,}"), " ").trim()
        return cut(one, FH_NOTE_NAME_MAX).ifEmpty { "a friend house" }
    }

    /** [text] cut to [max] characters (never through a surrogate pair),
     *  with an ellipsis when it was longer. */
    fun cut(text: String, max: Int = FH_NOTE_TEXT_MAX): String {
        if (text.codePointCount(0, text.length) <= max) return text
        val end = text.offsetByCodePoints(0, max)
        return text.substring(0, end).trimEnd() + "…"
    }

    /**
     * The notification for [count] new messages from [name], the newest
     * [body], under the friend's [alert] level; null for none (`inbox`, or
     * a level this build doesn't know).
     */
    fun noteFor(alert: String?, house: String, name: String?, body: String?, count: Int, newestId: Long): FhNote? {
        if (count <= 0) return null
        val who = cleanName(name)
        return when (alert) {
            "sender" -> FhNote(house, "A message from $who", null, count, newestId)
            "read" -> FhNote(house, who, cut(cleanText(body.orEmpty())).ifEmpty { null }, count, newestId)
            else -> null
        }
    }
}

/** Where the notifications go. The Android one is [FriendHouseNotifier]. */
interface FhSink {
    /** Notifications are on for this app and the friend houses' channel. */
    fun canPost(): Boolean

    fun post(serverKey: String, note: FhNote)

    /** Take back [house]'s notification: its messages were read. */
    fun withdraw(serverKey: String, house: String)
}

/** What the tick remembers per server between runs: the unread total it
 *  last saw, and each conversation's newest message id it has dealt with. */
data class FhSeen(val unread: Int = 0, val lastIds: Map<String, Long> = emptyMap())

interface FhSeenStore {
    suspend fun read(serverKey: String): FhSeen?
    suspend fun write(serverKey: String, seen: FhSeen)
}

/** What one background tick did (logged; pinned by the tests). */
enum class FhTick {
    /** Posted at least one notification. */
    NOTIFIED,

    /** Asked, and nothing new needed a notification. */
    QUIET,

    /** The first look at this server: what was there is the baseline. */
    BASELINE,

    /** Friend Houses is off or not offered here: nothing more asked. */
    HIDDEN,

    /** No answer. */
    UNREACHABLE,

    /** The phone is a room: no household request, no notification. */
    ROOM_MODE,

    NOTIFICATIONS_OFF,
    OFF_LAN,
    NO_SERVER,
    TIMED_OUT,
}

class FriendHouseAlerts(
    private val api: FriendHousesApi,
    private val store: FhSeenStore,
    private val sink: FhSink,
    private val serverUrl: () -> String,
    private val serverKey: (String) -> String,
    /** Every status answer, so the in-app gate and badge learn it too. */
    private val onStatus: (server: String, FhAvailability) -> Unit = { _, _ -> },
    /** The phone is a room (roommode/): nothing asked, nothing posted. */
    private val roomMode: () -> Boolean = { false },
    private val budgetMs: Long = SYNC_BUDGET_MS,
    /** Never handed a name, a title or a message. */
    private val log: (String) -> Unit = {},
) {
    /** The background sync's tick. Never throws. */
    suspend fun backgroundTick(onLan: Boolean): FhTick {
        val result = try {
            val server = serverUrl()
            when {
                roomMode() -> FhTick.ROOM_MODE
                server.isBlank() -> FhTick.NO_SERVER
                !sink.canPost() -> FhTick.NOTIFICATIONS_OFF
                !onLan -> FhTick.OFF_LAN
                else -> withTimeoutOrNull(budgetMs) { check(server) } ?: FhTick.TIMED_OUT
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // The class only: a message could quote what the server sent.
            log("friend houses tick failed: ${e.javaClass.simpleName}")
            FhTick.UNREACHABLE
        }
        log("friend houses tick: ${result.toString().lowercase()}")
        return result
    }

    private suspend fun check(server: String): FhTick {
        val availability = api.status()
        onStatus(server, availability)
        val status = when (availability) {
            is FhAvailability.Shown -> availability.status
            FhAvailability.Hidden -> return FhTick.HIDDEN
            FhAvailability.Unknown -> return FhTick.UNREACHABLE
        }
        val key = serverKey(server)
        val seen = store.read(key)
        val unread = status.unread.coerceAtLeast(0)

        if (seen == null) {
            // First look: what is already there was there before this phone
            // was asked; the badge shows it, a notification would be old news.
            val ids = if (unread > 0) api.inbox().newestFirst().associate { it.house to it.lastId } else emptyMap()
            store.write(key, FhSeen(unread, ids))
            return FhTick.BASELINE
        }
        if (unread == seen.unread) return FhTick.QUIET
        if (unread == 0) {
            // Everything was read (here or elsewhere): take back what is shown.
            seen.lastIds.keys.forEach { sink.withdraw(key, it) }
            store.write(key, seen.copy(unread = 0))
            return FhTick.QUIET
        }

        val inbox = api.inbox().newestFirst()
        val fresh = inbox.filter { it.unread > 0 && it.lastId > (seen.lastIds[it.house] ?: 0L) && it.friendId != null }
        val alerts: Map<String, String> =
            if (fresh.isEmpty()) emptyMap() else api.friends().associate { it.id to it.alert }
        var posted = 0
        for (c in inbox) {
            if (c.unread == 0) sink.withdraw(key, c.house)
        }
        for (c in fresh) {
            val alert = alerts[c.friendId]
            if (alert != "sender" && alert != "read") continue
            val since = seen.lastIds[c.house] ?: 0L
            val news = api.conversation(c.house, limit = FhPaths.PAGE)
                .filter { it.incoming && it.state == "unread" && it.id > since }
            val newest = news.maxByOrNull { it.id } ?: continue
            val note = FhNotify.noteFor(alert, c.house, c.name, newest.body, news.size, newest.id) ?: continue
            sink.post(key, note)
            posted++
        }
        store.write(key, FhSeen(unread, seen.lastIds + inbox.associate { it.house to it.lastId }))
        log("friend houses: ${plural(fresh.size, "conversation")} with new messages, $posted notified")
        return if (posted > 0) FhTick.NOTIFIED else FhTick.QUIET
    }
}
