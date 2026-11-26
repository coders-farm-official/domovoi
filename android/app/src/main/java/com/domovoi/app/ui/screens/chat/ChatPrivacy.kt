package com.domovoi.app.ui.screens.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Group
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.foundation.selection.toggleable
import androidx.compose.ui.unit.dp
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

// ---------------------------------------------------------------------------
// Private chats (1.5.0, design-notes PRIVATE-CHATS.md; docs/SECURITY_PRIVACY.md
// "Private chats"). A home-server chat is private to the phone or browser
// that started it unless it is shared with the household. The server decides
// who sees what, by this phone's device key (net/DeviceKey.kt), and says so
// in every thread's access fields; the app shows what those say and hides
// what they deny. The words are §2.5's, as written (the dashboard's chat.jsx
// uses the same ones).
// ---------------------------------------------------------------------------

/** A new chat's chip and header (§2.5). */
const val CHAT_PRIVATE_LINE = "Private · only this device and the house's admins can see it"

/** The switch, and its helper line. */
const val CHAT_SHARE_SWITCH = "Share with household"
const val CHAT_SHARE_HELP = "Everyone's phones and browsers in the house can read and add to it."

/** Turning sharing off asks first. */
const val CHAT_UNSHARE_ASK =
    "Stop sharing? Other phones and browsers won't see this chat any more, including anything they wrote in it."

/** A shared chat's chip. */
const val CHAT_SHARED_LINE = "Shared with the household"

/** On a shared screen: no switch, and this chip. */
const val CHAT_SHARED_SCREEN_LINE = "Shared · chats on this screen are shared with the household"

/** Delete, a private chat ("Delete "…"? This can't be undone."); a shared
 *  one the phone owns says [SERVER_DELETE_NOTE] too; another device's
 *  shared chat has no delete at all. */
internal fun deleteChatText(title: String, shared: Boolean?): String =
    if (shared == false) "Delete \"$title\"? This can't be undone."
    else "Delete \"$title\"? $SERVER_DELETE_NOTE This can't be undone."

/** An admin reading another device's private chat. */
internal fun adminBanner(deviceName: String?): String =
    "You're reading ${deviceName?.trim()?.takeIf { it.isNotEmpty() } ?: "another device"}'s private chat as an admin. " +
        "You can share it or delete it, not write in it."

/** The admin's list filter. */
const val CHAT_SCOPE_MINE = "My chats"
const val CHAT_SCOPE_ALL = "All chats in the house"

/** Phones only, once: the fully private place, where this build has one. */
const val CHAT_TRULY_PRIVATE_NOTE = "Want it truly private? Chat with Domovoi on this phone: it never leaves the phone."

/** Save to Documents from a private chat asks first. */
const val CHAT_SAVE_PRIVATE_ASK = "Saves a copy to Documents, where the household can see it."

/** An open chat that the server stopped showing this phone (a 404 after an
 *  unshare, or a delete): said, and the chat closes. */
const val CHAT_GONE_LINE = "This chat isn't shared with this device any more."

/** The share switch's toasts (the dashboard's). */
internal const val CHAT_SHARED_TOAST = "shared with the household"
internal const val CHAT_UNSHARED_TOAST = "private to this device again"

// ── What the thread JSON says ────────────────────────────────────────────

/** The server sent the access fields: a server from before 1.5.0 didn't, and
 *  then nothing about sharing is said (every chat is the household's). */
internal val ThreadRow.knowsSharing: Boolean get() = shared != null

/** A field the server didn't send denies nothing (an older server). */
internal val ThreadRow.writable: Boolean get() = can_write != false

/** This thread with [o]'s access fields (the server's word, newer). */
internal fun ThreadRow.withAccessOf(o: ThreadRow): ThreadRow = copy(
    shared = o.shared, mine = o.mine, can_write = o.can_write, can_share = o.can_share,
    can_delete = o.can_delete, admin_view = o.admin_view, owner_name = o.owner_name,
)
internal val ThreadRow.deletable: Boolean get() = can_delete != false

/** The switch shows only to the owner or an admin, never on a shared screen. */
internal fun showsShareSwitch(thread: ThreadRow, sharedScreen: Boolean): Boolean =
    !sharedScreen && thread.can_share == true

/** The chip for an open chat that [knowsSharing]. */
internal fun privacyLine(shared: Boolean, sharedScreen: Boolean): String = when {
    sharedScreen -> CHAT_SHARED_SCREEN_LINE
    shared -> CHAT_SHARED_LINE
    else -> CHAT_PRIVATE_LINE
}

/** A new chat's chip before it exists on the server: private by default when
 *  this phone holds a key ([shareNew] when the switch was turned on first),
 *  always shared on a shared screen, and null without a key (the place chip
 *  then says the old "shared with the household"). */
