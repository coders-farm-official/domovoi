package com.domovoi.app.ui.shell

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.domovoi.app.net.FoundDomovoi
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * The picker's LAN sweep and what the panel shows of it: whether one is
 * running, how far it got, what answered.
 *
 * All four are Compose state, and all four are written only by the
 * coroutine that runs [run] — the panel's composition scope, so the main
 * thread. A sweep may report progress from any thread: reports go into a
 * [MutableStateFlow] (safe from any thread, and kept from going backwards
 * when two threads report out of order) and reach [progress] on [run]'s
 * own thread. [com.domovoi.app.net.Discovery.scan] reports on its caller's
 * thread already; this holder does not rely on it.
 *
 * Why: on the batch-11 emulator smoke (2026-10-16) the first connect
 * crashed in the progress write, which the sweep made straight from its IO
 * threads, up to forty at once (a retry connected).
 */
internal class ServerScan {
    var scanning by mutableStateOf(false)
        private set
    var scanned by mutableStateOf(false)
        private set
    var progress by mutableStateOf(0 to 0)
        private set
    var found by mutableStateOf<List<FoundDomovoi>>(emptyList())
        private set

    /**
     * One sweep. [sweep] gets a `report(done, total)` it may call from any
     * thread and returns what answered. Does nothing while a sweep is
     * already running.
     */
    suspend fun run(sweep: suspend (report: (done: Int, total: Int) -> Unit) -> List<FoundDomovoi>) {
        if (scanning) return
        scanning = true
        scanned = false
        found = emptyList()
        progress = 0 to 0
        val reports = MutableStateFlow(0 to 0)
        try {
            coroutineScope {
                val shown = launch { reports.collect { progress = it } }
                val hits = sweep { done, total ->
                    reports.update { if (done > it.first) done to total else it }
                }
                shown.cancel()
                found = hits
            }
            progress = reports.value
            scanned = true
        } finally {
            scanning = false
        }
    }
}
