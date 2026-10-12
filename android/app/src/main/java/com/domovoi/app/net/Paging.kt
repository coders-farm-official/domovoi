package com.domovoi.app.net

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.serializer

/*
 * Load as you scroll (owner decision 2026-10-11): no files-like list —
 * Music, Videos, Files, Audiobooks, Podcasts, and the phone's own lists —
 * ever holds a whole list. Every one of them goes through [Pager], which
 * speaks the server's one paging contract (web/backend/api/paging.py):
 * `limit` plus the opaque keyset `cursor` from the previous answer's
 * `next_cursor`, answered with `total`, `has_more` and `next_cursor`. The
 * web twin is data.js usePagedList.
 *
 * Sorting, filtering and search are the server's: a new filter is a new
 * list (a new [Pager.reset]) from page one.
 */

const val DEFAULT_PAGE_SIZE = 50
/** The server's hard max for one page. */
const val SERVER_MAX_PAGE = 200
/** A [Pager.reload] re-reads at most this many rows; past it, it starts over. */
const val RELOAD_CAP = 1000

/** One page of a list. [extra] is the rest of the answer (a folder's breadcrumb, per-library counts). */
data class Page<T>(
    val items: List<T>,
    val total: Int?,
    val hasMore: Boolean,
    val nextCursor: String?,
    val extra: JsonObject? = null,
)

/** What a fetch is asked for. [offset] is the rows already loaded: a server
 *  from before cursors (Music's offset paging) reads it; a newer one follows
 *  [cursor] and ignores it. [fresh] asks a filesystem list to re-read disk. */
data class PageRequest(val cursor: String?, val offset: Int, val limit: Int, val fresh: Boolean = false)

data class PagedState<T>(
    val items: List<T> = emptyList(),
    val total: Int? = null,
    val hasMore: Boolean = false,
    val nextCursor: String? = null,
    val loading: Boolean = false,
    val error: String? = null,
    val extra: JsonObject? = null,
    /** At least one answer has arrived for this list. */
    val loaded: Boolean = false,
) {
    /** The scroll trigger may ask for the next page: one at a time, never over a failure. */
    val canLoadMore: Boolean get() = hasMore && !loading && error == null
}

/** Pure transitions, kept apart so the JVM tests can drive them directly. */
object Paging {
    /** [page] appended to [items], skipping any row whose key is already there. */
    fun <T> merge(items: List<T>, page: List<T>, keyOf: (T) -> Any): List<T> {
        if (page.isEmpty()) return items
        val seen = HashSet<Any>(items.size + page.size)
        items.forEach { seen += keyOf(it) }
        val out = ArrayList<T>(items.size + page.size)
        out.addAll(items)
        for (it in page) if (seen.add(keyOf(it))) out += it
        return out
    }

    fun <T> appended(s: PagedState<T>, page: Page<T>, keyOf: (T) -> Any): PagedState<T> = PagedState(
        items = merge(s.items, page.items, keyOf),
        total = page.total ?: s.total,
        hasMore = page.hasMore,
        nextCursor = page.nextCursor,
        loading = false,
        error = null,
        extra = page.extra ?: s.extra,
        loaded = true,
    )

    fun <T> failed(s: PagedState<T>, message: String): PagedState<T> =
        s.copy(loading = false, error = message, loaded = true)

    /** "showing 100 of 5,232 tracks" (or "100 tracks" when the total is unknown). */
    fun countText(shown: Int, total: Int?, noun: String): String {
        val f = java.text.NumberFormat.getIntegerInstance(java.util.Locale.US)
        return if (total != null) "showing ${f.format(shown)} of ${f.format(total)} $noun"
        else "${f.format(shown)} $noun"
    }

    /** [base] (filters and sort, no paging) plus this request's paging params. */
    fun path(base: String, req: PageRequest): String = buildString {
        append(base)
        append(if ('?' in base) '&' else '?')
        append("limit=").append(req.limit)
        if (req.offset > 0) append("&offset=").append(req.offset)
        req.cursor?.let { append("&cursor=").append(java.net.URLEncoder.encode(it, "UTF-8")) }
        if (req.fresh) append("&refresh=true")
    }

    /**
     * One answer as a [Page]. [itemsKey] names the rows ("items", "videos",
     * "entries"). Tolerates every older server: a bare array, or an object
     * without the paging fields, is one complete page; an answer with a
     * `total` but no `has_more` (Music before cursors) has more when the rows
     * so far fall short of it.
     */
    fun <T> decode(
        json: JsonElement,
        item: KSerializer<T>,
        itemsKey: String = "items",
        loadedBefore: Int = 0,
    ): Page<T> {
        if (json is JsonArray) {
            val all = json.map { DomovoiJson.decodeFromJsonElement(item, it) }
            return Page(all, all.size, hasMore = false, nextCursor = null)
        }
        val obj = json as? JsonObject ?: return Page(emptyList(), 0, false, null)
        val rows = (obj[itemsKey] as? JsonArray).orEmpty().map { DomovoiJson.decodeFromJsonElement(item, it) }
        val total = (obj["total"] as? JsonPrimitive)?.intOrNull
        val hasMore = (obj["has_more"] as? JsonPrimitive)?.booleanOrNull
            ?: (total != null && loadedBefore + rows.size < total)
        val next = (obj["next_cursor"] as? JsonPrimitive)?.contentOrNull
        return Page(rows, total, hasMore, next, JsonObject(obj - itemsKey))
    }

