package com.domovoi.app.audiobooks

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * The books saved on this phone, read once for the whole app: the "your
 * device" side of Audiobooks, the Domovoi side's "on this phone" badges and
 * book page, and the local shell's tab all read this one [state].
 *
 * Each screen used to read the disk for itself, so removing a book on the
 * your-device side left the Domovoi side showing "on this phone" and
 * "remove from phone" until Audiobooks was left (batch 18, 2026-10-20). Now
 * the shelf re-reads whenever the store changes ([BookStore.changes]: a
 * record written, a book removed, by the app or its download job), and every
 * reader sees the same answer at the same time.
 */
class SavedShelf(
    private val store: BookStore,
    private val scope: CoroutineScope,
    private val io: CoroutineDispatcher = Dispatchers.IO,
) {
    /** What the shelf last read. [loaded] is false until the first read. */
    data class Snapshot(
        val books: List<SavedBook> = emptyList(),
        /** Bytes each book takes now, by folder key. */
        val bytes: Map<String, Long> = emptyMap(),
        val usedBytes: Long = 0L,
        val freeBytes: Long = 0L,
        val loaded: Boolean = false,
    )

    private val _state = MutableStateFlow(Snapshot())
    val state: StateFlow<Snapshot> = _state

    init {
        // A StateFlow hands over its value at once, so this is also the first read.
        scope.launch { store.changes.collectLatest { withContext(io) { readNow() } } }
    }

    /** Read again (a download job moved on): off the caller's thread. */
    fun reload() {
        scope.launch(io) { readNow() }
    }

    /** Reads the disk and publishes it. Blocking; never on the main thread. */
    fun readNow(): Snapshot = synchronized(this) {
        val list = runCatching { store.list() }.getOrDefault(emptyList())
        val snap = Snapshot(
            books = list,
            bytes = list.associate { it.key to runCatching { store.bytesOf(it.key) }.getOrDefault(0L) },
            usedBytes = runCatching { store.totalBytes() }.getOrDefault(0L),
            freeBytes = runCatching { store.usableBytes() }.getOrDefault(0L),
            loaded = true,
        )
        _state.value = snap
        snap
    }
}
