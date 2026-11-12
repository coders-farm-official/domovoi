package com.domovoi.app.ui.screens.lists

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.failureText
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

// ---------------------------------------------------------------------------
// Model — mirrors web/backend/schemas.py HouseholdList / HouseholdListItem /
// HouseholdListDetail / ListItemAdded / ListsCleared (V048,
// web/backend/api/lists.py). The fields every answer carries have no
// default, so a change to the server's shape fails the decoder tests rather
// than drawing a blank row.
// ---------------------------------------------------------------------------

@Serializable
internal data class HouseholdList(
    val id: Long,
    val name: String,
    val position: Int = 0,
    val open_count: Int = 0,
    val done_count: Int = 0,
    val created_at: String? = null,
    val updated_at: String? = null,
)

@Serializable
internal data class HouseholdListItem(
    val id: Long,
    val list_id: Long,
    val text: String,
    val done: Boolean = false,
    val done_at: String? = null,
    val position: Int = 0,
    val added_by_person_id: Long? = null,
    val added_by_name: String? = null,
    val added_by_room: String? = null,
    /** "voice" or "screen". */
    val added_via: String = "screen",
    val created_at: String? = null,
    val updated_at: String? = null,
)

@Serializable
internal data class HouseholdListDetail(
    val list: HouseholdList,
    val items: List<HouseholdListItem> = emptyList(),
)

/** `POST /api/lists/{id}/items`: the item and what happened to it. */
@Serializable
internal data class ListItemAdded(
    val item: HouseholdListItem,
    /** "added", "reopened" or "already". */
    val outcome: String = "added",
)

@Serializable
internal data class ListsCleared(val removed: Int = 0)

/** The one event the lists screen listens for (web/backend/realtime.py
 *  `lists_changed` → `lists`). Its payload is a digest: only "something
 *  changed" is read from it. */
internal const val LISTS_CHANGED = "lists.changed"
internal val LISTS_EVENTS = setOf(LISTS_CHANGED)

// ---------------------------------------------------------------------------
// Wording — web/static/lists.jsx.
// ---------------------------------------------------------------------------

internal fun listsThings(n: Int): String = if (n == 1) "1 thing" else "$n things"

/** The page header's sub-line: "3 lists · 5 things to go". */
internal fun listsHeaderSub(lists: List<HouseholdList>): String {
    if (lists.isEmpty()) return "to-do, shopping and your own"
    val total = lists.sumOf { it.open_count }
    val n = if (lists.size == 1) "1 list" else "${lists.size} lists"
    return "$n · ${listsThings(total)} to go"
}

/** The open list's sub-line: "2 things to go · 1 crossed off". */
internal fun listCardSub(open: Int, done: Int): String =
    "${listsThings(open)} to go" + if (done > 0) " · $done crossed off" else ""

internal fun addPlaceholder(listName: String): String = "add to ${listName.lowercase()}"

internal fun emptyListHint(listName: String): String =
    "type above, or say \"add milk to the ${listName.lowercase()} list\""

/** Who added an item, when the server knows: "Alex · kitchen", "by voice ·
 *  garage", "Alex"; null for an item added on a screen. */
internal fun whoLine(item: HouseholdListItem): String? {
    if (item.added_via != "voice") return null
    val who = item.added_by_name?.takeIf { it.isNotBlank() } ?: "by voice"
    val room = item.added_by_room?.takeIf { it.isNotBlank() }
    return if (room != null) "$who · $room" else who
}

/** The short note an add answers with, or null for a plain "added". */
internal fun addNote(added: ListItemAdded): String? = when (added.outcome) {
    "already" -> "\"${added.item.text}\" is already on the list"
    "reopened" -> "\"${added.item.text}\" is back on the list"
    else -> null
}

internal fun deleteListBody(itemCount: Int): String =
    (if (itemCount > 0) "The list and the ${listsThings(itemCount)} on it go for good." else "The list goes for good.") +
        " This can't be undone."