    /** A page cut from a list already in memory or a database cursor's position (the phone's own lists). */
    fun <T> slice(all: List<T>, req: PageRequest): Page<T> {
        val from = req.offset.coerceIn(0, all.size)
        val to = (from + req.limit).coerceAtMost(all.size)
        return Page(all.subList(from, to).toList(), all.size, to < all.size, null)
    }
}

inline fun <reified T> JsonElement.toPage(itemsKey: String = "items", loadedBefore: Int = 0): Page<T> =
    Paging.decode(this, serializer<T>(), itemsKey, loadedBefore)

/**
 * One list, loaded a page at a time. Lives in a composition's scope
 * ([com.domovoi.app.ui.components.rememberPager]); every method is called on
 * that scope's thread. A list that was reset while a page was in flight
 * drops that page's answer.
 */
class Pager<T>(
    private val scope: CoroutineScope,
    val pageSize: Int = DEFAULT_PAGE_SIZE,
    private val keyOf: (T) -> Any,
    private val fetch: suspend (PageRequest) -> Page<T>,
) {
    private val _state = MutableStateFlow(PagedState<T>())
    val state: StateFlow<PagedState<T>> = _state
    private var generation = 0
    private var job: Job? = null

    /** A new list: forget what was loaded and read page one. */
    fun reset() = readFromTop(want = 0, fresh = false)

    /** The next page, when there is one and nothing is in flight. */
    fun loadMore() {
        val s = _state.value
        if (!s.canLoadMore) return
        val mine = generation
        _state.value = s.copy(loading = true, error = null)
        job = scope.launch {
            val req = PageRequest(s.nextCursor, s.items.size, pageSize)
            val page = try {
                fetch(req)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (mine == generation) _state.value = Paging.failed(_state.value, e.message ?: "request failed")
                return@launch
            }
            if (mine == generation) _state.value = Paging.appended(_state.value, page, keyOf)
        }
    }

    /** After a failure: page one again when nothing loaded, else the page that failed. */
    fun retry() {
        val s = _state.value
        if (s.items.isEmpty()) {
            reset()
            return
        }
        _state.value = s.copy(error = null)
        loadMore()
    }

    /**
     * Re-read what is loaded — a change event, or after an edit — keeping
     * the list where it is; [fresh] makes a filesystem list re-walk disk.
     * Past [RELOAD_CAP] rows it starts over at page one instead.
     */
    fun reload(fresh: Boolean = false) {
        val n = _state.value.items.size
        readFromTop(want = if (n > RELOAD_CAP) 0 else n, fresh = fresh)
    }

    fun update(key: Any, change: (T) -> T) {
        val s = _state.value
        _state.value = s.copy(items = s.items.map { if (keyOf(it) == key) change(it) else it })
    }

    fun remove(key: Any) {
        val s = _state.value
        val kept = s.items.filter { keyOf(it) != key }
        val gone = s.items.size - kept.size
        _state.value = s.copy(items = kept, total = s.total?.let { (it - gone).coerceAtLeast(0) })
    }

    /** For tests: wait for whatever is in flight. */
    suspend fun idle() {
        job?.join()
    }

    private fun readFromTop(want: Int, fresh: Boolean) {
        val mine = ++generation
        job?.cancel()
        val before = _state.value
        _state.value = if (want == 0) PagedState(loading = true) else before.copy(loading = true, error = null)
        val target = maxOf(pageSize, want.coerceAtMost(RELOAD_CAP))
        job = scope.launch {
            var items = emptyList<T>()
            var last: Page<T>? = null
            var cursor: String? = null
            try {
                do {
                    val limit = (target - items.size).coerceIn(pageSize, SERVER_MAX_PAGE)
                    val page = fetch(PageRequest(cursor, items.size, limit, fresh = fresh && cursor == null))
                    if (mine != generation) return@launch
                    items = Paging.merge(items, page.items, keyOf)
                    last = page
                    cursor = page.nextCursor
                } while (page.hasMore && items.size < target && (cursor != null || page.items.isNotEmpty()))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (mine == generation) {
                    _state.value = Paging.failed(if (want == 0) PagedState() else before, e.message ?: "request failed")
                }
                return@launch
            }
            val p = last ?: return@launch
            _state.value = PagedState(
                items = items,
                total = p.total,
                hasMore = p.hasMore,
                nextCursor = p.nextCursor,
                extra = p.extra,
                loaded = true,
            )
        }
    }
}
