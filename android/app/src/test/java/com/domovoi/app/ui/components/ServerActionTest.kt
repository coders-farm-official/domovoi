package com.domovoi.app.ui.components

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.yield
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * Server actions that outlive the UI that asked for them
 * (ui/components/ServerAction.kt). In 1.0.0-rc2 the track drawer closed on
 * "play in room" and sent the request from its own composition scope: the
 * request was cancelled with the drawer and `runCatching` turned the
 * cancellation into "play failed: The coroutine scope left the composition".
 */
class ServerActionTest {

    /** The composition's own exception, as Compose throws it when a
     *  `rememberCoroutineScope()` leaves (it is a CancellationException). */
    private class LeftComposition : CancellationException("The coroutine scope left the composition")

    @Test fun theOldWayToastedTheCancellationAsAFailure() = runBlocking {
        // What rc2 did, kept here so the trap stays written down: runCatching
        // catches the cancellation and hands it to onFailure.
        val drawer = CoroutineScope(Job() + Dispatchers.Unconfined)
        val answer = CompletableDeferred<String>()
        val toasts = mutableListOf<String>()
        drawer.launch {
            runCatching { answer.await() }.onFailure { toasts += "play failed: ${it.message}" }
        }
        drawer.cancel(LeftComposition())
        assertEquals(listOf("play failed: The coroutine scope left the composition"), toasts)
    }

    @Test fun aCancelledCallerIsNeverAFailure() = runBlocking {
        val drawer = CoroutineScope(Job() + Dispatchers.Unconfined)
        val answer = CompletableDeferred<String>()
        val failures = mutableListOf<Throwable>()
        val successes = mutableListOf<String>()
        drawer.launchAction(
            action = { answer.await() },
            onFailure = { failures += it },
            onSuccess = { successes += it },
        )
        drawer.cancel(LeftComposition())
        answer.complete("late")
        yield()
        assertTrue("a cancellation was reported as a failure: $failures", failures.isEmpty())
        assertTrue(successes.isEmpty())
    }

    @Test fun theAppScopeOutlivesTheDrawerThatAsked() = runBlocking {
        // The drawer asks, closes at once; the app's scope carries the request
        // to its answer, which still lands as the success toast.
        val app = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val drawer = CoroutineScope(Job() + Dispatchers.Unconfined)
        val answer = CompletableDeferred<String>()
        val toasts = mutableListOf<String>()
        try {
            drawer.launch {
                app.launchAction(
                    action = { answer.await() },
                    onFailure = { toasts += "play failed: ${it.message}" },
                    onSuccess = { toasts += "played $it" },
                )
            }
            drawer.cancel(LeftComposition())
            answer.complete("ZipLoc Bag Boys")
            yield()
            assertEquals(listOf("played ZipLoc Bag Boys"), toasts)
        } finally {
            app.cancel()
        }
    }

    @Test fun aRealFailureIsStillReported() = runBlocking {
        val failures = mutableListOf<Throwable>()
        val job = launchAction(
            action = { throw IOException("unreachable") },
            onFailure = { failures += it },
        )
        job.join()
        assertEquals(1, failures.size)
        assertTrue(failures.single() is IOException)
    }

    @Test fun aTimeoutInsideTheCallIsAFailureNotASilence() = runBlocking {
        // A cancellation that is not ours (the call's own timeout) while the
        // caller is alive must still be said, or the tap does nothing.
        val failures = mutableListOf<Throwable>()
        val job = launchAction(
            action = { withTimeout(10) { delay(5_000) } },
            onFailure = { failures += it },
        )
        job.join()
        assertEquals(1, failures.size)
        assertTrue(failures.single() is TimeoutCancellationException)
    }

    @Test fun runActionRethrowsItsOwnCancellation() = runBlocking {
        val gate = CompletableDeferred<Unit>()
        var after = false
        val job = launch(Dispatchers.Unconfined) {
            runAction { gate.await() }
            after = true
        }
        job.cancel(LeftComposition())
        job.join()
        assertTrue("runAction swallowed the cancellation and carried on", !after)
    }
}
