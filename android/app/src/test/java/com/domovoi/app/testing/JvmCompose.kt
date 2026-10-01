package com.domovoi.app.testing

import androidx.compose.runtime.AbstractApplier
import androidx.compose.runtime.BroadcastFrameClock
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Composition
import androidx.compose.runtime.Recomposer
import androidx.compose.runtime.snapshots.Snapshot
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield

/**
 * The Compose runtime on the JVM, with no UI: a [Recomposer] driven by a
 * frame clock this test ticks, on one thread (runBlocking's), so the code
 * under test composes, recomposes and runs its effects exactly as it would
 * in the app — only nothing is laid out or drawn.
 *
 * For state-holding composables (`remember…` functions): what they read
 * decides what recomposes, which is what these tests pin.
 */
internal fun composeTest(applier: AbstractApplier<*> = NoNodes(), block: suspend JvmComposition.() -> Unit): Unit =
    runBlocking {
        val clock = BroadcastFrameClock()
        val job = Job(coroutineContext[Job])
        val context = coroutineContext + clock + job
        val recomposer = Recomposer(context)
        launch(context, start = CoroutineStart.UNDISPATCHED) { recomposer.runRecomposeAndApplyChanges() }
        var applyPending = false
        val writes = Snapshot.registerGlobalWriteObserver {
            if (!applyPending) {
                applyPending = true
                launch(context) {
                    applyPending = false
                    Snapshot.sendApplyNotifications()
                }
            }
        }
        val composition = Composition(applier, recomposer)
        try {
            JvmComposition(composition, clock, recomposer).block()
        } finally {
            composition.dispose()
            writes.dispose()
            recomposer.cancel()
            job.cancel()
        }
    }

internal class JvmComposition(
    private val composition: Composition,
    private val clock: BroadcastFrameClock,
    private val recomposer: Recomposer,
) {
    fun setContent(content: @Composable () -> Unit) = composition.setContent(content)

    /** Let every pending write, effect and recomposition run to the end.
     *  An animation that never ends (a progress spinner) keeps frames
     *  coming: with [untilQuiet] false this runs [frames] frames instead. */
    suspend fun settle(untilQuiet: Boolean = true, frames: Int = 200) {
        var quiet = 0
        repeat(frames) {
            Snapshot.sendApplyNotifications()
            yield()
            clock.sendFrame(System.nanoTime())
            yield()
            quiet = if (recomposer.hasPendingWork) 0 else quiet + 1
            if (untilQuiet && quiet >= 5) return
        }
        check(!untilQuiet) { "the composition never settled" }
    }
}

/** An applier for compositions that emit no nodes. */
internal class NoNodes : AbstractApplier<Unit>(Unit) {
    override fun insertTopDown(index: Int, instance: Unit) = Unit
    override fun insertBottomUp(index: Int, instance: Unit) = Unit
    override fun remove(index: Int, count: Int) = Unit
    override fun move(from: Int, to: Int, count: Int) = Unit
    override fun onClear() = Unit
}