internal fun newChatLine(hasKey: Boolean, sharedScreen: Boolean, shareNew: Boolean): String? = when {
    sharedScreen -> CHAT_SHARED_SCREEN_LINE
    !hasKey -> null
    shareNew -> CHAT_SHARED_LINE
    else -> CHAT_PRIVATE_LINE
}

/**
 * `POST /api/chat/threads`'s body. With a key, `shared` says what the chip
 * says, so a private chat the server can't keep private fails loudly (409)
 * instead of being made shared by surprise; on a shared screen it is shared;
 * without a key the server's default (shared, as the chip says) stands.
 */
internal fun createThreadBody(hasKey: Boolean, sharedScreen: Boolean, shareNew: Boolean): JsonObject = buildJsonObject {
    when {
        sharedScreen -> put("shared", true)
        hasKey -> put("shared", shareNew)
    }
}

/** The 409s a create (or an unshare) answers with a `code`. */
enum class ChatRefusal { SharedScreen, KeyMissing }

/** [e]'s `code`, when it is one of [ChatRefusal]. */
internal fun chatRefusal(e: Throwable): ChatRefusal? {
    val api = e as? ApiException ?: return null
    if (api.status != 409) return null
    return when (refusalCode(api.body)) {
        "shared_screen" -> ChatRefusal.SharedScreen
        "device_key_missing" -> ChatRefusal.KeyMissing
        else -> null
    }
}

internal fun refusalCode(body: String?): String? = runCatching {
    ((DomovoiJson.parseToJsonElement(body.orEmpty()) as? JsonObject)?.get("code") as? JsonPrimitive)?.contentOrNull
}.getOrNull()

/** The thread is not there for this phone: a 404 whose body is the
 *  server's "thread not found" (byte-identical for a missing chat and one
 *  this phone can't see). A child route's own 404 ("no such lookup card")
 *  is not this. */
internal fun isThreadGone(status: Int, body: String?): Boolean =
    status == 404 && runCatching {
        ((DomovoiJson.parseToJsonElement(body.orEmpty()) as? JsonObject)?.get("detail") as? JsonPrimitive)?.contentOrNull
    }.getOrNull() == "thread not found"

internal fun isThreadGone(e: Throwable): Boolean =
    e is ChatGoneException || (e is ApiException && isThreadGone(e.status, e.body))

/** A streamed send (or retry, pick, lookup answer) to a thread this phone
 *  can't see any more. */
internal class ChatGoneException : java.io.IOException(CHAT_GONE_LINE)

/** The thread list's path: the chats or the archived ones, and an admin's
 *  "All chats in the house" (`scope=all`). */
internal fun threadListPath(archived: Boolean, all: Boolean): String {
    val q = listOfNotNull("archived=true".takeIf { archived }, "scope=all".takeIf { all })
    return "/api/chat/threads" + if (q.isEmpty()) "" else "?" + q.joinToString("&")
}

/**
 * Whether to offer the admin's "My chats" / "All chats in the house" filter.
 * The phone can't sign in as an admin on this branch (phone admin lives on
 * its own branch), so the only evidence is the server's: a thread it marks
 * `admin_view`, or an `owner_name` (sent to admins only). The admin sign-in
 * work replaces this guess with what it knows.
 */
internal fun offersAdminScope(threads: List<ThreadRow>, showingAll: Boolean): Boolean =
    showingAll || threads.any { it.admin_view || !it.owner_name.isNullOrBlank() }

// ── Composables ──────────────────────────────────────────────────────────

/** The chip under a conversation's title, and the switch when it may show. */
@Composable
internal fun ChatPrivacyBar(
    line: String,
    shared: Boolean,
    showSwitch: Boolean,
    onShare: (Boolean) -> Unit,
) {
    Column(Modifier.fillMaxWidth().padding(bottom = 4.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(
                if (shared) Icons.Outlined.Group else Icons.Outlined.Lock, contentDescription = null,
                tint = Domovoi.colors.fgMuted, modifier = Modifier.size(14.dp),
            )
            Text(line, style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted)
        }
        if (showSwitch) {
            Row(
                Modifier.fillMaxWidth()
                    .toggleable(value = shared, role = Role.Switch, onValueChange = onShare)
                    .semantics { contentDescription = CHAT_SHARE_SWITCH; stateDescription = if (shared) "on" else "off" },
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(CHAT_SHARE_SWITCH, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                    Text(CHAT_SHARE_HELP, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
                }
                Spacer(Modifier.width(8.dp))
                Switch(checked = shared, onCheckedChange = null)
            }
        }
    }
}

/** A quiet note box with a close button (the admin banner, the first-use note). */
@Composable
internal fun ChatNote(text: String, onClose: (() -> Unit)? = null) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted, modifier = Modifier.weight(1f))
        onClose?.let {
            IconButton(onClick = it) {
                Icon(Icons.Outlined.Close, contentDescription = "close", tint = Domovoi.colors.fgMuted)
            }
        }
    }
}
