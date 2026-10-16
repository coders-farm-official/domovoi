package com.domovoi.app.ui.screens.settings

import android.widget.Toast
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.data.DeveloperOptions
import com.domovoi.app.data.VersionTaps
import com.domovoi.app.data.appVersionText
import com.domovoi.app.ui.screens.assistant.AssistantBenchmarkCard
import com.domovoi.app.ui.theme.Domovoi

/*
 * Developer options in Settings (data/DeveloperOptions.kt): the app version
 * line that turns them on with seven taps, and the section that holds the
 * developer tools, with the switch that turns them off. Both in Settings →
 * About and in the no-server settings (LocalSettingsScreen.kt).
 */

/** What the section's card says it holds. */
internal const val DEVELOPER_OPTIONS_SUB =
    "Tools for measuring the app on this phone, not needed for everyday use: the on-device " +
        "assistant benchmark below, a reply's timings in the assistant's long-press menu, and the " +
        "last stop's timing in its model settings."

/** Under the section's switch: how to get back. */
internal const val DEVELOPER_OPTIONS_OFF_HINT =
    "Off hides these tools again. Tapping the app version seven times brings them back."

/**
 * "app version 1.0.0-rc1": the app's own version (the server's is on the
 * dashboard). Seven taps turn developer options on ([VersionTaps]); from
 * the third, a toast says how many are left. Plain text where developer
 * options have nothing to show ([DeveloperOptions.available]).
 *
 * For a screen reader it is one control, labelled by its words; the
 * countdown is a system toast, which TalkBack reads out.
 */
@Composable
internal fun AppVersionLine(
    options: DeveloperOptions = LocalApp.current.developerOptions,
    versionName: String? = rememberVersionName(),
    say: (String) -> Unit = rememberOneToast(),
) {
    val words = appVersionText(versionName, options.debugBuild)
    val style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace)
    if (!options.available) {
        Text(words, style = style, color = Domovoi.colors.fgFaint)
        return
    }
    // A new count each time the line is shown, as Android's build number.
    val taps = remember(options) { VersionTaps(options) }
    Box(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp)
            .clickable { taps.tap().message?.let(say) },
        contentAlignment = Alignment.CenterStart,
    ) {
        Text(words, style = style, color = Domovoi.colors.fgFaint)
    }
}

/** The no-server settings' home for the app version line (Settings →
 *  About carries it in its About Domovoi card). */
@Composable
internal fun AboutThisAppCard() {
    PanelCard("About this app") {
        AppVersionLine()
    }
}

/**
 * The developer tools, only while developer options are on: a card with
 * the switch that turns them off, then the tools ([tools]: the on-device
 * assistant benchmark). Nothing at all while they are off.
 */
@Composable
internal fun DeveloperOptionsSection(
    options: DeveloperOptions = LocalApp.current.developerOptions,
    say: (String) -> Unit = rememberOneToast(),
    tools: @Composable () -> Unit = { AssistantBenchmarkCard() },
) {
    val on by options.on.collectAsState()
    if (!on) return
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        PanelCard("Developer options", DEVELOPER_OPTIONS_SUB) {
            // One control for a screen reader: the row toggles, labelled by its text.
            Row(
                Modifier
                    .fillMaxWidth()
                    .heightIn(min = 48.dp)
                    .toggleable(value = true, role = Role.Switch, onValueChange = { keep ->
                        if (!keep) {
                            options.turnOff()
                            say(DeveloperOptions.OFF_TEXT)
                        }
                    }),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Switch(checked = true, onCheckedChange = null)
                Column(Modifier.padding(start = 10.dp)) {
                    Text("use developer options", style = MaterialTheme.typography.labelLarge, color = Domovoi.colors.fg)
                    Text(DEVELOPER_OPTIONS_OFF_HINT, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
                }
            }
        }
        tools()
    }
}

/** This install's versionName, read once. */
@Composable
private fun rememberVersionName(): String? {
    val context = LocalContext.current
    return remember(context) {
        runCatching { context.packageManager.getPackageInfo(context.packageName, 0).versionName }.getOrNull()
    }
}

/** A system toast, one at a time as Android's own countdown: each replaces
 *  the one before instead of queueing behind it. */
@Composable
private fun rememberOneToast(): (String) -> Unit {
    val context = LocalContext.current.applicationContext
    return remember(context) {
        var last: Toast? = null
        { message: String ->
            last?.cancel()
            last = Toast.makeText(context, message, Toast.LENGTH_SHORT).also { it.show() }
        }
    }
}
