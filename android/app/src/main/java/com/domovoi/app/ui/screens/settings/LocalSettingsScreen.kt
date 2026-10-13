package com.domovoi.app.ui.screens.settings

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import com.domovoi.app.assistant.AssistantSupport
import com.domovoi.app.ui.screens.assistant.AssistantBenchmarkCard
import com.domovoi.app.ui.theme.Domovoi

/**
 * This phone's own settings, reachable with no server: the local-media
 * shell has no Settings route, so a gear in its top bar
 * ([LocalSettingsButton]) opens this instead. It holds only what needs no
 * server — the voice, the problem reports, the on-device assistant's
 * benchmark (64-bit installs only, [AssistantSupport]) and the licences —
 * the same cards Settings → Voice and About show when a Domovoi is
 * connected.
 */
@Composable
internal fun LocalSettingsScreen(
    onBack: () -> Unit,
    cards: @Composable () -> Unit = {
        VoiceCard()
        ProblemReportsCard()
        AssistantBenchmarkCard()
        LicencesCard()
    },
) {
    BackHandler(onBack = onBack)
    Column(
        Modifier
            .fillMaxSize()
            .background(Domovoi.colors.canvas)
            .windowInsetsPadding(WindowInsets.systemBars),
    ) {
        Row(
            Modifier.padding(start = 8.dp, top = 8.dp, end = 16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                Modifier
                    .clip(CircleShape)
                    .clickable(onClick = onBack)
                    .padding(10.dp),
            ) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "back", tint = Domovoi.colors.fg)
            }
            Column(Modifier.padding(start = 4.dp)) {
                Text("Settings", style = MaterialTheme.typography.titleLarge, color = Domovoi.colors.fg)
                Text(
                    (if (AssistantSupport.available) "This phone's voice, its problem reports and the on-device assistant. "
                    else "This phone's voice and its problem reports. ") +
                        "Server settings appear once a Domovoi is connected.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
        }
        // A few cards: a plain scrolling column (no lazy list to measure).
        Column(
            Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            cards()
        }
    }
}

/** The local shell's gear: opens [LocalSettingsScreen]. */
@Composable
internal fun LocalSettingsButton(onClick: () -> Unit) {
    Box(
        Modifier
            .clip(CircleShape)
            .clickable(onClick = onClick)
            .padding(6.dp),
    ) {
        Icon(
            Icons.Filled.Settings,
            contentDescription = "settings",
            tint = Domovoi.colors.fgMuted,
            modifier = Modifier.size(18.dp),
        )
    }
}
