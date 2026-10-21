package com.domovoi.app.ui.screens.assistant

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.domovoi.app.assistant.CardKind
import com.domovoi.app.assistant.CardState
import com.domovoi.app.assistant.HandOffKind
import com.domovoi.app.assistant.HandOffs
import com.domovoi.app.assistant.PhoneCard
import com.domovoi.app.assistant.PhonePermissions
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.theme.Domovoi

/** What a card shows: its question, its two buttons, and once it is done,
 *  the line that replaces the buttons. Kept apart from the drawing so the
 *  JVM tests read it. */
internal data class CardFace(val title: String, val primary: String, val secondary: String, val doneLine: String?)

internal fun cardFace(card: PhoneCard): CardFace? = when (card.kind) {
    CardKind.HANDOFF -> card.handOff?.takeIf { it.kind != HandOffKind.OTHER }?.let { h ->
        CardFace(
            title = HandOffs.cardTitle(h),
            primary = HandOffs.buttonLabel(h),
            secondary = "not now",
            doneLine = when (card.state) {
                CardState.OPEN -> null
                CardState.DONE -> when (h.kind) {
                    HandOffKind.SHARE -> "shared"
                    else -> "opened"
                }
                CardState.DISMISSED -> "not now"
            },
        )
    }
    CardKind.PERMISSION -> card.phonePermission?.let { p ->
        CardFace(
            title = PhonePermissions.title(p),
            primary = "allow",
            secondary = PhonePermissions.notNow(p),
            doneLine = when (card.state) {
                CardState.OPEN -> null
                CardState.DONE -> "allowed"
                CardState.DISMISSED -> "not allowed: Domovoi won't ask again"
            },
        )
    }
}

/**
 * A hand-off or permission card under a phone chat's reply: nothing
 * happens until one of its buttons is tapped (the other app opens, or
 * Android's own permission dialog). A done card keeps its question and says
 * what happened.
 */
@Composable
internal fun PhoneCardView(card: PhoneCard, onPrimary: () -> Unit, onSecondary: () -> Unit, enabled: Boolean = true) {
    val face = cardFace(card) ?: return
    DomovoiCard(Modifier.widthIn(max = 480.dp).padding(top = 6.dp)) {
        Text(face.title, style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg, modifier = Modifier.asHeading())
        val done = face.doneLine
        if (done == null) {
            Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = onPrimary, enabled = enabled) { Text(face.primary, color = Domovoi.colors.brand) }
                TextButton(onClick = onSecondary, enabled = enabled) { Text(face.secondary, color = Domovoi.colors.fgMuted) }
            }
        } else {
            Column(Modifier.padding(top = 4.dp).semantics { liveRegion = LiveRegionMode.Polite }) {
                Text(done, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgFaint)
            }
        }
    }
}
