package com.domovoi.app.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.grid.LazyGridState
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.net.DEFAULT_PAGE_SIZE
import com.domovoi.app.net.Page
import com.domovoi.app.net.PageRequest
import com.domovoi.app.net.PagedState
import com.domovoi.app.net.Pager
import com.domovoi.app.net.Paging
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.filter

/**
 * A [Pager] for this composition: page one is read when it first appears and
 * again whenever one of [keys] changes (a filter, a search, a folder — every
 * one of them is a new list) or the server does. [eventTypes] are the WS
 * events after which the loaded rows are re-read in place (debounced: an
 * indexer sweep sends a burst). The web twin is data.js usePagedList.
 */
@Composable
fun <T> rememberPager(
    vararg keys: Any?,
    pageSize: Int = DEFAULT_PAGE_SIZE,
    keyOf: (T) -> Any,
    eventTypes: Set<String> = emptySet(),
    fetch: suspend (AppContainer, PageRequest) -> Page<T>,
): Pager<T> {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    val latestFetch by rememberUpdatedState(fetch)
    val latestKey by rememberUpdatedState(keyOf)
    val pager = remember { Pager<T>(scope, pageSize, { latestKey(it) }) { req -> latestFetch(app, req) } }
    LaunchedEffect(app.prefs.serverUrl.value, *keys) { pager.reset() }
    if (eventTypes.isNotEmpty()) {
        LaunchedEffect(eventTypes, pager) {
            app.bus.events.filter { it.type in eventTypes }.collectLatest {
                delay(400)
                pager.reload()
            }
        }
    }
    return pager
}

/**
 * Whether the last visible item is within [threshold] of the end. A list
 * shorter than twice the threshold is not "near its end" yet: that is a
 * screen still showing its header and a spinner, whose layout the first
 * page's arrival hasn't replaced — read then, it asked for page two before
 * anyone scrolled (seen on the Music page, 2026-10-12).
 */
internal fun nearEnd(totalItems: Int, lastVisible: Int, threshold: Int): Boolean =
    totalItems >= 2 * threshold && lastVisible >= totalItems - 1 - threshold

/**
 * Ask for the next page as the list nears its end: within [threshold] items
 * of the last one. Asks again after each page lands while the end is still
 * in reach (a page that didn't fill the screen), never while one is in
 * flight or after a failure (the footer's retry owns that).
 */
@Composable
fun LoadMoreNearEnd(state: LazyListState, pager: Pager<*>, threshold: Int = 8) {
    LaunchedEffect(state, pager) {
        snapshotFlow {
            val info = state.layoutInfo
            val last = info.visibleItemsInfo.lastOrNull()?.index ?: -1
            nearEnd(info.totalItemsCount, last, threshold)
        }.combine(pager.state) { near, s -> near && s.canLoadMore }
            .distinctUntilChanged()
            .collect { if (it) pager.loadMore() }
    }
}

@Composable
fun LoadMoreNearEnd(state: LazyGridState, pager: Pager<*>, threshold: Int = 12) {
    LaunchedEffect(state, pager) {
        snapshotFlow {
            val info = state.layoutInfo
            val last = info.visibleItemsInfo.lastOrNull()?.index ?: -1
            nearEnd(info.totalItemsCount, last, threshold)
        }.combine(pager.state) { near, s -> near && s.canLoadMore }
            .distinctUntilChanged()
            .collect { if (it) pager.loadMore() }
    }
}

/**
 * The end of a paged list: "showing N of M", a spinner while a page is on
 * its way, a retry when one failed, and a plain "load more" for whoever
 * doesn't scroll (TalkBack, a keyboard). Nothing at all for an empty list —
 * the screen's own empty state speaks then.
 */
@Composable
fun PagedFooter(
    state: PagedState<*>,
    noun: String,
    onRetry: () -> Unit,
    onLoadMore: () -> Unit,
    modifier: Modifier = Modifier,
) {
    if (state.items.isEmpty() && !state.loading && state.error == null) return
    Row(
        modifier.fillMaxWidth().padding(vertical = 10.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp, Alignment.CenterHorizontally),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (state.items.isNotEmpty() || state.total != null) {
            Text(
                Paging.countText(state.items.size, state.total, noun),
                style = MaterialTheme.typography.labelSmall,
                fontFamily = FontFamily.Monospace,
                color = Domovoi.colors.fgMuted,
            )
        }
        when {
            state.loading -> CircularProgressIndicator(
                color = Domovoi.colors.brand, strokeWidth = 2.dp, modifier = Modifier.size(16.dp),
            )
            state.error != null -> {
                Text(
                    if (state.items.isEmpty()) "couldn't load $noun" else "couldn't load the next page",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fg,
                )
                TextButton(onClick = onRetry) { Text("retry", color = Domovoi.colors.brand) }
            }
            state.hasMore -> TextButton(onClick = onLoadMore) {
                Text("load more", color = Domovoi.colors.brand)
            }
        }
    }
}
