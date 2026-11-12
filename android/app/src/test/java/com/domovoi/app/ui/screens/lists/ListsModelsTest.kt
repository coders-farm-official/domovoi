package com.domovoi.app.ui.screens.lists

import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * The Lists screen's decoders, wording, order and add box, against the
 * contract in design-notes/LISTS-MOBILE-HANDOFF.md §2 (the JSON below is
 * copied from it, not written to match the decoder).
 */
class ListsModelsTest {

    private inline fun <reified T> decode(json: String): T = DomovoiJson.decodeFromString(json)

    // ── §7.1 decoding, the §2 shapes verbatim ────────────────────────────

    private val listJson = """{"id": 2, "name": "Shopping", "position": 2, "open_count": 3, "done_count": 1,
 "created_at": "2026-11-11T20:55:21.123+00:00", "updated_at": "2026-11-11T21:03:00+00:00"}"""

    private val voiceItemJson = """{"id": 41, "list_id": 2, "text": "Milk", "done": false, "done_at": null, "position": 4,
 "added_by_person_id": 1, "added_by_name": "Alex", "added_by_room": "kitchen",
 "added_via": "voice",
 "created_at": "...", "updated_at": "..."}"""

    // An item added on a screen: "Items added from the dashboard or an app
    // have all three null" (§2.1); crossed off, so `done_at` is set.
    private val screenItemJson = """{"id": 42, "list_id": 2, "text": "Bread", "done": true,
 "done_at": "2026-11-11T21:03:00+00:00", "position": 5,
 "added_by_person_id": null, "added_by_name": null, "added_by_room": null,
 "added_via": "screen",
 "created_at": "...", "updated_at": "..."}"""

    @Test fun aListDecodes() {
        val l = decode<HouseholdList>(listJson)
        assertEquals(HouseholdList(2, "Shopping", 2, 3, 1, "2026-11-11T20:55:21.123+00:00", "2026-11-11T21:03:00+00:00"), l)
        assertEquals(listOf(l), decode<List<HouseholdList>>("[$listJson]"))
    }

    @Test fun anItemDecodesWithAndWithoutWhoAddedIt() {
        val v = decode<HouseholdListItem>(voiceItemJson)
        assertEquals(41L, v.id); assertEquals(2L, v.list_id); assertEquals("Milk", v.text)
        assertFalse(v.done); assertNull(v.done_at); assertEquals(4, v.position)
        assertEquals(1L, v.added_by_person_id); assertEquals("Alex", v.added_by_name)
        assertEquals("kitchen", v.added_by_room); assertEquals("voice", v.added_via)

        val s = decode<HouseholdListItem>(screenItemJson)
        assertTrue(s.done); assertEquals("2026-11-11T21:03:00+00:00", s.done_at)
        assertNull(s.added_by_person_id); assertNull(s.added_by_name); assertNull(s.added_by_room)
        assertEquals("screen", s.added_via)
    }

    @Test fun theDetailWrapperDecodes() {
        val d = decode<HouseholdListDetail>("""{"list": $listJson, "items": [$voiceItemJson, $screenItemJson]}""")
        assertEquals("Shopping", d.list.name)
        assertEquals(listOf(41L, 42L), d.items.map { it.id })
    }

    @Test fun theAddAnswerDecodesWithEachOutcome() {
        for (outcome in listOf("added", "reopened", "already")) {
            val a = decode<ListItemAdded>("""{"item": $voiceItemJson, "outcome": "$outcome"}""")
            assertEquals(outcome, a.outcome)
            assertEquals("Milk", a.item.text)
        }
        val milk = decode<HouseholdListItem>(voiceItemJson)
        assertNull(addNote(ListItemAdded(milk, "added")))
        assertEquals("\"Milk\" is already on the list", addNote(ListItemAdded(milk, "already")))
        assertEquals("\"Milk\" is back on the list", addNote(ListItemAdded(milk, "reopened")))
        assertEquals(3, decode<ListsCleared>("""{"removed": 3}""").removed)
    }

