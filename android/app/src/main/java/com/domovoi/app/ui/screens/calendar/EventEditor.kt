package com.domovoi.app.ui.screens.calendar

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Notes
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Place
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi
import java.time.Duration
import java.time.Instant
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.Locale

// ---------------------------------------------------------------------------
// Event detail (side rail on wide widths, bottom sheet content on compact).
// ---------------------------------------------------------------------------

@Composable
internal fun EventDetailContent(
    event: CalendarEvent,
    onClose: () -> Unit,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
) {
    Column(
        Modifier.fillMaxWidth().padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Box(
                Modifier
                    .width(4.dp)
                    .height(46.dp)
                    .clip(RoundedCornerShape(2.dp))
                    .background(if (event.isGoogle()) Domovoi.colors.idle else Domovoi.colors.brand),
            )
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(
                    event.title,
                    style = MaterialTheme.typography.titleMedium,
                    color = Domovoi.colors.fg,
                )
                Text(
                    fmtDateLong(event.startLocal().toLocalDate()),
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted,
                )
                if (event.ends_at != null) {
                    Text(
                        fmtRange(event),
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted,
                    )
                }
            }
            IconButton(onClick = onClose) {
                Icon(
                    Icons.Filled.Close,
                    contentDescription = "close",
                    modifier = Modifier.size(16.dp),
                    tint = Domovoi.colors.fgMuted,
                )
            }
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)

        val loc = event.location
        if (!loc.isNullOrBlank()) {
            Row(
                verticalAlignment = Alignment.Top,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Icon(
                    Icons.Filled.Place,
                    contentDescription = null,
                    modifier = Modifier.size(14.dp),
                    tint = Domovoi.colors.fgMuted,
                )
                Text(loc, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fg)
            }
        }
        val desc = event.description
        if (!desc.isNullOrBlank()) {
            Row(
                verticalAlignment = Alignment.Top,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Icon(
                    Icons.AutoMirrored.Filled.Notes,
                    contentDescription = null,
                    modifier = Modifier.size(14.dp),
                    tint = Domovoi.colors.fgMuted,
                )
                Text(desc, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            }
        }

        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Pill(event.source ?: "local", if (event.isGoogle()) Tone.Idle else Tone.Ok)
            if (event.isGoogle() && event.last_synced_at != null) {
                Text(
                    "synced ${relTime(event.last_synced_at)}",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgFaint,
                )
            }
        }
        Text(
            "event #${event.id}",
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgFaint,
        )

        HorizontalDivider(color = Domovoi.colors.borderSoft)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = onEdit) { Text("edit", color = Domovoi.colors.fg) }
            TextButton(onClick = onDelete) { Text("delete", color = Domovoi.colors.err) }
        }
    }
}

// ---------------------------------------------------------------------------
// Create / edit modal — native date + time pickers, start shift preserves
// duration, end must be after start.
// ---------------------------------------------------------------------------

