package com.domovoi.app.ui.components

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.ui.theme.Domovoi
import kotlin.math.abs

// ---------------------------------------------------------------------------
// Status + labels — parity with web components.jsx (Pill, StatusDot, ...)
// ---------------------------------------------------------------------------

enum class Tone { Ok, Warn, Err, Idle, Brand }

@Composable
fun toneColor(tone: Tone): Color = when (tone) {
    Tone.Ok -> Domovoi.colors.ok
    Tone.Warn -> Domovoi.colors.warn
    Tone.Err -> Domovoi.colors.err
    Tone.Idle -> Domovoi.colors.idle
    Tone.Brand -> Domovoi.colors.brand
}

@Composable
fun toneSoft(tone: Tone): Color = when (tone) {
    Tone.Ok -> Domovoi.colors.okSoft
    Tone.Warn -> Domovoi.colors.warnSoft
    Tone.Err -> Domovoi.colors.errSoft
    Tone.Idle -> Domovoi.colors.idleSoft
    Tone.Brand -> Domovoi.colors.brandSoft
}

/** Live things pulse. Idle things don't. */
@Composable
fun StatusDot(tone: Tone, live: Boolean = false, modifier: Modifier = Modifier) {
    val color = toneColor(tone)
    Box(modifier = modifier.size(10.dp), contentAlignment = Alignment.Center) {
        if (live) {
            val t = rememberInfiniteTransition(label = "pulse")
            val s by t.animateFloat(
                initialValue = 1f, targetValue = 2.4f,
                animationSpec = infiniteRepeatable(tween(1600), RepeatMode.Restart),
                label = "halo",
            )
            Box(
                Modifier.size(8.dp).scale(s)
                    .background(color.copy(alpha = (0.4f * (2.4f - s) / 1.4f).coerceIn(0f, 0.4f)), CircleShape)
            )
        }
        Box(Modifier.size(8.dp).background(color, CircleShape))
    }
}

@Composable
fun Pill(text: String, tone: Tone = Tone.Idle, live: Boolean = false, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier
            .background(toneSoft(tone), RoundedCornerShape(999.dp))
            .padding(horizontal = 8.dp, vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(5.dp),
    ) {
        if (live) StatusDot(tone, live = true)
        Text(
            text.lowercase(),
            style = MaterialTheme.typography.labelMedium,
            color = toneColor(tone),
            maxLines = 1,
        )
    }
}

@Composable
fun RoomChip(roomId: String?, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(999.dp))
            .padding(horizontal = 8.dp, vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(5.dp),
    ) {
        Box(Modifier.size(6.dp).background(Domovoi.colors.brand, CircleShape))
        Text(
            (roomId ?: "—").lowercase(),
            style = MaterialTheme.typography.labelMedium,
            color = Domovoi.colors.fgMuted,
            maxLines = 1, overflow = TextOverflow.Ellipsis,
        )
    }
}

/** Deterministic gradient avatar from a name's first letter (web Avatar). */
@Composable
fun AvatarBubble(name: String?, size: Int = 32) {
    val initial = name?.trim()?.firstOrNull()?.uppercase() ?: "?"
    val hues = listOf(
        Color(0xFFF2A618) to Color(0xFFE47318),
        Color(0xFF54BF5C) to Color(0xFF2E8B57),
        Color(0xFF5B8DEF) to Color(0xFF3A5FC0),
        Color(0xFFB86BD9) to Color(0xFF8A4BAF),
        Color(0xFFE4657C) to Color(0xFFB43A55),
    )
    val pair = hues[abs((name ?: "?").hashCode()) % hues.size]
    Box(
        Modifier.size(size.dp)
            .background(Brush.linearGradient(listOf(pair.first, pair.second)), CircleShape),
        contentAlignment = Alignment.Center,
    ) {
        Text(initial, color = Color.White, fontWeight = FontWeight.SemiBold,
            style = MaterialTheme.typography.labelLarge)
    }
}

// ---------------------------------------------------------------------------
// Cards / layout
// ---------------------------------------------------------------------------

@Composable
fun DomovoiCard(
    modifier: Modifier = Modifier,
    padding: Int = 16,
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(10.dp),
        color = Domovoi.colors.card,
        border = androidx.compose.foundation.BorderStroke(1.dp, Domovoi.colors.border),
    ) {
        Column(Modifier.padding(padding.dp), content = content)
    }
}

@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text.lowercase(),
        modifier = modifier,
        style = MaterialTheme.typography.labelSmall,
        color = Domovoi.colors.fgMuted,
    )
}

@Composable
fun PageHeader(
    title: String,
    sub: String? = null,
    modifier: Modifier = Modifier,
    actions: (@Composable () -> Unit)? = null,
) {
    Row(
        modifier = modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.headlineLarge, color = Domovoi.colors.fg)
            if (sub != null) {
                Text(sub, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted)
            }
        }
        if (actions != null) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                actions()
            }
        }
    }
}

@Composable
fun Stat(label: String, value: String, sub: String? = null, modifier: Modifier = Modifier) {
    DomovoiCard(modifier = modifier) {
        SectionLabel(label)
        Text(
            value,
            style = MaterialTheme.typography.headlineLarge,
            color = Domovoi.colors.fg,
            modifier = Modifier.padding(top = 4.dp),
        )
        if (sub != null) {
            Text(sub, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgSubtle)
        }
    }
}

// ---------------------------------------------------------------------------
// Empty / loading states
// ---------------------------------------------------------------------------

@Composable
fun EmptyState(
    title: String,
    sub: String? = null,
    modifier: Modifier = Modifier,
    action: (@Composable () -> Unit)? = null,
) {
    Column(
        modifier = modifier.fillMaxWidth().padding(vertical = 40.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        SleepingDomovoi()
        Text(title, style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fgMuted)
        if (sub != null) {
            Text(sub, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgSubtle)
        }
        if (action != null) {
            Box(Modifier.padding(top = 6.dp)) { action() }
        }
    }
}

@Composable
fun LoadingState(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().padding(vertical = 40.dp), contentAlignment = Alignment.Center) {
        androidx.compose.material3.CircularProgressIndicator(color = Domovoi.colors.brand)
    }
}

@Composable
fun ErrorState(message: String, onRetry: (() -> Unit)? = null, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxWidth().padding(vertical = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text("couldn't reach the server", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fgMuted)
        Text(message, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgSubtle)
        if (onRetry != null) {
            androidx.compose.material3.TextButton(onClick = onRetry) { Text("retry") }
        }
    }
}