    @Test fun aListMissingItsNameIsNotDrawnBlank() {
        // A required field gone from the server's answer fails the read.
        assertTrue(runCatching { decode<HouseholdList>("""{"id": 2, "position": 2}""") }.isFailure)
    }

    // ── §7.2 errors ──────────────────────────────────────────────────────

    private fun http(status: Int, body: String) = ApiException(status, "$status x: $body", body = body)

    @Test fun a409SaysTheServersMessageAndKeepsItsCode() {
        val f = listsFailure(
            "make list",
            http(409, """{"detail": {"code": "list_name_taken", "message": "there's already a list called Shopping"}}"""),
        )
        assertEquals("there's already a list called Shopping", f.message)
        assertEquals("list_name_taken", f.code)
        assertFalse(f.gone)
        assertEquals(
            "list_full",
            listsFailure("add", http(409, """{"detail": {"code": "list_full", "message": "that list is full"}}""")).code,
        )
    }

    @Test fun a404IsQuietAndMeansReRead() {
        val f = listsFailure("save", http(404, """{"detail": "item 9 not found on list 2"}"""))
        assertNull(f.message)
        assertTrue(f.gone)
    }

    @Test fun a422GivesAMessageFromAStringOrPydanticsList() {
        assertEquals("text can't be empty", listsFailure("add", http(422, """{"detail": "text can't be empty"}""")).message)
        val pyd = """{"detail": [{"type": "string_too_long", "loc": ["body", "text"],
            "msg": "String should have at most 200 characters", "input": "x", "ctx": {"max_length": 200}}]}"""
        assertEquals("String should have at most 200 characters", listsFailure("add", http(422, pyd)).message)
        assertEquals("no fields provided", listsFailure("save", http(400, """{"detail": "no fields provided"}""")).message)
    }

    @Test fun otherFailuresUseTheAppsUsualLine() {
        assertEquals("add failed (offline?)", listsFailure("add", IOException("reset")).message)
        assertTrue(listsFailure("add", http(500, "oops")).message!!.startsWith("add failed: 500"))
        assertTrue(listsRefused(http(401, "{}")))
        assertTrue(listsRefused(http(403, "{}")))
        assertFalse(listsRefused(http(404, "{}")))
    }

    // ── §7.3 order ───────────────────────────────────────────────────────

    private fun item(id: Long, position: Int, done: Boolean = false) =
        HouseholdListItem(id = id, list_id = 2, text = "i$id", done = done, position = position)

    // Server order is (position, id): 12, 9 (crossed off), 15, 20.
    private val items = listOf(item(15, 3), item(9, 2, done = true), item(12, 1), item(20, 3))

    @Test fun theServersOrderIsPositionThenId() {
        assertEquals(listOf(12L, 9L, 15L, 20L), inListOrder(items).map { it.id })
    }

    @Test fun movingAnItemSendsTheOpenItemsOnlyInTheirNewOrder() {
        val sorted = inListOrder(items)
        assertEquals(listOf(20L, 12L, 15L), openOrderWithMove(sorted, 20, 0))
        assertEquals(listOf(15L, 20L, 12L), openOrderWithMove(sorted, 12, 99))
        assertNull(openOrderWithMove(sorted, 9, 0)) // crossed off: not movable
        assertEquals(listOf(15L, 12L, 20L), openOrderStep(sorted, 15, -1))
        assertEquals(listOf(12L, 20L, 15L), openOrderStep(sorted, 15, +1))
        assertNull(openOrderStep(sorted, 12, -1))
        assertNull(openOrderStep(sorted, 20, +1))
        listOfNotNull(openOrderWithMove(sorted, 20, 0), openOrderStep(sorted, 15, -1))
            .forEach { assertFalse(9L in it) }
    }

