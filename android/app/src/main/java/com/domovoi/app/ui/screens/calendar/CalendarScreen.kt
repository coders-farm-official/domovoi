package com.domovoi.app.ui.screens.calendar

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ChevronLeft
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Place
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.window.core.layout.WindowWidthSizeClass
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import java.time.LocalDate

/**
 * Calendar page — agenda / month / week / day views, full CRUD, event
 * detail rail (wide) / bottom sheet (compact). Mirrors
 * web/static/calendar.jsx.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CalendarScreen() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val compact = currentWindowAdaptiveInfo().windowSizeClass.windowWidthSizeClass ==
        WindowWidthSizeClass.COMPACT

    val state = rememberApi(eventTypes = setOf("calendar.events.changed")) { a ->
        a.api.get("/api/calendar/events").decode<List<CalendarEvent>>()
    }
    val events = state.data ?: emptyList()

    var view by remember(compact) {
        mutableStateOf(if (compact) CalView.Agenda else CalView.Month)
    }
    var anchor by remember { mutableStateOf(LocalDate.now()) }
    var selectedId by remember { mutableStateOf<Long?>(null) }
    var editorDraft by remember { mutableStateOf<EventDraft?>(null) }
    var deleteTarget by remember { mutableStateOf<CalendarEvent?>(null) }
    val selected = events.firstOrNull { it.id == selectedId }

    fun save(d: EventDraft) {
        scope.launch {
            runCatching {
                val loc: String? = d.location.ifBlank { null }
                val desc: String? = d.description.ifBlank { null }
                val body = buildJsonObject {
                    put("title", d.title)
                    put("starts_at", d.start.format(IsoLocalFmt))
                    put("ends_at", d.end.format(IsoLocalFmt))
                    put("location", loc)
                    put("description", desc)
                }
                if (d.id == null) app.api.post("/api/calendar/events", body)
                else app.api.patch("/api/calendar/events/${d.id}", body)
            }.onSuccess { res ->
                if (d.id == null) {
                    toast("event created")
                    val newId = runCatching {
                        (res as? JsonObject)?.get("id")?.jsonPrimitive?.longOrNull
                    }.getOrNull()
                    if (newId != null) selectedId = newId
                } else {
                    toast("event updated")
                    selectedId = d.id
                }
                editorDraft = null
                state.refresh()
            }.onFailure { toast("save failed: ${it.message}") }
        }
    }

    fun delete(e: CalendarEvent) {
        scope.launch {
            runCatching { app.api.delete("/api/calendar/events/${e.id}") }
                .onSuccess {
                    toast("deleted \"${e.title}\"")
                    if (selectedId == e.id) selectedId = null
                    state.refresh()
                }
                .onFailure { toast("delete failed: ${it.message}") }
        }
    }

    val pickDay: (LocalDate) -> Unit = { d -> anchor = d; view = CalView.Day }

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        PageHeader(
            "Calendar",
            "${events.size} events · source of truth until calendarhandler ships",
            actions = {
                Button(onClick = { editorDraft = blankDraft(anchor) }) { Text("new event") }
            },
        )
        Spacer(Modifier.height(12.dp))
        CalTopBar(
            view = view,
            views = if (compact) listOf(CalView.Agenda, CalView.Month, CalView.Day)
            else listOf(CalView.Month, CalView.Week, CalView.Day),
            anchor = anchor,
            onView = { view = it },
            onAnchor = { anchor = it },
        )
        Spacer(Modifier.height(12.dp))

        when {
            state.loading && state.data == null -> LoadingState()
            state.error != null && state.data == null ->
                ErrorState(state.error ?: "request failed", state.refresh)
            else -> {
                val visible = visibleEvents(view, anchor, events)
                Row(
                    Modifier.weight(1f).fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Box(Modifier.weight(1f)) {
                        if (visible.isEmpty()) {
                            if (view == CalView.Agenda) {
                                EmptyState("nothing on the calendar", "tap new event to add one")
                            } else {
                                EmptyState(
                                    "nothing in this range",
                                    "tap new event to add one for " +
                                        if (view == CalView.Day) fmtDayLabel(anchor) else view.label,
                                )
                            }
                        } else {
                            when (view) {
                                CalView.Agenda -> AgendaList(
                                    events = visible,
                                    selectedId = selectedId,
                                    onSelect = { selectedId = it },
                                )
                                CalView.Month -> MonthGrid(
                                    anchor = anchor,
                                    events = visible,
                                    selectedId = selectedId,
                                    compact = compact,
                                    onSelect = { selectedId = it },
                                    onPickDay = pickDay,
                                )
                                CalView.Week -> TimeGrid(
                                    days = (0..6).map { startOfWeekSun(anchor).plusDays(it.toLong()) },
                                    events = visible,
                                    selectedId = selectedId,
                                    onSelect = { selectedId = it },
                                    onPickDay = pickDay,
                                )
                                CalView.Day -> TimeGrid(
                                    days = listOf(anchor),
                                    events = visible,
                                    selectedId = selectedId,
                                    onSelect = { selectedId = it },
                                )
                            }
                        }
                    }
                    if (!compact && selected != null) {
                        DomovoiCard(Modifier.width(320.dp), padding = 0) {
                            EventDetailContent(
                                event = selected,
                                onClose = { selectedId = null },
                                onEdit = { editorDraft = draftOf(selected) },
                                onDelete = { deleteTarget = selected },
                            )
                        }
                    }
                }
            }
        }
    }

    if (compact && selected != null) {
        ModalBottomSheet(
            onDismissRequest = { selectedId = null },
            containerColor = Domovoi.colors.raised,
        ) {
            Box(Modifier.padding(bottom = 24.dp)) {
                EventDetailContent(
                    event = selected,
                    onClose = { selectedId = null },
                    onEdit = { editorDraft = draftOf(selected) },
                    onDelete = { deleteTarget = selected },
                )
            }
        }
    }

    editorDraft?.let { draft ->
        EventEditorDialog(
            initial = draft,
            onDismiss = { editorDraft = null },
            onSave = { save(it) },
        )
    }

    deleteTarget?.let { ev ->
        ConfirmDialog(
            title = "delete event",
            body = "Delete \"${ev.title}\"? This can't be undone.",
            confirmLabel = "delete",
            destructive = true,
            onConfirm = { delete(ev) },
            onDismiss = { deleteTarget = null },
        )
    }
}

// ---------------------------------------------------------------------------
// Top bar — view toggle, today, prev/next chevrons + range label.
// ---------------------------------------------------------------------------

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CalTopBar(
    view: CalView,
    views: List<CalView>,
    anchor: LocalDate,
    onView: (CalView) -> Unit,
    onAnchor: (LocalDate) -> Unit,
) {
    FlowRow(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(
            Modifier
                .border(1.dp, Domovoi.colors.border, RoundedCornerShape(8.dp))
                .padding(2.dp),
        ) {
            views.forEach { v ->
                val on = v == view
                Text(
                    v.label,
                    modifier = Modifier
                        .clip(RoundedCornerShape(6.dp))
                        .background(if (on) Domovoi.colors.brandSoft else Color.Transparent)
                        .clickable { onView(v) }
                        .padding(horizontal = 10.dp, vertical = 6.dp),
                    style = MaterialTheme.typography.labelMedium,
                    color = if (on) Domovoi.colors.brand else Domovoi.colors.fgMuted,
                )
            }
        }
        TextButton(onClick = { onAnchor(LocalDate.now()) }) {
            Text("today", color = Domovoi.colors.fgMuted)
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { onAnchor(stepAnchor(view, anchor, -1)) }) {
                Icon(
                    Icons.Filled.ChevronLeft,
                    contentDescription = "previous",
                    tint = Domovoi.colors.fgMuted,
                )
            }
            Text(
                rangeLabel(view, anchor),
                style = MaterialTheme.typography.labelLarge,
                color = Domovoi.colors.fg,
            )
            IconButton(onClick = { onAnchor(stepAnchor(view, anchor, 1)) }) {
                Icon(
                    Icons.Filled.ChevronRight,
                    contentDescription = "next",
                    tint = Domovoi.colors.fgMuted,
                )
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Agenda list (compact default) — upcoming events grouped by day.
// ---------------------------------------------------------------------------

@Composable
private fun AgendaList(
    events: List<CalendarEvent>,
    selectedId: Long?,
    onSelect: (Long) -> Unit,
) {
    val today = LocalDate.now()
    val grouped = events.groupBy { it.startLocal().toLocalDate() }

    LazyColumn(Modifier.fillMaxSize()) {
        grouped.forEach { (day, list) ->
            item(key = "h-$day") {
                Row(
                    Modifier.fillMaxWidth().padding(top = 12.dp, bottom = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        if (day == today) "today" else fmtDayLabel(day),
                        style = MaterialTheme.typography.labelMedium,
                        color = Domovoi.colors.fg,
                    )
                    Spacer(Modifier.weight(1f))
                    Text(
                        day.toString(),
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgFaint,
                    )
                }
            }
            items(list, key = { it.id }) { e ->
                AgendaRow(e, selected = selectedId == e.id, onClick = { onSelect(e.id) })
                Spacer(Modifier.height(6.dp))
            }
        }
    }
}

@Composable
private fun AgendaRow(e: CalendarEvent, selected: Boolean, onClick: () -> Unit) {
    val shape = RoundedCornerShape(8.dp)
    Row(
        Modifier
            .fillMaxWidth()
            .clip(shape)
            .background(if (selected) Domovoi.colors.brandSoft else Domovoi.colors.card)
            .border(1.dp, if (selected) Domovoi.colors.brand else Domovoi.colors.border, shape)
            .clickable(onClick = onClick)
            .padding(10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Column(Modifier.width(64.dp)) {
            Text(
                fmtClock(e.startLocal()),
                style = MaterialTheme.typography.labelMedium,
                color = Domovoi.colors.fg,
            )
            if (e.ends_at != null) {
                Text(
                    fmtClock(e.endLocal()),
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgFaint,
                )
            }
        }
        Column(Modifier.weight(1f)) {
            Text(
                e.title,
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fg,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            val loc = e.location
            if (!loc.isNullOrBlank()) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(3.dp),
                ) {
                    Icon(
                        Icons.Filled.Place,
                        contentDescription = null,
                        modifier = Modifier.size(11.dp),
                        tint = Domovoi.colors.fgMuted,
                    )
                    Text(
                        loc,
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }
        Pill(e.source ?: "local", if (e.isGoogle()) Tone.Idle else Tone.Ok)
    }
}
