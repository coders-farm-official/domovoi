package com.domovoi.app.ui.screens.videos

import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.grid.LazyGridState
import androidx.compose.runtime.withFrameNanos

/**
 * Scroll a vertical grid so item [index] sits as close to the middle of the
 * viewport as the grid allows: jump to it (which puts it at the top), wait
 * a frame for the layout, then scroll back by however far its centre is
 * from the viewport's. The grid clamps at either end on its own.
 */
internal suspend fun LazyGridState.centerOn(index: Int) {
    if (index < 0) return
    scrollToItem(index)
    withFrameNanos { }
    val info = layoutInfo.visibleItemsInfo.firstOrNull { it.index == index } ?: return
    val delta = centerScrollDelta(
        info.offset.y, info.size.height, layoutInfo.viewportStartOffset, layoutInfo.viewportEndOffset,
    )
    if (delta != 0) scrollBy(delta.toFloat())
}

/** Same as [LazyGridState.centerOn], for a list (the horizontal "recently played" strip). */
internal suspend fun LazyListState.centerOn(index: Int) {
    if (index < 0) return
    scrollToItem(index)
    withFrameNanos { }
    val info = layoutInfo.visibleItemsInfo.firstOrNull { it.index == index } ?: return
    val delta = centerScrollDelta(
        info.offset, info.size, layoutInfo.viewportStartOffset, layoutInfo.viewportEndOffset,
    )
    if (delta != 0) scrollBy(delta.toFloat())
}
