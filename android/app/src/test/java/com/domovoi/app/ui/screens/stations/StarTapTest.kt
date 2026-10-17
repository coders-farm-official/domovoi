package com.domovoi.app.ui.screens.stations

import com.domovoi.app.net.decode
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The star on a Stations search hit is a real toggle (plugin board F-P013).
 * A new online hit has id 0; its first star saves it (POST), and the row
 * keeps the id that POST answers, so the second tap unfavorites the saved
 * row (PATCH) instead of doing nothing, and a third favorites it again
 * without saving a second copy.
 */
class StarTapTest {
    @Test fun aNewOnlineHitIsSavedOnItsFirstTap() {
        assertEquals(StarTap.Post, searchStarTap(rowId = 0L, favorited = false))
    }

    @Test fun theTapsAfterTheSaveFlipTheSavedRow() {
        // What the row holds after the POST answered {"id": 7, ...}.
        val saved: JsonElement = Json.parseToJsonElement(
            """{"id": 7, "name": "101 SMOOTH JAZZ", "source": "online", "favorited": true}""",
        )
        var rowId = saved.decode<Station>().id
        var favorited = true

        val second = searchStarTap(rowId, favorited)
        assertEquals(StarTap.Patch(7L, favorited = false), second)
        favorited = (second as StarTap.Patch).favorited

        val third = searchStarTap(rowId, favorited)
        assertEquals(StarTap.Patch(7L, favorited = true), third)
    }

    @Test fun aSavedRowAlwaysPatches() {
        // Local FM rows and hits /search found in the table come with their id.
        assertEquals(StarTap.Patch(42L, favorited = false), searchStarTap(42L, favorited = true))
        assertEquals(StarTap.Patch(42L, favorited = true), searchStarTap(42L, favorited = false))
    }
}
