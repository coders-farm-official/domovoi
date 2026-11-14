package com.domovoi.app.ui.screens.settings

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.calendarshare.CalendarShare
import com.domovoi.app.calendarshare.PhoneCalendar
import com.domovoi.app.calendarshare.PhoneCalendarReader
import com.domovoi.app.calendarshare.ShareEntry
import com.domovoi.app.calendarshare.ShareRules
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.net.LocalSharedScreen
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.time.Instant

/**
 * The prominent disclosure, shown above the switch BEFORE the phone's
 * permission prompt (Play's User Data policy: what is sent, to where, and
 * how to stop). android/play/DATA_SAFETY.md quotes it.
 */
internal fun calendarShareDisclosure(server: String): String =
    "Show events from the calendars you pick on this phone on your household calendar. " +
        "When this is on, Domovoi reads those calendars and sends a copy of each event's title, time and " +
        "place, and the calendar's name and colour, to your Domovoi server ($server). Only there: nothing " +
        "goes to Coders Farm or anyone else. Descriptions and guests stay on this phone. The copy is " +
        "read-only, updates every few hours and soon after a change, and is deleted from the server when " +
        "you turn this off."

internal const val CALENDAR_PERMISSION_DENIED =
    "Domovoi can't read this phone's calendars, so nothing is shared. To share them, allow Calendars " +
        "for Domovoi in the phone's settings, then turn this on again."

/** The settings line under the switch: when it last went, or why not. */
internal fun shareStatusLine(e: ShareEntry?, nowIso: (Long) -> String = { Instant.ofEpochMilli(it).toString() }): String? {
    if (e == null) return null
    if (!e.enabled) {
        if (!e.pendingDelete) return null
        return e.lastError?.let { "the house's copy isn't deleted yet: $it" } ?: "deleting the house's copy…"
    }
    e.lastError?.let { return it }
    if (e.calendarIds.isEmpty()) {
        return if (e.lastSentAtMs == null) "tick the calendars to share" else "no calendars ticked: the house shows none of them"
    }
    val sent = e.lastSentAtMs ?: return "sending soon…"
    val left = if (e.lastDropped > 0) " · ${e.lastDropped} events left out (too many to send)" else ""
    return "last sent ${relTime(nowIso(sent))}$left"
}

/** "Work · alex@example.com". */
internal fun phoneCalendarLabel(c: PhoneCalendar): Pair<String, String?> =
    c.name to c.accountName?.takeIf { it.isNotBlank() && it != c.name }

/**
 * Settings → Household → Phone calendars: "Share this phone's calendars
 * with the house". Off until the person turns it on; READ_CALENDAR is
 * asked for only then, after the disclosure above the switch. Not offered
 * on a shared screen, or before the phone is paired (there is no house to
 * share with).
 */
@Composable
internal fun PhoneCalendarsPanel() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val shared = LocalSharedScreen.current
    val serverUrl by app.prefs.serverUrl.collectAsState()
    val token by app.prefs.deviceToken.collectAsState()
    val server = ServerCredentials.normalize(serverUrl)
    val store = remember { CalendarShare.store(context) }
    val all by store.state.collectAsState()
    val entry = all[server]
    val reader = remember { PhoneCalendarReader(context.applicationContext) }
    var denied by remember { mutableStateOf(false) }
    var calendars by remember { mutableStateOf<List<PhoneCalendar>?>(null) }
    var reload by remember { mutableStateOf(0) }

    LaunchedEffect(entry?.enabled, reload) {
        calendars = if (entry?.enabled == true) withContext(Dispatchers.IO) { reader.calendars() } else null
    }
    // Back from the phone's settings: the permission may be there now.
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
        if (denied && reader.allowed()) denied = false
        reload++
    }

    fun turnOn() {
        denied = false
        store.update(server) { ShareRules.turnOn(it) }
        CalendarShare.sendSoon(context)
    }

    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) turnOn() else denied = true
    }

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            PanelCard("Share this phone's calendars with the house") {
                when {
                    shared -> Text(
                        "This is a shared screen, so it doesn't share calendars. Turn it on from your own phone.",
                        style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
                    )
                    server.isBlank() || token.isNullOrBlank() -> Text(
                        "Pair this phone with your Domovoi first; then its calendars can be shown at home.",
                        style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
                    )
                    else -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(
                            calendarShareDisclosure(app.prefs.serverLabel()),
                            style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
                        )
                        val on = entry?.enabled == true
                        Row(
                            Modifier.fillMaxWidth().heightIn(min = 44.dp).toggleable(value = on, role = Role.Switch) { want ->
                                if (!want) {
                                    store.update(server) { ShareRules.turnOff(it) }
                                    CalendarShare.sendSoon(context, delayMs = 0)
                                    toast("calendar sharing off")
                                } else if (reader.allowed()) {
                                    turnOn()
                                } else {
                                    ask.launch(Manifest.permission.READ_CALENDAR)
                                }
                            },
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Switch(checked = on, onCheckedChange = null)
                            Text(if (on) "on" else "off", style = MaterialTheme.typography.labelLarge,
                                color = Domovoi.colors.fg, modifier = Modifier.padding(start = 10.dp))
                        }
                        if (denied && !on) {
                            Text(CALENDAR_PERMISSION_DENIED, style = MaterialTheme.typography.bodySmall,
                                color = Domovoi.colors.warn)
                            TextButton(onClick = {
                                runCatching {
                                    context.startActivity(
                                        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
                                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                                    )
                                }.onFailure { toast("couldn't open the phone's settings") }
                            }) { Text("open the phone's settings", color = Domovoi.colors.brand) }
                        }
                        shareStatusLine(entry)?.let { line ->
                            val bad = entry?.lastError != null
                            Text(line, style = MaterialTheme.typography.labelMedium,
                                color = if (bad) Domovoi.colors.warn else Domovoi.colors.fgMuted)
                        }
                    }
                }
            }
        }
        val list = calendars
        if (!shared && entry?.enabled == true && !token.isNullOrBlank()) {
            item {
                PanelCard("calendars to share", "Only the ticked ones go to the house.") {
                    when {
                        list == null -> Text(
                            if (reader.allowed()) "reading the phone's calendars…" else CALENDAR_PERMISSION_DENIED,
                            style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
                        )
                        list.isEmpty() -> Text(
                            "There are no calendars on this phone. Add an account with a calendar in the phone's settings.",
                            style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
                        )
                        else -> Column {
                            val ticked = entry.calendarIds.toSet()
                            list.forEach { c -> CalendarRow(c, c.id in ticked) { want ->
                                store.update(server) { ShareRules.toggle(it, c.id, want) }
                                CalendarShare.sendSoon(context)
                            } }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun CalendarRow(c: PhoneCalendar, checked: Boolean, onChange: (Boolean) -> Unit) {
    val (name, account) = phoneCalendarLabel(c)
    Row(
        Modifier.fillMaxWidth().heightIn(min = 44.dp).toggleable(value = checked, role = Role.Checkbox, onValueChange = onChange),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Checkbox(checked = checked, onCheckedChange = null)
        // The calendar's own colour, as a dot only: the one accent stays amber.
        Box(
            Modifier.padding(start = 8.dp, end = 8.dp).size(10.dp)
                .background(c.color?.let { Color(it or 0xFF000000.toInt()) } ?: Domovoi.colors.idle, CircleShape),
        )
        Column(Modifier.weight(1f)) {
            Text(name, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            account?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}
