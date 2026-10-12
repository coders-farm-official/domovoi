package com.domovoi.app.ui.screens.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.domovoi.app.ui.components.AvatarBubble
import com.domovoi.app.ui.theme.Domovoi

/*
 * Who Domovoi thinks said a voice message (owner decisions 2026-10-08): the
 * phone belongs to a person, but voice identification still runs, and a
 * small marker on each spoken message names who it heard. The marker is
 * softer when the match was weak, or when it names someone other than the
 * phone's owner (Settings → Connection → "This phone belongs to"). The
 * decision is pure and unit-tested; the composable only draws it.
 */

/** What a voice message's marker says, and how strongly. */
internal data class SpeakerMark(
    /** Read out under the message: "Ana", "maybe Ana", "voice not recognized". */
    val label: String,
    /** The person's name for the avatar; null when nobody was recognized. */
    val name: String?,
    /** Drawn softer: a weak match, someone other than the owner, or nobody. */
    val soft: Boolean,
    /** What a screen reader says. */
    val description: String,
)

/**
 * The marker for a message, or null when it needs none (anything typed, or
 * Domovoi's own reply). [ownerId] is the phone's owner on this server (`Prefs.owners`).
 */
internal fun speakerMark(
    source: String?,
    role: String,
    personId: Long?,
    name: String?,
    confident: Boolean,
    ownerId: String?,
): SpeakerMark? {
    if (source != "voice" || role != "user") return null
    if (personId == null) {
        return SpeakerMark("voice not recognized", null, soft = true, description = "spoken; Domovoi didn't recognize the voice")
    }
    val who = name?.takeIf { it.isNotBlank() } ?: "someone it knows"
    val notOwner = ownerId != null && ownerId != personId.toString()
    return when {
        !confident -> SpeakerMark("maybe $who", who, soft = true, description = "spoken; Domovoi thinks it was $who, but isn't sure")
        notOwner -> SpeakerMark(who, who, soft = true, description = "spoken by $who, not this phone's owner")
        else -> SpeakerMark(who, who, soft = false, description = "spoken by $who")
    }
}

@Composable
internal fun SpeakerMarker(mark: SpeakerMark, modifier: Modifier = Modifier) {
    Row(
        modifier
            .padding(top = 3.dp)
            .alpha(if (mark.soft) 0.55f else 1f)
            .semantics(mergeDescendants = true) { contentDescription = mark.description },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Icon(
            Icons.Outlined.Mic, contentDescription = null,
            tint = Domovoi.colors.fgFaint, modifier = Modifier.size(12.dp),
        )
        if (mark.name != null) AvatarBubble(mark.name, size = 16)
        Text(
            mark.label,
            style = MaterialTheme.typography.labelSmall,
            color = if (mark.soft) Domovoi.colors.fgFaint else Domovoi.colors.fgMuted,
        )
    }
}
