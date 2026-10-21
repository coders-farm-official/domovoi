package com.domovoi.app.ui.screens.chat

import androidx.compose.runtime.Composable

/**
 * Where a chat runs (assistant plan, part 6: One Chat). Chat is one tab with
 * one history list and one conversation view; every chat runs in one of two
 * places, and says which with a chip:
 *
 *  - [Phone]: the model on this phone (the on-device assistant). Private to
 *    this phone, works with no server and no internet.
 *  - [Server]: the home server's chat (`/api/chat`). The household's: every
 *    paired device sees the same threads.
 *
 * Each place answers from its own world and they never blend (owner,
 * 2026-10-21): a phone chat never reads the server's conversations or data,
 * a home-server chat never reads the phone's, and nothing here moves a
 * message from one place to the other.
 */
enum class ChatPlaceKind(val key: String) {
    Phone("phone"),
    Server("server"),
    ;

    companion object {
        /** The kind Prefs keeps as [key]; null for none (or one this build
         *  doesn't know). */
        fun of(key: String?): ChatPlaceKind? = entries.firstOrNull { it.key == key }
    }
}

/** The chips, lowercase like the rest of the chrome (docs/design/README.md). */
const val CHIP_PHONE = "on this phone"
const val CHIP_SERVER = "on home server"

/** The server's chip until this phone has used a home-server chat once:
 *  those chats are the household's, and the chip says so first. */
const val CHIP_SERVER_SHARED = "on home server · shared with the household"

/** Chat away from home: the server's chats can't be read (the phone keeps
 *  no copy of them), so the list says when they come back. */
const val AWAY_LINE = "home server chats show when you're home"

/** ...and when the person chose this phone's media with the server in
 *  reach: the server's chats come back with the workspace. */
const val LOCAL_CHOSEN_LINE = "home server chats show when you switch back to your server"

/** The composer's placeholder, in every place. */
const val COMPOSER_HINT = "Message Domovoi…"

/** What a chat with no title yet is called, in every place. */
const val NEW_CHAT = "new chat"

/** The chip for [kind]; the server's says "shared with the household" until
 *  [serverShareSeen]. */
fun placeChip(kind: ChatPlaceKind, serverShareSeen: Boolean): String = when (kind) {
    ChatPlaceKind.Phone -> CHIP_PHONE
    ChatPlaceKind.Server -> if (serverShareSeen) CHIP_SERVER else CHIP_SERVER_SHARED
}

/**
 * One row of Chat's history list, from either place: [id] is that place's
 * own (a server thread id, or a phone conversation id), never mixed.
 * [snippet]: the last words, where the place keeps them (the server does).
 */
data class ChatRow(
    val place: ChatPlaceKind,
    val id: String,
    val title: String,
    val snippet: String?,
    /** Last activity, epoch ms (0 when unknown). */
    val updatedMs: Long,
    val messages: Int,
    /** In the "archived" view, not the main list. */
    val archived: Boolean = false,
    /** Pinned on this phone ([pinKey]); the list sets it from Prefs. */
    val pinned: Boolean = false,
    /** A search hit: the words around the match (null: the title matched,
     *  or this is no search). */
    val match: String? = null,
)

/** Both places' chats in one list, the most recently used first; on a tie
 *  the order the lists came in holds (stable sort). */
fun mergeChats(lists: List<List<ChatRow>>): List<ChatRow> =
    lists.flatten().sortedByDescending { it.updatedMs }

/**
 * The history list's order (assistant plan part 7): the chats pinned on
 * this phone first, then the rest, each by last use, newest first.
 * Archived chats are left out (they have their own view).
 */
fun orderChats(rows: List<ChatRow>): List<ChatRow> =
    rows.filter { !it.archived }.sortedWith(compareByDescending<ChatRow> { it.pinned }.thenByDescending { it.updatedMs })

/** The "archived" view: the archived chats, newest first. */
fun archivedChats(rows: List<ChatRow>): List<ChatRow> =
    rows.filter { it.archived }.sortedByDescending { it.updatedMs }

/**
 * How Prefs keys a pin: a phone chat by its id; a home-server chat by its
 * server ([server], the normalised address) and thread id, so pins on two
 * servers never mix. Pins stay on this phone (owner, 2026-10-20): one
 * person's pins don't move the household's list.
 */
fun pinKey(row: ChatRow, server: String): String = when (row.place) {
    ChatPlaceKind.Phone -> "phone:${row.id}"
    ChatPlaceKind.Server -> "server:$server#${row.id}"
}

/** [rows] with [ChatRow.pinned] set from [pins] ([pinKey]). */
fun withPins(rows: List<ChatRow>, pins: Set<String>, server: String): List<ChatRow> =
    rows.map { it.copy(pinned = pinKey(it, server) in pins) }

/** What a chat's long-press menu offers, in order. */
internal fun rowActions(row: ChatRow): List<String> = listOf(
    ACTION_RENAME,
    if (row.pinned) ACTION_UNPIN else ACTION_PIN,
    if (row.archived) ACTION_UNARCHIVE else ACTION_ARCHIVE,
    ACTION_SHARE,
    ACTION_DELETE,
)

internal const val ACTION_RENAME = "rename"
internal const val ACTION_PIN = "pin"
internal const val ACTION_UNPIN = "unpin"
internal const val ACTION_ARCHIVE = "archive"
internal const val ACTION_UNARCHIVE = "move back to chats"
internal const val ACTION_SHARE = "share"
internal const val ACTION_DELETE = "delete"

/** The quiet divider where the model's view of a chat begins (assistant
 *  plan part 7: the honest window note). */