// ---------------------------------------------------------------------------
// Order and selection.
// ---------------------------------------------------------------------------

/** The server's order: position, then id (gaps are normal). */
internal fun inListOrder(items: List<HouseholdListItem>): List<HouseholdListItem> =
    items.sortedWith(compareBy<HouseholdListItem>({ it.position }, { it.id }))

/** The list shown: the one this phone last picked, else the first. */
internal fun selectedList(lists: List<HouseholdList>, savedId: Long?): HouseholdList? =
    lists.firstOrNull { it.id == savedId } ?: lists.firstOrNull()

/** The open items' ids with [id] moved to [toIndex] (among the open items):
 *  the `item_ids` of `PUT …/items/order`. Crossed-off items are never in it.
 *  Null when [id] is not an open item. */
internal fun openOrderWithMove(items: List<HouseholdListItem>, id: Long, toIndex: Int): List<Long>? {
    val ids = items.filter { !it.done }.map { it.id }.toMutableList()
    val from = ids.indexOf(id)
    if (from < 0) return null
    ids.removeAt(from)
    ids.add(toIndex.coerceIn(0, ids.size), id)
    return ids
}

/** "move up" (-1) / "move down" (+1) for [id], or null at the end it can't
 *  pass (the accessibility actions; the dashboard's arrow keys). */
internal fun openOrderStep(items: List<HouseholdListItem>, id: Long, delta: Int): List<Long>? {
    val open = items.filter { !it.done }.map { it.id }
    val from = open.indexOf(id)
    val to = from + delta
    if (from < 0 || to < 0 || to >= open.size) return null
    return openOrderWithMove(items, id, to)
}

/**
 * The open list's items as the screen draws them. A re-read lands here
 * ([serverSays]); while a drag is under way it is held back, not applied
 * (a voice add arriving mid-drag would otherwise reorder the rows under the
 * finger), and the drop decides: a moved order is sent and the screen
 * re-reads; an unmoved one takes what was held back.
 */
internal class ListBoard(initial: List<HouseholdListItem>) {
    var items by mutableStateOf(inListOrder(initial))
        private set

    /** The open ids in their dragged order while a drag is under way. */
    var dragOrder by mutableStateOf<List<Long>?>(null)
        private set
    var dragId by mutableStateOf<Long?>(null)
        private set
    var dragOffset by mutableStateOf(0f)

    private var heldBack: List<HouseholdListItem>? = null

    val dragging: Boolean get() = dragOrder != null

    val open: List<HouseholdListItem>
        get() {
            val all = items.filter { !it.done }
            val order = dragOrder ?: return all
            return order.mapNotNull { id -> all.firstOrNull { it.id == id } }
        }
    val done: List<HouseholdListItem> get() = items.filter { it.done }

    fun serverSays(server: List<HouseholdListItem>) {
        if (dragging) heldBack = server else items = inListOrder(server)
    }

    /** An optimistic change (cross off, new words, removed). */
    fun change(f: (List<HouseholdListItem>) -> List<HouseholdListItem>) {
        items = f(items)
    }

    fun startDrag(id: Long) {
        if (items.none { it.id == id && !it.done }) return
        dragOrder = items.filter { !it.done }.map { it.id }
        dragId = id
        dragOffset = 0f
    }

    /** The dragged row passes [targetId]: they swap places. */
    fun dragPast(targetId: Long): Boolean {
        val order = dragOrder?.toMutableList() ?: return false
        val a = order.indexOf(dragId ?: return false)
        val b = order.indexOf(targetId)
        if (a < 0 || b < 0 || a == b) return false
        order.removeAt(a)
        order.add(b, dragId!!)
        dragOrder = order
        return true
    }

    /** The drop: the new open order to send, or null when nothing moved
     *  (then what a re-read brought meanwhile is applied now). */
    fun endDrag(): List<Long>? {
        val order = dragOrder ?: return null
        val before = items.filter { !it.done }.map { it.id }
        dragOrder = null
        dragId = null
        dragOffset = 0f
        val held = heldBack
        heldBack = null
        if (order == before) {
            held?.let { items = inListOrder(it) }
            return null
        }
        applyOrder(order)
        return order
    }