@Composable
internal fun EventEditorDialog(
    initial: EventDraft,
    onDismiss: () -> Unit,
    onSave: (EventDraft) -> Unit,
) {
    var title by remember(initial) { mutableStateOf(initial.title) }
    var start by remember(initial) { mutableStateOf(initial.start) }
    var end by remember(initial) { mutableStateOf(initial.end) }
    var location by remember(initial) { mutableStateOf(initial.location) }
    var description by remember(initial) { mutableStateOf(initial.description) }
    var titleErr by remember(initial) { mutableStateOf<String?>(null) }
    var endErr by remember(initial) { mutableStateOf<String?>(null) }

    Dialog(onDismissRequest = onDismiss) {
        Surface(
            shape = RoundedCornerShape(12.dp),
            color = Domovoi.colors.raised,
            border = BorderStroke(1.dp, Domovoi.colors.border),
        ) {
            Column(
                Modifier.verticalScroll(rememberScrollState()).padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Text(
                    if (initial.id != null) "edit event" else "new event",
                    style = MaterialTheme.typography.titleMedium,
                    color = Domovoi.colors.fg,
                )

                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    OutlinedTextField(
                        value = title,
                        onValueChange = { title = it; titleErr = null },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("title") },
                        placeholder = { Text("e.g. dinner with mom", color = Domovoi.colors.fgSubtle) },
                        singleLine = true,
                        isError = titleErr != null,
                    )
                    titleErr?.let {
                        Text(it, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.err)
                    }
                }

                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    SectionLabel("starts at")
                    DateTimeRow(start) { newStart ->
                        val dur = Duration.between(start, end)
                        start = newStart
                        end = newStart.plus(
                            if (dur.isZero || dur.isNegative) Duration.ofHours(1) else dur
                        )
                        endErr = null
                    }
                }
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    SectionLabel("ends at")
                    DateTimeRow(end) { end = it; endErr = null }
                    endErr?.let {
                        Text(it, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.err)
                    }
                }

                OutlinedTextField(
                    value = location,
                    onValueChange = { location = it },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("location") },
                    placeholder = { Text("optional", color = Domovoi.colors.fgSubtle) },
                    singleLine = true,
                )
                OutlinedTextField(
                    value = description,
                    onValueChange = { description = it },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("description") },
                    placeholder = { Text("optional notes", color = Domovoi.colors.fgSubtle) },
                    minLines = 3,
                )

                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
                ) {
                    TextButton(onClick = onDismiss) { Text("cancel", color = Domovoi.colors.fgMuted) }
                    Button(onClick = {
                        val t = title.trim()
                        titleErr = if (t.isEmpty()) "title is required" else null
                        endErr = if (!end.isAfter(start)) "end must be after start" else null
                        if (titleErr == null && endErr == null) {
                            onSave(
                                EventDraft(
                                    id = initial.id,
                                    title = t,
                                    start = start,
                                    end = end,
                                    location = location.trim(),
                                    description = description.trim(),
                                )
                            )
                        }
                    }) { Text(if (initial.id != null) "save" else "create event") }
                }
            }
        }
    }
}

private val dateBtnFmt = DateTimeFormatter.ofPattern("MMM d, yyyy", Locale.US)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DateTimeRow(value: LocalDateTime, onChange: (LocalDateTime) -> Unit) {
    var showDate by remember { mutableStateOf(false) }
    var showTime by remember { mutableStateOf(false) }

    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedButton(onClick = { showDate = true }, modifier = Modifier.weight(1f)) {
            Text(
                value.format(dateBtnFmt).lowercase(Locale.US),
                color = Domovoi.colors.fg,
                maxLines = 1,
            )
        }
        OutlinedButton(onClick = { showTime = true }) {
            Text(fmtClock(value), color = Domovoi.colors.fg, maxLines = 1)
        }
    }

    if (showDate) {
        val dateState = rememberDatePickerState(
            initialSelectedDateMillis = value
                .toLocalDate()
                .atStartOfDay(ZoneOffset.UTC)
                .toInstant()
                .toEpochMilli(),
        )
        DatePickerDialog(
            onDismissRequest = { showDate = false },
            confirmButton = {
                TextButton(onClick = {
                    dateState.selectedDateMillis?.let { ms ->
                        val d = Instant.ofEpochMilli(ms).atZone(ZoneOffset.UTC).toLocalDate()
                        onChange(LocalDateTime.of(d, value.toLocalTime()))
                    }
                    showDate = false
                }) { Text("ok", color = Domovoi.colors.brand) }
            },
            dismissButton = {
                TextButton(onClick = { showDate = false }) {
                    Text("cancel", color = Domovoi.colors.fgMuted)
                }
            },
        ) {
            DatePicker(state = dateState)
        }
    }

    if (showTime) {
        val timeState = rememberTimePickerState(
            initialHour = value.hour,
            initialMinute = value.minute,
            is24Hour = false,
        )
        AlertDialog(
            onDismissRequest = { showTime = false },
            containerColor = Domovoi.colors.raised,
            title = { Text("pick a time", style = MaterialTheme.typography.titleSmall) },
            text = { TimePicker(state = timeState) },
            confirmButton = {
                TextButton(onClick = {
                    onChange(
                        LocalDateTime.of(
                            value.toLocalDate(),
                            LocalTime.of(timeState.hour, timeState.minute),
                        )
                    )
                    showTime = false
                }) { Text("ok", color = Domovoi.colors.brand) }
            },
            dismissButton = {
                TextButton(onClick = { showTime = false }) {
                    Text("cancel", color = Domovoi.colors.fgMuted)
                }
            },
        )
    }
}