    @Test fun aDragSendsTheOpenOrderAndARefreshWaitsForTheDrop() {
        val board = ListBoard(items)
        board.startDrag(20)
        assertTrue(board.dragPast(15))
        assertTrue(board.dragPast(12))
        assertEquals(listOf(20L, 12L, 15L), board.open.map { it.id })
        // A voice add lands mid-drag: held back, the rows don't move.
        board.serverSays(items + item(30, 9))
        assertEquals(listOf(20L, 12L, 15L), board.open.map { it.id })
        assertEquals(listOf(20L, 12L, 15L), board.endDrag())
        assertFalse(board.dragging)
        assertEquals(listOf(20L, 12L, 15L, 9L), board.items.map { it.id })
        // Not moved: the held-back read is taken at the drop.
        board.startDrag(12)
        board.serverSays(items + item(30, 9))
        assertNull(board.endDrag())
        assertEquals(listOf(12L, 15L, 20L, 30L), board.open.map { it.id })
        // A crossed-off item can't be dragged.
        board.startDrag(9)
        assertFalse(board.dragging)
    }

    // ── §7.4 who-line ────────────────────────────────────────────────────

    @Test fun theWhoLine() {
        val v = item(1, 1).copy(added_via = "voice")
        assertEquals("Alex · kitchen", whoLine(v.copy(added_by_name = "Alex", added_by_room = "kitchen")))
        assertEquals("by voice · kitchen", whoLine(v.copy(added_by_room = "kitchen")))
        assertEquals("Alex", whoLine(v.copy(added_by_name = "Alex")))
        assertEquals("by voice", whoLine(v))
        assertNull(whoLine(item(1, 1).copy(added_by_name = "Alex", added_by_room = "kitchen")))
        assertEquals("Alex · kitchen", whoLine(decode(voiceItemJson)))
        assertNull(whoLine(decode(screenItemJson)))
    }

    @Test fun theWording() {
        assertEquals("to-do, shopping and your own", listsHeaderSub(emptyList()))
        val l = decode<HouseholdList>(listJson)
        assertEquals("1 list · 3 things to go", listsHeaderSub(listOf(l)))
        assertEquals("2 lists · 4 things to go", listsHeaderSub(listOf(l, l.copy(id = 3, open_count = 1))))
        assertEquals("1 thing to go", listCardSub(1, 0))
        assertEquals("2 things to go · 1 crossed off", listCardSub(2, 1))
        assertEquals("add to shopping", addPlaceholder("Shopping"))
        assertEquals("type above, or say \"add milk to the camping list\"", emptyListHint("Camping"))
        assertEquals("The list and the 3 things on it go for good. This can't be undone.", deleteListBody(3))
        assertEquals(l, selectedList(listOf(l.copy(id = 1), l), 2L))
        assertEquals(1L, selectedList(listOf(l.copy(id = 1), l), 99L)!!.id)
        assertNull(selectedList(emptyList(), 2L))
    }

    // ── §7.7 the add box ─────────────────────────────────────────────────

    @Test fun theAddFieldEmptiesOnSendAndGetsItsWordsBackAfterAFailedAdd() = runBlocking {
        val field = ListAddField("  Milk ")
        val answer = CompletableDeferred<String>()
        val sent = mutableListOf<String>()
        val first = async(start = CoroutineStart.UNDISPATCHED) {
            field.submit { words -> sent += words; answer.await() }
        }
        // Sent, and the box is empty while the add travels.
        assertEquals(listOf("Milk"), sent)
        assertEquals("", field.text)
        answer.completeExceptionally(IOException("offline"))
        assertTrue(first.await()!!.isFailure)
        assertEquals("Milk", field.text)

        // Typed again while the add was out: that is never overwritten.
        val again = CompletableDeferred<String>()
        val second = async(start = CoroutineStart.UNDISPATCHED) { field.submit { again.await() } }
        assertEquals("", field.text)
        field.text = "Eggs"
        again.completeExceptionally(IOException("offline"))
        assertTrue(second.await()!!.isFailure)
        assertEquals("Eggs", field.text)

        // A good add leaves the box empty; nothing to send sends nothing.
        val ok = async(start = CoroutineStart.UNDISPATCHED) { field.submit { "fine" } }
        assertEquals("fine", ok.await()!!.getOrNull())
        assertEquals("", field.text)
        field.text = "   "
        assertNull(field.submit { error("must not send") })
        yield()
    }
}
