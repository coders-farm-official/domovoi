package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DragIndicator
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import com.domovoi.app.ui.theme.Domovoi
import kotlin.math.roundToInt

/*
 * Drag to reorder a queue (the web's draggable rows). A 48dp handle on each
 * row: hold, then drag; the row follows the finger and lands where it is let
 * go. Holding first keeps a plain swipe for scrolling the list. TalkBack
 * gets "move up" / "move down" actions on the handle instead of the gesture.
 */

/** Where a row dragged from [from] by [offsetPx] lands, rows being [rowPx] tall. */
internal fun dragTarget(from: Int, offsetPx: Float, rowPx: Float, count: Int): Int {
    if (count <= 0) return 0
    val steps = if (rowPx > 0f) (offsetPx / rowPx).roundToInt() else 0
    return (from + steps).coerceIn(0, count - 1)
}

/** One list's drag in progress: which row, how far. */
@Stable
internal class QueueDragState {
    var from by mutableStateOf<Int?>(null)
    var offsetPx by mutableFloatStateOf(0f)
    var rowPx by mutableFloatStateOf(0f)

    /** The dragged row's modifier: lifted, following the finger. */
    fun rowModifier(i: Int): Modifier =
        if (from == i) Modifier.zIndex(1f).graphicsLayer { translationY = offsetPx } else Modifier
}

@Composable
internal fun QueueDragHandle(
    state: QueueDragState,
    index: Int,
    count: Int,
    title: String,
    onMove: (from: Int, to: Int) -> Unit,
) {
    val move by rememberUpdatedState(onMove)
    val n by rememberUpdatedState(count)
    Box(
        Modifier
            .size(48.dp)
            .pointerInput(index) {
                detectDragGesturesAfterLongPress(
                    onDragStart = { state.from = index; state.offsetPx = 0f },
                    onDrag = { change, amount -> change.consume(); state.offsetPx += amount.y },
                    onDragEnd = {
                        val to = dragTarget(index, state.offsetPx, state.rowPx, n)
                        state.from = null; state.offsetPx = 0f
                        if (to != index) move(index, to)
                    },
                    onDragCancel = { state.from = null; state.offsetPx = 0f },
                )
            }
            .semantics {
                contentDescription = "move $title"
                customActions = buildList {
                    if (index > 0) add(CustomAccessibilityAction("move up") { move(index, index - 1); true })
                    if (index < count - 1) add(CustomAccessibilityAction("move down") { move(index, index + 1); true })
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        Icon(Icons.Filled.DragIndicator, contentDescription = null, tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(20.dp))
    }
}