const val WINDOW_NOTE = "earlier messages are beyond what Domovoi can keep in mind"

/** The confirmation for deleting a home-server chat: it is the household's. */
const val SERVER_DELETE_NOTE = "Deletes it for everyone in the house."

/** What the list says when a search couldn't run in some place. */
const val SEARCH_FAILED = "couldn't search"

/** Chat's search field. */
const val SEARCH_HINT = "search chats"

/** What decides which places Chat offers, apart from Android. */
data class PlaceFacts(
    /** The local shell: no home server in reach (or none paired, or the
     *  person chose this phone's media). */
    val away: Boolean,
    /** The server allows chat ([com.domovoi.app.net.Capabilities.aiEnabled]: its AI
     *  features aren't off). */
    val serverChatOn: Boolean,
    /** This install has the on-device engine (64-bit phone and APK). */
    val phoneEngine: Boolean,
    /** This phone has the memory for the model (DeviceTier not Unsupported). */
    val phoneCapable: Boolean,
    /** A shared screen (the kitchen tablet): nobody's own chats. */
    val shared: Boolean,
)

/** The places whose chats are listed: the server's only when it is in
 *  reach and allows chat; the phone's only where the install has the engine
 *  and the screen isn't shared. */
fun listedPlaces(f: PlaceFacts): List<ChatPlaceKind> = listOfNotNull(
    ChatPlaceKind.Server.takeIf { !f.away && f.serverChatOn },
    ChatPlaceKind.Phone.takeIf { f.phoneEngine && !f.shared },
)

/** The places a new chat can start in right now (the picker's choices):
 *  the listed ones, less the phone when it can't run the model. */
fun startablePlaces(f: PlaceFacts): List<ChatPlaceKind> =
    listedPlaces(f).filter { it != ChatPlaceKind.Phone || f.phoneCapable }

/**
 * Where a new chat starts, of [choices] (the places that work right now):
 * the one this phone used last ([lastUsed]) when it is still a choice;
 * otherwise, the first time, the home server when it has a chat model
 * ([serverHasModel], plan Q1, owner-approved), else this phone. Away from
 * home the phone is the only choice, so it is always the phone. Null: no
 * place works.
 */
fun defaultPlace(choices: List<ChatPlaceKind>, lastUsed: ChatPlaceKind?, serverHasModel: Boolean?): ChatPlaceKind? = when {
    choices.isEmpty() -> null
    choices.size == 1 -> choices.single()
    lastUsed != null && lastUsed in choices -> lastUsed
    ChatPlaceKind.Server in choices && serverHasModel == true -> ChatPlaceKind.Server
    ChatPlaceKind.Phone in choices -> ChatPlaceKind.Phone
    else -> choices.first()
}

/**
 * Whether the home server can answer a chat now: its configured chat model
 * is one Ollama has installed (`GET /api/chat/models`). Null when it isn't
 * known yet. A tag-less name matches its `:latest`, as Ollama names it.
 */
internal fun serverHasChatModel(info: ChatModelsInfo?): Boolean? {
    info ?: return null
    val model = info.default_model?.trim()?.ifEmpty { null } ?: return false
    fun bare(name: String) = name.trim().removeSuffix(":latest")
    return info.installed.any { bare(it) == bare(model) }
}

/**
 * What the conversation view hands a place: the shared header's chip, the
 * way back to the list, and, for a chat with nothing said yet, the picker
 * that moves it to the other place.
 */
class ChatFrame(
    val chip: String,
    val onBack: () -> Unit,
    /** The place picker; null for a chat that already has messages (its
     *  place is fixed). A place draws it only until something is said. */
    val picker: (@Composable () -> Unit)?,
    /** A message went out in this place: it becomes the last used. */
    val onSent: () -> Unit,
    /** A new chat started from the list's "Message Domovoi…" box: this is
     *  sent as its first message as soon as the chat opens. */
    val firstMessage: String? = null,
)

/**
 * One place a chat can run in. Chat's history list and conversation view
 * reach a place only through this; what differs (the server's voice view
 * and photos; the phone's voice loop, its model setup and its report store)
 * stays inside the place.
 */
interface ChatPlace {
    val kind: ChatPlaceKind

    /** This place's chats, newest first (empty while they load). */
    val chats: List<ChatRow>

    /** Whether a reply can be written here at all now: the server's chat
     *  model is installed / the phone's model is on the phone; null while
     *  it isn't known. Decides the first default place ([defaultPlace]). */
    val ready: Boolean?

    /** The list has been read once (Chat opens a new chat at once only
     *  when it knows there are none). */
    val loaded: Boolean get() = true

    /** Reads the list again (a conversation closed, or a delete). */
    fun refresh()

    /** What the delete confirmation says for [row]. */
    fun deleteText(row: ChatRow): String

    /** Deletes [row], after the list's confirmation. */
    fun delete(row: ChatRow)

    /** Names [row] [title]: the person's name, which a generated title
     *  never replaces. */
    fun rename(row: ChatRow, title: String) {}

    /** Moves [row] to the "archived" view, or back. */
    fun setArchived(row: ChatRow, archived: Boolean) {}

    /** This place's chats whose title or messages contain [query], archived
     *  ones too, each with the words around the match. Throws when the
     *  search couldn't run (the list says so, rather than "no chats match"). */
    suspend fun search(query: String): List<ChatRow> = emptyList()

    /** [row] as plain text for the share sheet; null when it can't be read. */
    suspend fun shareText(row: ChatRow): String? = null

    /** The conversation: [row] null is a new chat in this place. */
    @Composable
    fun Conversation(row: ChatRow?, frame: ChatFrame)
}