    /** Open items in [order] (the ones it doesn't name after them, in place),
     *  then the crossed-off ones: what the server will answer. */
    fun applyOrder(order: List<Long>) {
        val open = items.filter { !it.done }
        val named = order.mapNotNull { id -> open.firstOrNull { it.id == id } }
        items = named + open.filter { it.id !in order } + items.filter { it.done }
    }
}

// ---------------------------------------------------------------------------
// The add box: empties at once, gets its words back after a failed add.
// ---------------------------------------------------------------------------

/**
 * The add field's words. [submit] empties it as soon as the add is sent, so
 * the next item can be typed while this one travels; a failed add puts its
 * words back only if the field is still empty (what was typed since is never
 * overwritten). The dashboard bug of 2026-11-11: fast typing dropped items.
 */
internal class ListAddField(initial: String = "") {
    var text by mutableStateOf(initial)

    /** Null when there was nothing to send. */
    suspend fun <T> submit(send: suspend (String) -> T): Result<T>? {
        val words = text.trim()
        if (words.isEmpty()) return null
        text = ""
        return try {
            Result.success(send(words))
        } catch (e: CancellationException) {
            if (text.isEmpty()) text = words
            throw e
        } catch (e: Exception) {
            if (text.isEmpty()) text = words
            Result.failure(e)
        }
    }
}

// ---------------------------------------------------------------------------
// Errors (web/backend/api/lists.py _refuse, §2.3 of the hand-off).
// ---------------------------------------------------------------------------

/** What the screen does about a failed write: [message] to show (null:
 *  say nothing), and whether to re-read ([reread]; always, after a write). */
internal data class ListsFailure(val message: String?, val code: String? = null, val gone: Boolean = false)

/** The words of a FastAPI `detail`: a sentence; `{code, message}` (the 409s);
 *  pydantic's list of `{msg}`. Null when the body has none. */
internal fun listsDetail(body: String?): Pair<String, String?>? {
    val obj = runCatching { DomovoiJson.parseToJsonElement(body.orEmpty()) as? JsonObject }.getOrNull() ?: return null
    return when (val d = obj["detail"]) {
        is JsonPrimitive -> d.contentOrNull?.takeIf { d.isString && it.isNotBlank() }?.let { it to null }
        is JsonObject -> {
            val msg = (d["message"] as? JsonPrimitive)?.contentOrNull?.takeIf { it.isNotBlank() } ?: return null
            msg to (d["code"] as? JsonPrimitive)?.contentOrNull
        }
        is JsonArray -> d.firstNotNullOfOrNull { e ->
            ((e as? JsonObject)?.get("msg") as? JsonPrimitive)?.contentOrNull?.takeIf { it.isNotBlank() }
        }?.let { it to null }
        else -> null
    }
}

/**
 * A failed write, for the screen. `404`: the list or item went elsewhere
 * (deleted on another phone, by voice): nothing alarming is said and the
 * screen re-reads ([ListsFailure.gone]). `400`/`409`/`422`: the server's own
 * sentence ("there's already a list called Shopping"), with the 409's code.
 * Anything else: the app's usual failure line ([failureText]).
 */
internal fun listsFailure(action: String, e: Throwable): ListsFailure {
    if (e is ApiException) {
        if (e.status == 404) return ListsFailure(null, gone = true)
        if (e.status == 400 || e.status == 409 || e.status == 422) {
            val (msg, code) = listsDetail(e.body.ifBlank { e.message?.substringAfter(": ", "") })
                ?: return ListsFailure(failureText(action, e))
            return ListsFailure(msg, code)
        }
    }
    return ListsFailure(failureText(action, e))
}

/** A read refused for want of the household token: the paired-only state. */
internal fun listsRefused(e: Throwable): Boolean = e is ApiException && (e.status == 401 || e.status == 403)
