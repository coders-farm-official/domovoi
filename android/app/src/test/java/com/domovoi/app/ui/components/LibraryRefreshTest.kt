package com.domovoi.app.ui.components

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The library screens' refresh (Videos, Files, Podcasts, Audiobooks): the
 * header button and the pull-to-refresh are one action, so a pull while the
 * button's rescan runs (or a second tap) starts nothing more, and "busy"
 * holds until the rescan ends — then a new one may start.
 */
class LibraryRefreshTest {

    @Test fun oneRefreshAtATimeAndBusyUntilItEnds() = runBlocking {
        var runs = 0
        var gate = CompletableDeferred<Unit>()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val refresh = LibraryRefresh(scope) {
            runs += 1
            gate.await()
        }
        assertFalse(refresh.busy)
        refresh.refresh()
        assertTrue(refresh.busy)
        refresh.refresh()                 // the pull while the button's runs
        assertEquals(1, runs)
        gate.complete(Unit)
        yield()
        assertFalse(refresh.busy)

        gate = CompletableDeferred()
        refresh.refresh()
        assertEquals(2, runs)
        gate.complete(Unit)
        yield()
        assertFalse(refresh.busy)
    }

    @Test fun aFailedRefreshStillEndsBusy() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined +
            kotlinx.coroutines.CoroutineExceptionHandler { _, _ -> })
        val refresh = LibraryRefresh(scope) { error("the server said no") }
        refresh.refresh()
        yield()
        assertFalse(refresh.busy)
    }

    @Test fun theEventIsTheOneTheWebSends() {
        assertEquals("files.changed", FILES_CHANGED)
    }
}
