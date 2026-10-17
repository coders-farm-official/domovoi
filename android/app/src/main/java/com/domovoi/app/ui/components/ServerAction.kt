package com.domovoi.app.ui.components

import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch

/**
 * Starts a server action (play in a room, cast, add to a playlist, delete)
 * whose answer is a toast and a refresh, not something the asking UI shows.
 *
 * **Which scope.** Pass the app's scope (`AppContainer.scope`) whenever the
 * UI that asked can leave before the answer comes: a drawer that closes on
 * "play in room", a dialog, a dropdown, a lazy row that scrolls away or a
 * tab that is switched. A `rememberCoroutineScope()` is cancelled when its
 * composable leaves, and the request goes with it. In 1.0.0-rc2 the track
 * drawer closed itself on "play in room" and then launched the request in
 * its own scope, so the play never reached the room and the cancellation
 * was toasted as "play failed: The coroutine scope left the composition".
 *
 * **Cancellation is never a failure.** [onFailure] hears only real errors:
 * when the action stops because ITS coroutine was cancelled, nothing is
 * toasted. A cancellation from inside the call (a timeout, say) while the
 * coroutine is still alive is a real failure and is reported as one.
 */
fun <T> CoroutineScope.launchAction(
    action: suspend () -> T,
    onFailure: (Throwable) -> Unit,
    onSuccess: (T) -> Unit = {},
): Job = launch { runAction(action).fold(onSuccess, onFailure) }

/**
 * Whether the calling composable is still on screen, for an answer that
 * arrives in the app's scope and wants to touch the UI that asked: a delete
 * that closes its drawer must not close a different one opened since.
 */
@Composable
fun rememberStillComposed(): () -> Boolean {
    val composed = remember { AtomicBoolean(true) }
    DisposableEffect(Unit) {
        composed.set(true)
        onDispose { composed.set(false) }
    }
    return composed::get
}

/**
 * [runCatching] for a suspending server call, minus the trap: a cancelled
 * caller is rethrown (so the coroutine stops quietly), never returned as a
 * failure to show. Use it where an action needs more than [launchAction]
 * gives (a `finally`, several calls in a row).
 */
suspend fun <T> runAction(action: suspend () -> T): Result<T> = try {
    Result.success(action())
} catch (e: CancellationException) {
    // Our own coroutine was cancelled: stop here, say nothing.
    currentCoroutineContext().ensureActive()
    // Still alive, so the call itself gave up (a timeout inside it).
    Result.failure(e)
} catch (e: Throwable) {
    Result.failure(e)
}
