package com.domovoi.app.player

import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Test

class QueueWindowTest {

    private val big = (0 until 5_007).toList()

    @Test fun shortListIsQueuedWhole() {
        val list = (0 until 12).toList()
        val w = QueueWindow.around(list, 7)
        assertSame(list, w.items)
        assertEquals(7, w.index)
    }

    @Test fun exactlyMaxIsQueuedWhole() {
        val list = (0 until QueueWindow.MAX).toList()
        assertEquals(QueueWindow.MAX, QueueWindow.around(list, 499).items.size)
    }

    @Test fun wholeDeviceLibraryIsCutToTheCap() {
        // The 2026-09-30 freeze: tapping one song queued all 5,007 tracks.
        val w = QueueWindow.around(big, 3_000)
        assertEquals(QueueWindow.MAX, w.items.size)
        assertEquals(3_000, w.items[w.index])
        // A short run before the tapped track, the rest after it.
        assertEquals(QueueWindow.LEAD, w.index)
        assertEquals(3_000 - QueueWindow.LEAD, w.items.first())
        assertEquals(3_000 - QueueWindow.LEAD + QueueWindow.MAX - 1, w.items.last())
    }

    @Test fun tapNearTheStartKeepsWhatIsBefore() {
        val w = QueueWindow.around(big, 10)
        assertEquals(0, w.items.first())
        assertEquals(10, w.index)
        assertEquals(QueueWindow.MAX, w.items.size)
    }

    @Test fun tapNearTheEndFillsTheWindowFromBefore() {
        val w = QueueWindow.around(big, big.lastIndex)
        assertEquals(QueueWindow.MAX, w.items.size)
        assertEquals(big.last(), w.items.last())
        assertEquals(big.last(), w.items[w.index])
        assertEquals(QueueWindow.MAX - 1, w.index)
    }

    @Test fun windowIsAlwaysContiguousAndHoldsTheTappedItem() {
        for (tap in listOf(0, 1, 49, 50, 51, 499, 500, 2_500, 4_506, 4_507, 4_957, 5_006)) {
            val w = QueueWindow.around(big, tap, max = 500, lead = 50)
            assertEquals("tap $tap", tap, w.items[w.index])
            assertEquals("tap $tap", w.items.first() + w.items.size - 1, w.items.last())
            assertEquals("tap $tap", 500, w.items.size)
        }
    }

    @Test fun outOfRangeIndexIsClamped() {
        assertEquals(0, QueueWindow.around(big, -4).items[0])
        val w = QueueWindow.around(big, 99_999)
        assertEquals(big.last(), w.items[w.index])
    }

    @Test fun emptyListStaysEmpty() {
        val w = QueueWindow.around(emptyList<Int>(), 0)
        assertEquals(0, w.items.size)
        assertEquals(0, w.index)
    }
}
