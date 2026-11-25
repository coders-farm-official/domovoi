package com.domovoi.app.roommode

import android.content.Intent
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.theme.Domovoi

/** Settings → Rooms: "Use this phone in a room" — opens room mode's
 *  explainer (roommode/RoomSetupActivity.kt) in its own task. Household
 *  words: a room is a place and this phone one device in it; the phone
 *  never says "satellite" ([RoomStatusText.ENTRY_TITLE]). */
@Composable
fun RoomModeEntryCard() {
    val context = LocalContext.current
    DomovoiCard {
        SectionLabel("this phone")
        Spacer(Modifier.height(6.dp))
        Text(RoomStatusText.ENTRY_TITLE, style = MaterialTheme.typography.titleMedium)
        Text(
            "A spare phone or tablet on its charger can listen and speak in a room, on its own or beside the room's " +
                "other devices. The normal app is locked while it does.",
            style = MaterialTheme.typography.bodyMedium,
            color = Domovoi.colors.fgMuted,
        )
        Spacer(Modifier.height(10.dp))
        OutlinedButton(onClick = {
            context.startActivity(Intent(context, RoomSetupActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }) { Text(RoomStatusText.ENTRY_TITLE) }
    }
}
