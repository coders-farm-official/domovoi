package com.domovoi.app.ui.screens.calendar

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.domovoi.app.ui.theme.Domovoi
import java.time.Duration
import java.time.LocalDate
import java.time.LocalTime
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import java.util.Locale

// ---------------------------------------------------------------------------
// Month grid — 42 cells, up to 3 chips + "+N more", today highlighted,
// out-of-month dimmed, tap a day -> day view.
// ---------------------------------------------------------------------------

@Composable
internal fun MonthGrid(
    anchor: LocalDate,
    events: List<CalendarEvent>,
    selectedId: Long?,
    compact: Boolean,
    onSelect: (Long) -> Unit,
    onPickDay: (LocalDate) -> Unit,
) {
    val gridStart = monthGridStart(anchor)
    val today = LocalDate.now()
    val anchorMonth = YearMonth.from(anchor)
    val byDay = remember(events) {
        events
            .groupBy { it.startLocal().toLocalDate() }
            .mapValues { (_, v) -> v.sortedBy { it.starts_at } }
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        Row(Modifier.fillMaxWidth()) {
            listOf("sun", "mon", "tue", "wed", "thu", "fri", "sat").forEach { d ->
                Text(
                    d,
                    modifier = Modifier.weight(1f).padding(vertical = 6.dp),
                    textAlign = TextAlign.Center,
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
        }
        val cellH = if (compact) 86.dp else 108.dp
        repeat(6) { r ->
            Row(Modifier.fillMaxWidth().height(cellH)) {
                repeat(7) { c ->
                    val d = gridStart.plusDays((r * 7 + c).toLong())
                    MonthCell(
                        d = d,
                        inMonth = YearMonth.from(d) == anchorMonth,
                        isToday = d == today,
                        dayEvents = byDay[d] ?: emptyList(),
                        selectedId = selectedId,
                        compact = compact,
                        onSelect = onSelect,
                        onPickDay = onPickDay,
                        modifier = Modifier.weight(1f).fillMaxHeight(),
                    )
                }
            }
        }
    }
}

@Composable
private fun MonthCell(
    d: LocalDate,
    inMonth: Boolean,
    isToday: Boolean,
    dayEvents: List<CalendarEvent>,
    selectedId: Long?,
    compact: Boolean,
    onSelect: (Long) -> Unit,
    onPickDay: (LocalDate) -> Unit,
    modifier: Modifier = Modifier,
) {
    val visible = dayEvents.take(3)
    val overflow = dayEvents.size - visible.size
    Column(
        modifier
            .border(0.5.dp, Domovoi.colors.borderSoft)
            .clickable { onPickDay(d) }
            .padding(3.dp),
    ) {
        Text(
            "${d.dayOfMonth}",
            style = MaterialTheme.typography.labelSmall,
            color = when {
                isToday -> Domovoi.colors.brandFg
                !inMonth -> Domovoi.colors.fgFaint
                else -> Domovoi.colors.fgMuted
            },
            modifier = if (isToday) {
                Modifier
                    .background(Domovoi.colors.brand, RoundedCornerShape(999.dp))
                    .padding(horizontal = 5.dp, vertical = 1.dp)
            } else {
                Modifier.padding(horizontal = 2.dp)
            },
        )
        visible.forEach { e ->
            MonthChip(
                e = e,
                selected = selectedId == e.id,
                dimmed = !inMonth,
                compact = compact,
                onSelect = onSelect,
            )
        }
        if (overflow > 0) {
            Text(
                "+$overflow more",
                fontSize = 9.sp,
                color = Domovoi.colors.fgFaint,
                modifier = Modifier.padding(start = 2.dp, top = 2.dp),
            )
        }
    }
}

@Composable
private fun MonthChip(
    e: CalendarEvent,
    selected: Boolean,
    dimmed: Boolean,
    compact: Boolean,
    onSelect: (Long) -> Unit,
) {
    val main = if (e.isGoogle()) Domovoi.colors.idle else Domovoi.colors.brand
    val soft = if (e.isGoogle()) Domovoi.colors.idleSoft else Domovoi.colors.brandSoft
    val shape = RoundedCornerShape(4.dp)
    Row(
        Modifier
            .fillMaxWidth()
            .padding(top = 2.dp)
            .clip(shape)
            .background(soft)
            .then(if (selected) Modifier.border(1.dp, main, shape) else Modifier)
            .clickable { onSelect(e.id) }
            .padding(horizontal = 3.dp, vertical = 1.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(3.dp),
    ) {
        Box(Modifier.size(5.dp).background(main, CircleShape))
        if (!compact) {
            Text(
                fmtClock(e.startLocal()),
                fontSize = 9.sp,
                color = Domovoi.colors.fgMuted,
                maxLines = 1,
            )
        }
        Text(
            e.title,
            fontSize = 10.sp,
            color = if (dimmed) Domovoi.colors.fgMuted else Domovoi.colors.fg,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

// ---------------------------------------------------------------------------
// Time grid — shared by week (7 columns) and day (1 column) views.
// 6:00–22:00 hour rows, positioned events, "now" line on today.
// ---------------------------------------------------------------------------

private const val START_HOUR = 6
private const val END_HOUR = 22
private const val GRID_HOURS = END_HOUR - START_HOUR
private val HOUR_HEIGHT = 60.dp
private val weekdayShortFmt = DateTimeFormatter.ofPattern("EEE", Locale.US)

@Composable
internal fun TimeGrid(
    days: List<LocalDate>,
    events: List<CalendarEvent>,
    selectedId: Long?,
    onSelect: (Long) -> Unit,
    onPickDay: ((LocalDate) -> Unit)? = null,
) {
    val today = LocalDate.now()
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth()) {
            Spacer(Modifier.width(44.dp))
            days.forEach { d ->
                val isToday = d == today
                Column(
                    Modifier
                        .weight(1f)
                        .then(
                            if (onPickDay != null) Modifier.clickable { onPickDay(d) }
                            else Modifier
                        )
                        .padding(vertical = 6.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    Text(
                        d.format(weekdayShortFmt).lowercase(Locale.US),
                        style = MaterialTheme.typography.labelSmall,
                        color = if (isToday) Domovoi.colors.brand else Domovoi.colors.fgMuted,
                    )
                    Text(
                        "${d.dayOfMonth}",
                        style = MaterialTheme.typography.titleSmall,
                        color = if (isToday) Domovoi.colors.brandFg else Domovoi.colors.fg,
                        modifier = if (isToday) {
                            Modifier
                                .background(Domovoi.colors.brand, RoundedCornerShape(999.dp))
                                .padding(horizontal = 7.dp, vertical = 1.dp)
                        } else {
                            Modifier
                        },
                    )
                }
            }
        }
        HorizontalDivider(color = Domovoi.colors.border)
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState())) {
            Row(Modifier.fillMaxWidth().height(HOUR_HEIGHT * GRID_HOURS)) {
                Column(Modifier.width(44.dp)) {
                    for (h in START_HOUR until END_HOUR) {
                        Box(Modifier.height(HOUR_HEIGHT).fillMaxWidth()) {
                            Text(
                                hourLabel(h),
                                style = MaterialTheme.typography.labelSmall,
                                color = Domovoi.colors.fgFaint,
                                modifier = Modifier
                                    .align(Alignment.TopEnd)
                                    .padding(end = 4.dp),
                            )
                        }
                    }
                }
                days.forEach { d ->
                    DayColumn(
                        day = d,
                        dayEvents = events.filter { it.startLocal().toLocalDate() == d },
                        selectedId = selectedId,
                        onSelect = onSelect,
                        modifier = Modifier.weight(1f).fillMaxHeight(),
                    )
                }
            }
        }
    }
}

@Composable
private fun DayColumn(
    day: LocalDate,
    dayEvents: List<CalendarEvent>,
    selectedId: Long?,
    onSelect: (Long) -> Unit,
    modifier: Modifier = Modifier,
) {
    val lineColor = Domovoi.colors.borderSoft
    val isToday = day == LocalDate.now()

    Box(
        modifier.drawBehind {
            val hourPx = HOUR_HEIGHT.toPx()
            for (i in 0..GRID_HOURS) {
                val y = i * hourPx
                drawLine(lineColor, Offset(0f, y), Offset(size.width, y), strokeWidth = 1f)
            }
            drawLine(lineColor, Offset(0f, 0f), Offset(0f, size.height), strokeWidth = 1f)
        },
    ) {
        dayEvents.forEach { e ->
            val s = e.startLocal()
            val en = e.endLocal()
            val top = ((s.hour - START_HOUR) * 60 + s.minute)
                .coerceIn(0, GRID_HOURS * 60 - 20)
            val durMin = Duration.between(s, en).toMinutes().toInt()
                .coerceAtLeast(20)
                .coerceAtMost(GRID_HOURS * 60 - top)
            val main = if (e.isGoogle()) Domovoi.colors.idle else Domovoi.colors.brand
            val soft = if (e.isGoogle()) Domovoi.colors.idleSoft else Domovoi.colors.brandSoft
            val sel = selectedId == e.id
            val shape = RoundedCornerShape(6.dp)
            Column(
                Modifier
                    .padding(horizontal = 2.dp)
                    .offset(y = top.dp)
                    .fillMaxWidth()
                    .height(durMin.dp)
                    .clip(shape)
                    .background(soft)
                    .border(if (sel) 1.5.dp else 0.5.dp, main, shape)
                    .clickable { onSelect(e.id) }
                    .padding(4.dp),
            ) {
                Text(
                    e.title,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = Domovoi.colors.fg,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    fmtRange(e),
                    fontSize = 9.sp,
                    color = Domovoi.colors.fgMuted,
                    maxLines = 1,
                )
                val loc = e.location
                if (!loc.isNullOrBlank() && durMin > 60) {
                    Text(
                        loc,
                        fontSize = 9.sp,
                        color = Domovoi.colors.fgMuted,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }
        if (isToday) {
            val now = LocalTime.now()
            val m = (now.hour - START_HOUR) * 60 + now.minute
            if (m in 0..(GRID_HOURS * 60)) {
                Row(
                    Modifier.offset(y = m.dp).fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box(Modifier.size(6.dp).background(Domovoi.colors.brand, CircleShape))
                    Box(
                        Modifier
                            .weight(1f)
                            .height(2.dp)
                            .background(Domovoi.colors.brand),
                    )
                }
            }
        }
    }
}
