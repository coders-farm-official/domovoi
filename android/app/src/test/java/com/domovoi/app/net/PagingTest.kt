package com.domovoi.app.net

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import kotlinx.serialization.Serializable
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The phone's load-as-you-scroll state (net/Paging.kt) against the server's
 * paging contract (web/backend/api/paging.py): append, no duplicates,
 * retry of the failed page, reset on a new filter, reload in place, and the
 * answers older servers give.
 */
class PagingTest {
    /** A server list of [n] rows, cursor = the next row's index. */
    private class FakeServer(val n: Int, val repeatLast: Boolean = false) {
        val requests = mutableListOf<PageRequest>()
        var failNext = 0
        var gate: CompletableDeferred<Unit>? = null
        var rows: List<String> = (0 until n).map { "row-$it" }

        suspend fun fetch(req: PageRequest): Page<String> {
            requests += req
            gate?.await()
            if (failNext > 0) {
                failNext--
                throw ApiException(500, "500 disk on fire")
            }
            val start = req.cursor?.toInt() ?: 0
            val from = if (repeatLast && start > 0) start - 1 else start
            val end = minOf(start + req.limit, rows.size)
            val more = end < rows.size
            return Page(rows.subList(from, end), rows.size, more, if (more) end.toString() else null)
        }
    }

    private fun pagerOf(scope: kotlinx.coroutines.CoroutineScope, server: FakeServer): Pager<String> =
        Pager(scope, pageSize = 50, keyOf = { it }) { server.fetch(it) }

    @Test fun firstPageThenAppendByCursor() = runBlocking {
        val server = FakeServer(120)
        val pager = pagerOf(this, server)
        pager.reset(); pager.idle()
        var s = pager.state.value
        assertEquals(50, s.items.size)
        assertEquals(120, s.total)
        assertTrue(s.hasMore && s.canLoadMore)
        pager.loadMore(); pager.idle()
        pager.loadMore(); pager.idle()
        s = pager.state.value
        assertEquals((0 until 120).map { "row-$it" }, s.items)
        assertFalse(s.hasMore)
        assertEquals(listOf(null, "50", "100"), server.requests.map { it.cursor })
        assertEquals(listOf(50, 50, 50), server.requests.map { it.limit })
        pager.loadMore(); pager.idle()
        assertEquals("no request past the end", 3, server.requests.size)
    }

    @Test fun oneRequestInFlightAtATime() = runBlocking {
        val server = FakeServer(200)
        val pager = pagerOf(this, server)
        pager.reset(); pager.idle()
        server.gate = CompletableDeferred()
        pager.loadMore(); pager.loadMore(); pager.loadMore()
        yield()
        assertTrue(pager.state.value.loading)
        server.gate!!.complete(Unit); pager.idle()
        assertEquals(2, server.requests.size)
        assertEquals(100, pager.state.value.items.size)
    }

    @Test fun aRowSentTwiceIsShownOnce() = runBlocking {
        val server = FakeServer(120, repeatLast = true)
        val pager = pagerOf(this, server)
        pager.reset(); pager.idle()
        pager.loadMore(); pager.idle()
        val items = pager.state.value.items
        assertEquals(items.distinct(), items)
        assertEquals(100, items.size)
    }

    @Test fun aFailedPageWaitsForRetryAndRetriesTheSamePage() = runBlocking {
        val server = FakeServer(120)
        val pager = pagerOf(this, server)
        pager.reset(); pager.idle()
        server.failNext = 1
        pager.loadMore(); pager.idle()
        var s = pager.state.value
        assertEquals("500 disk on fire", s.error)
        assertEquals(50, s.items.size)
        assertFalse("the scroll trigger stops on a failure", s.canLoadMore)
        pager.loadMore(); pager.idle()
        assertEquals(2, server.requests.size)
        pager.retry(); pager.idle()
        s = pager.state.value
        assertNull(s.error)
        assertEquals(100, s.items.size)
        assertEquals(listOf(null, "50", "50"), server.requests.map { it.cursor })
    }

    @Test fun aFailedFirstPageRetriesPageOne() = runBlocking {
        val server = FakeServer(10)
        val pager = pagerOf(this, server)
        server.failNext = 1
        pager.reset(); pager.idle()
        assertTrue(pager.state.value.error != null && pager.state.value.items.isEmpty())
        pager.retry(); pager.idle()
        assertEquals(10, pager.state.value.items.size)
        assertEquals(listOf<String?>(null, null), server.requests.map { it.cursor })
    }

