package com.domovoi.app.ui.shell

import androidx.compose.runtime.snapshots.Snapshot
import com.domovoi.app.net.Discovery
import com.domovoi.app.net.FoundDomovoi
import com.domovoi.app.testing.JvmComposition
import com.domovoi.app.testing.composeTest
import kotlinx.coroutines.ExecutorCoroutineDispatcher
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger

/**
 * The picker's LAN sweep writes the panel's Compose state only on the
 * composition's own thread, however its results come in. On the batch-11
 * emulator smoke (2026-10-16) the first connect crashed in
 * ServerPicker.kt's progress write, made straight from the sweep's IO
 * threads, up to forty at once; a retry connected.
 *
 * Here composeTest's thread stands in for the main thread: it runs the
 * composition reading the scan's state and the coroutine running the scan,
 * while the results come from a pool of forty worker threads.
 */
class ServerScanTest {
    @get:Rule val timeout: Timeout = Timeout.seconds(60)

    private val pool: ExecutorCoroutineDispatcher = Executors.newFixedThreadPool(40).asCoroutineDispatcher()

    @After fun down() = pool.close()

    private val bases = (1..254).map { "http://10.0.0.$it:6369" }

    private fun hitOrNull(base: String): FoundDomovoi? =
        if (base.endsWith(".7:6369") || base.endsWith(".42:6369")) FoundDomovoi(base, "kitchen-box") else null

    /** Every write to snapshot state made off [main] while [block] runs. */
    private inline fun offThreadWrites(main: Thread, block: () -> Unit): List<String> {
        val off = ConcurrentLinkedQueue<String>()
        val watch = Snapshot.registerGlobalWriteObserver {
            if (Thread.currentThread() !== main) off += "${Thread.currentThread().name} wrote $it"
        }
        try {
            block()
        } finally {
            watch.dispose()
        }
        return off.toList()
    }

    /** Run [block] while frames keep coming, so the composition recomposes
     *  as the scan's state changes, as the panel does on a phone. */
    private suspend fun JvmComposition.whileFramesTick(block: suspend () -> Unit) = coroutineScope {
        val frames = launch {
            while (true) {
                settle(untilQuiet = false, frames = 1)
                delay(1)
            }
        }
        try {
            block()
        } finally {
            frames.cancel()
        }
    }

    @Test fun reportsFromFortyWorkerThreadsReachTheScreenOnlyOnItsOwnThread() = composeTest {
        val scan = ServerScan()
        var shown = ""
        val seen = mutableListOf<Int>()
        setContent {
            val (done, total) = scan.progress
            seen += done
            shown = "$done/$total, ${scan.found.size} found, " +
                if (scan.scanning) "scanning" else if (scan.scanned) "done" else "idle"
        }
        val main = Thread.currentThread()

        val off = offThreadWrites(main) {
            whileFramesTick {
                scan.run { report ->
                    // The pre-fix shape: every report straight from a
                    // worker thread, forty threads at a time.
                    withContext(pool) {
                        val done = AtomicInteger(0)
                        bases.mapIndexed { i, base ->
                            async {
                                Thread.sleep(i % 3L)
                                report(done.incrementAndGet(), bases.size)
                                hitOrNull(base)
                            }
                        }.awaitAll().filterNotNull()
                    }
                }
            }
        }
        settle()

        assertEquals("no Compose state written off the composition's thread", emptyList<String>(), off)
        assertEquals(254 to 254, scan.progress)
        assertEquals(2, scan.found.size)
        assertFalse(scan.scanning)
        assertTrue(scan.scanned)
        assertEquals("254/254, 2 found, done", shown)
        assertEquals("progress never went backwards on screen", seen.sorted(), seen)
    }

    @Test fun theSweepCallsBackOnTheCallersThreadWhicheverThreadAProbeFinishesOn() = runBlocking {
        val caller = Thread.currentThread()
        val callbackThreads = ConcurrentHashMap.newKeySet<Thread>()
        val probeThreads = ConcurrentHashMap.newKeySet<Thread>()
        val dones = ConcurrentLinkedQueue<Int>()
        var lastFound = -1
        val inFlight = AtomicInteger(0)
        val most = AtomicInteger(0)

        val hits = Discovery.sweep(
            bases,
            onProgress = { done, total, found ->
                callbackThreads += Thread.currentThread()
                dones += done
                lastFound = found
                assertEquals(254, total)
            },
        ) { base ->
            withContext(pool) {
                probeThreads += Thread.currentThread()
                most.accumulateAndGet(inFlight.incrementAndGet(), ::maxOf)
                Thread.sleep(2)
                inFlight.decrementAndGet()
                hitOrNull(base)
            }
        }

        assertEquals("every report on the caller's thread", setOf(caller), callbackThreads.toSet())
        assertTrue("the probes ran on the workers", probeThreads.none { it === caller })
        assertEquals("one report per host, in order", (1..254).toList(), dones.toList())
        assertEquals(2, lastFound)
        assertTrue("still a parallel sweep, at most 40 at once (saw ${most.get()})", most.get() in 2..40)
        assertEquals(listOf("http://10.0.0.42:6369", "http://10.0.0.7:6369"), hits.map { it.url })
    }

    @Test fun thePickersWiringEndToEndWritesNothingOffTheCompositionThread() = composeTest {
        val scan = ServerScan()
        var shown = ""
        setContent { shown = "${scan.progress.first}/${scan.progress.second} ${scan.found.map { it.url }}" }
        val main = Thread.currentThread()

        // What ServerPickerPanel runs, with the network swapped for probes
        // that finish on the worker threads, as Discovery.probe's IO does.
        val off = offThreadWrites(main) {
            whileFramesTick {
                scan.run { report ->
                    Discovery.sweep(bases, { done, total, _ -> report(done, total) }) { base ->
                        withContext(pool) {
                            Thread.sleep(base.length % 3L)
                            hitOrNull(base)
                        }
                    }
                }
            }
        }
        settle()

        assertEquals(emptyList<String>(), off)
        assertEquals("254/254 [http://10.0.0.42:6369, http://10.0.0.7:6369]", shown)
        assertTrue(scan.scanned)
    }

    @Test fun aSecondRunWhileOneIsGoingDoesNothing() = runBlocking {
        val scan = ServerScan()
        var sweeps = 0
        coroutineScope {
            launch {
                scan.run { report ->
                    sweeps++
                    delay(50)
                    report(1, 1)
                    emptyList()
                }
            }
            delay(10)
            assertTrue(scan.scanning)
            scan.run { sweeps++; emptyList() }
        }
        assertEquals(1, sweeps)
        assertEquals(1 to 1, scan.progress)
        assertTrue(scan.scanned)
    }
}
