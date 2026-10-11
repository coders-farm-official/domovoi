package com.domovoi.app.ui.shell

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.alerts.shellBanner
import com.domovoi.app.net.LocalSharedScreen
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.toneColor
import com.domovoi.app.ui.components.toneSoft
import com.domovoi.app.ui.theme.Domovoi

/**
 * The container guard's banner (the web's ContainerGuardBanner), a thin
 * full-width strip at the top of the content area in every shell, above
 * whatever screen shows, while something needs an admin: red for tampering
 * and errors, amber for a warning. Its words are the server's banner, or on
 * a shared screen only "Something needs an admin's attention."
 * (alerts/ContainerIncidents.kt shellBanner). Tapping it, or its "details",
 * opens the Containers screen; there, it has no "details" to offer.
 *
 * In a window the keyboard has crowded (a landscape phone) it steps aside
 * with the rest of the chrome (see TopChrome), and is back the moment the
 * keyboard closes.
 */
@Composable
internal fun ContainerGuardBanner(route: Route, navigate: (Route) -> Unit) {
    val app = LocalApp.current
    val state by app.containerAlerts.state.collectAsState()
    val banner = shellBanner(state, LocalSharedScreen.current)
    if (!banner.show || keyboardCrowdsTheWindow()) return
    val tone = if (banner.tone?.red == false) Tone.Warn else Tone.Err
    val here = route == Route.Containers
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier
                .fillMaxWidth()
                .background(toneSoft(tone))
                .then(
                    if (here) Modifier
                    else Modifier.clickable(onClickLabel = "container details") { navigate(Route.Containers) },
                )
                .semantics { liveRegion = LiveRegionMode.Polite }
                .heightIn(min = 40.dp)
                .padding(start = 16.dp, end = 8.dp, top = 6.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            StatusDot(tone, live = true)
            Text(
                banner.text,
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fg,
                maxLines = 3,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            if (!here) {
                Text(
                    "details",
                    style = MaterialTheme.typography.labelLarge,
                    color = toneColor(tone),
                    modifier = Modifier.padding(horizontal = 8.dp),
                )
            }
        }
        Box(Modifier.fillMaxWidth().height(1.dp).background(toneColor(tone).copy(alpha = 0.35f)))
    }
}
