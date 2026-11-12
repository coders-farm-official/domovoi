package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.QueueMusic
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.ui.theme.Domovoi

/*
 * A room's queue, one tap from the room (batch 2 §2): Home's room rows,
 * Music's room cards and the room's own settings each carry a queue button
 * that opens this sheet — the same rows, edits and search box as Music's
 * "room queue" tab, for that one room.
 */

/** The sheet for [room]'s queue. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun RoomQueueSheet(room: String, onDismiss: () -> Unit) {
    val rq = rememberRoomQueue(listOf(room), room)
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = Domovoi.colors.card,
    ) {
        LazyColumn(
            Modifier.fillMaxWidth(),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            queueTab(listOf(room), rq, picker = false)
        }
    }
    RoomQueueDialogs(rq)
}

/**
 * The queue button a room shows, 48dp, "<room>'s queue" to TalkBack. Hidden
 * on a phone that isn't paired: the room's queue is a household read.
 */
@Composable
internal fun RoomQueueButton(room: String, onOpen: () -> Unit) {
    val app = LocalApp.current
    val token by app.prefs.deviceToken.collectAsState()
    if (token.isNullOrBlank()) return
    IconButton(onClick = onOpen) {
        Icon(Icons.AutoMirrored.Filled.QueueMusic, contentDescription = roomQueueLabel(room), tint = Domovoi.colors.fgMuted)
    }
}

/** TalkBack's name for a room's queue button. */
internal fun roomQueueLabel(room: String) = "$room's queue"
