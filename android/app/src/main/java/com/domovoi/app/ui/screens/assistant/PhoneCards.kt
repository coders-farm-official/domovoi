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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.domovoi.app.assistant.ActionKind
import com.domovoi.app.assistant.ActionTexts
import com.domovoi.app.assistant.AssistantController
import com.domovoi.app.assistant.CardKind
import com.domovoi.app.assistant.CardState
import com.domovoi.app.assistant.HandOffKind
import com.domovoi.app.assistant.HandOffs
import com.domovoi.app.assistant.PhoneCard
import com.domovoi.app.assistant.PhonePermissions
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.delay

/** What a card shows: its question, its two buttons (no second one when
 *  [secondary] is empty), and once it is done, the line that replaces the
 *  buttons. Kept apart from the drawing so the JVM tests read it. */
internal data class CardFace(val title: String, val primary: String, val secondary: String, val doneLine: String?)

/** Whether an Undo card still shows its button: for [AssistantController.UNDO_SHOWN_MS]
 *  after its reply ([atMs]), and only while it is open. */
internal fun undoShowing(card: PhoneCard, atMs: Long, nowMs: Long): Boolean =
    card.kind == CardKind.UNDO && card.state == CardState.OPEN && nowMs - atMs in 0 until AssistantController.UNDO_SHOWN_MS

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
                CardState.DISMISSED, CardState.REPLACED, CardState.FAILED -> "not now"
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
                CardState.DISMISSED, CardState.REPLACED, CardState.FAILED -> "not allowed: Domovoi won't ask again"
            },
        )
    }
    CardKind.ACTION -> card.action?.takeIf { it.kind != ActionKind.OTHER }?.let { a ->
        CardFace(
            title = card.title.orEmpty(),
            primary = ActionTexts.confirmLabel(a),
            secondary = "cancel",
            doneLine = when (card.state) {
                CardState.OPEN -> null
                CardState.DONE -> ActionTexts.doneLine(a)
                CardState.DISMISSED -> ActionTexts.DECLINED_LINE
                CardState.REPLACED -> "asked again below"
                CardState.FAILED -> if (a.kind == ActionKind.PLAY_VIDEO) "couldn't play it" else "the Clock app didn't take it"
            },
        )
    }
    CardKind.UNDO -> CardFace(
        title = "",
        primary = "undo",
        secondary = "",
        doneLine = if (card.state == CardState.DONE) ActionTexts.UNDO_LINE else null,
    )
}

/**
 * A hand-off or permission card under a phone chat's reply: nothing
 * happens until one of its buttons is tapped (the other app opens, or
 * Android's own permission dialog). A done card keeps its question and says
 * what happened.
 */
@Composable
internal fun PhoneCardView(card: PhoneCard, onPrimary: () -> Unit, onSecondary: () -> Unit, enabled: Boolean = true, atMs: Long = 0L) {
    val face = cardFace(card) ?: return
    if (card.kind == CardKind.UNDO) return UndoChip(card, face, atMs, onPrimary, enabled)
    DomovoiCard(Modifier.widthIn(max = 480.dp).padding(top = 6.dp)) {
        Text(face.title, style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg, modifier = Modifier.asHeading())
        val done = face.doneLine
        if (done == null) {
            Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = onPrimary, enabled = enabled) { Text(face.primary, color = Domovoi.colors.brand) }
                if (face.secondary.isNotEmpty()) {
                    TextButton(onClick = onSecondary, enabled = enabled) { Text(face.secondary, color = Domovoi.colors.fgMuted) }
                }
            }
        } else {
            Column(Modifier.padding(top = 4.dp).semantics { liveRegion = LiveRegionMode.Polite }) {
                Text(done, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgFaint)
            }
        }
    }
}

/**
 * The Undo under a reply whose action ran at once (N2): one button, shown
 * for ten seconds after the reply, then gone ("undo" said or typed still
 * works a little longer). Once used it says "undone".
 */
@Composable
private fun UndoChip(card: PhoneCard, face: CardFace, atMs: Long, onUndo: () -> Unit, enabled: Boolean) {
    var showing by remember(card, atMs) { mutableStateOf(undoShowing(card, atMs, System.currentTimeMillis())) }
    LaunchedEffect(card, atMs) {
        val left = atMs + AssistantController.UNDO_SHOWN_MS - System.currentTimeMillis()
        if (showing && left > 0) delay(left)
        showing = false
    }
    val done = face.doneLine
    when {
        done != null -> Text(done, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgFaint,
            modifier = Modifier.padding(top = 4.dp).semantics { liveRegion = LiveRegionMode.Polite })
        showing -> OutlinedButton(onClick = onUndo, enabled = enabled, modifier = Modifier.padding(top = 6.dp)) {
            Text(face.primary, color = Domovoi.colors.brand)
        }
    }
}