    @Test fun aResetDropsTheAnswerOfTheOldList() = runBlocking {
        val server = FakeServer(120)
        val pager = pagerOf(this, server)
        pager.reset(); pager.idle()
        server.gate = CompletableDeferred()
        pager.loadMore()
        yield()
        // The filter changes while page two is on its way.
        server.rows = listOf("match-1", "match-2")
        server.gate = null
        pager.reset(); pager.idle()
        val s = pager.state.value
        assertEquals(listOf("match-1", "match-2"), s.items)
        assertEquals(2, s.total)
        assertFalse(s.hasMore)
    }

    @Test fun reloadRereadsTheLoadedWindowInPlace() = runBlocking {
        val server = FakeServer(300)
        val pager = pagerOf(this, server)
        pager.reset(); pager.idle()
        pager.loadMore(); pager.idle()
        pager.loadMore(); pager.idle()
        server.requests.clear()
        server.rows = listOf("new-top") + server.rows
        pager.reload(fresh = true); pager.idle()
        val s = pager.state.value
        assertEquals(1, server.requests.size)
        assertEquals(150, server.requests[0].limit)
        assertTrue(server.requests[0].fresh)
        assertEquals("new-top", s.items.first())
        assertEquals(150, s.items.size)
        assertEquals(301, s.total)
        assertTrue(s.hasMore)
    }

    @Test fun updateAndRemoveEditInPlace() = runBlocking {
        val server = FakeServer(3)
        val pager = pagerOf(this, server)
        pager.reset(); pager.idle()
        pager.update("row-1") { "row-1*" }
        pager.remove("row-0")
        val s = pager.state.value
        assertEquals(listOf("row-1*", "row-2"), s.items)
        assertEquals(2, s.total)
    }

    // ── decoding the server's answers ──────────────────────────────────────

    @Serializable private data class Row(val id: Int = 0)

    @Test fun decodesThePagedAnswer() {
        val json = DomovoiJson.parseToJsonElement(
            """{"videos":[{"id":1},{"id":2}],"total":9,"has_more":true,"next_cursor":"abc","libraries":[]}""",
        )
        val p = json.toPage<Row>("videos")
        assertEquals(listOf(Row(1), Row(2)), p.items)
        assertEquals(9, p.total)
        assertTrue(p.hasMore)
        assertEquals("abc", p.nextCursor)
        assertFalse(p.extra!!.containsKey("videos"))
        assertTrue(p.extra!!.containsKey("libraries"))
    }

    @Test fun anOldServersBareArrayIsOneCompletePage() {
        val p = DomovoiJson.parseToJsonElement("""[{"id":1},{"id":2},{"id":3}]""").toPage<Row>()
        assertEquals(3, p.items.size)
        assertFalse(p.hasMore)
        assertEquals(3, p.total)
    }

    @Test fun anOldServersMusicPageFallsBackToOffsets() {
        val json = DomovoiJson.parseToJsonElement("""{"total":120,"items":[{"id":1}]}""")
        assertTrue(json.toPage<Row>(loadedBefore = 50).hasMore)
        assertFalse(json.toPage<Row>(loadedBefore = 119).hasMore)
        assertNull(json.toPage<Row>().nextCursor)
    }

    @Test fun pathsCarryLimitOffsetCursorAndRefresh() {
        assertEquals("/api/videos/list?sort=name&limit=50",
            Paging.path("/api/videos/list?sort=name", PageRequest(null, 0, 50)))
        assertEquals("/api/audiobooks?limit=50&offset=50&cursor=eyJz%2B",
            Paging.path("/api/audiobooks", PageRequest("eyJz+", 50, 50)))
        assertEquals("/api/files/browse?x=1&limit=50&refresh=true",
            Paging.path("/api/files/browse?x=1", PageRequest(null, 0, 50, fresh = true)))
    }

    @Test fun countText() {
        assertEquals("showing 100 of 5,232 tracks", Paging.countText(100, 5232, "tracks"))
        assertEquals("12 videos", Paging.countText(12, null, "videos"))
    }

    @Test fun sliceCutsAPageFromAListInMemory() {
        val all = (0 until 120).toList()
        val p = Paging.slice(all, PageRequest(null, 100, 50))
        assertEquals((100 until 120).toList(), p.items)
        assertFalse(p.hasMore)
        assertEquals(120, p.total)
        assertTrue(Paging.slice(all, PageRequest(null, 0, 50)).hasMore)
    }

    @Test fun mergeKeepsOrderAndDropsKnownKeys() {
        assertEquals(listOf("a", "b", "c"), Paging.merge(listOf("a", "b"), listOf("b", "c", "a"), { it }))
    }
}
