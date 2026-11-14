package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.unit.dp
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.theme.Domovoi

/*
 * "Why this song?" on an Up next pick (1.3.0; the web's UpNextWhy in
 * web/static/components.jsx). The chip is the pick's short reason
 * (`reason_label`: "same artist", "90s"); with a `why` — the server's plain
 * sentence from the signals the picker really used ("same artist as the
 * last song · not played in 4 months") — the chip is a button that shows
 * or hides that sentence as one muted line under the row, and never starts
 * the row's own action (play now). No `why` (a pick from before 1.3.0):
 * just the chip. The phone never words a reason itself: the line is the
 * server's text, as it came.
 */

/** TalkBack's name for the chip that shows the reason. */
internal const val WHY_THIS_SONG = "why this song?"

/** What a pick's chip is: [text] on the chip; [why], when set, the line a tap shows. */
internal data class WhyChip(val text: String, val why: String?) {
    val tappable: Boolean get() = why != null
    /** TalkBack's name for it (the web's aria-label). */
    val description: String get() = if (tappable) "$WHY_THIS_SONG $text".trim() else text
}

/** The chip for a pick with [label] and [why]; null when it has neither. */
internal fun whyChip(label: String?, why: String?): WhyChip? {
    val l = label?.takeIf { it.isNotBlank() }
    val w = why?.takeIf { it.isNotBlank() }
    if (l == null && w == null) return null
    return WhyChip(text = l ?: "why?", why = w)
}

/** The line under the row: the server's sentence exactly, only while open. */
internal fun whyLine(chip: WhyChip?, open: Boolean): String? = if (open) chip?.why else null

@Composable
internal fun UpNextWhyChip(chip: WhyChip, open: Boolean, onToggle: () -> Unit) {
    if (!chip.tappable) {
        Pill(chip.text)
        return
    }
    Box(
        Modifier
            .minimumInteractiveComponentSize()
            .clickable(role = Role.Button, onClickLabel = if (open) "hide why" else "show why", onClick = onToggle)
            .semantics {
                contentDescription = chip.description
                stateDescription = if (open) "shown" else "hidden"
            },
        contentAlignment = Alignment.Center,
    ) {
        Pill(chip.text)
    }
}

@Composable
internal fun UpNextWhyLine(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        color = Domovoi.colors.fgMuted,
        modifier = modifier
            .background(Domovoi.colors.borderSoft.copy(alpha = 0.35f), RoundedCornerShape(6.dp))
            .padding(horizontal = 8.dp, vertical = 4.dp)
            .semantics { liveRegion = LiveRegionMode.Polite },
    )
}

/** A room queue entry's chip: only an Up next pick has one. */
internal fun roomWhyChip(item: QueueItem): WhyChip? =
    if (item.isPick) whyChip(item.reasonLabel, item.why) else null
