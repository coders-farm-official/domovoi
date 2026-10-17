package com.domovoi.app.ui.components

import androidx.compose.foundation.layout.BoxScope
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/**
 * The state-socket event a filesystem library sends when it changed: an
 * upload, a delete, a move, a file written by another process into a folder
 * someone is viewing (web/backend/api/listing_cache.py). Videos and Files
 * re-read on it; the web twin listens for the same type.
 */
const val FILES_CHANGED = "files.changed"

/**
 * One refresh of a library screen (Videos, Files, Podcasts, Audiobooks), shared
 * by its header button and its pull-to-refresh so both say "busy" together and a
 * second press while one runs does nothing. [run] is what the screen's refresh
 * means: the server's rescan where one exists (Audiobooks' reindex), else a
 * re-read of the list from disk (`pager.reload(fresh = true)` then `idle()`).
 */
class LibraryRefresh(private val scope: CoroutineScope, private val run: suspend () -> Unit) {
    var busy by mutableStateOf(false)
        private set

    fun refresh() {
        if (busy) return
        busy = true
        scope.launch {
            try { run() } finally { busy = false }
        }
    }
}

@Composable
fun rememberLibraryRefresh(run: suspend () -> Unit): LibraryRefresh {
    val scope = rememberCoroutineScope()
    val latest by rememberUpdatedState(run)
    return remember { LibraryRefresh(scope) { latest() } }
}

/** The header's refresh button, as Music's "rescan library" one. */
@Composable
fun LibraryRefreshButton(refresh: LibraryRefresh, description: String) {
    IconButton(onClick = { refresh.refresh() }, enabled = !refresh.busy) {
        Icon(Icons.Filled.Refresh, contentDescription = description, tint = Domovoi.colors.fgMuted)
    }
}

/**
 * Pull-to-refresh around a library's list (material3 [PullToRefreshBox]). The
 * pull runs [refresh]; the indicator stays while it is busy. The list inside
 * must scroll for a pull to register, so an empty screen keeps the button.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LibraryRefreshBox(
    refresh: LibraryRefresh,
    modifier: Modifier = Modifier,
    content: @Composable BoxScope.() -> Unit,
) {
    PullToRefreshBox(
        isRefreshing = refresh.busy,
        onRefresh = { refresh.refresh() },
        modifier = modifier,
        content = content,
    )
}
