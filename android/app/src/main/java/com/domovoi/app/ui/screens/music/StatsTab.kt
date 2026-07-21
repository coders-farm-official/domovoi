package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.domovoi.app.net.ApiState
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.Stat
import com.domovoi.app.ui.components.fmtBigDur
import kotlin.math.roundToInt

/** Stats tab — whole-library aggregates from /api/music/library/stats.
 *  Mirrors web StatsTab. */
internal fun LazyListScope.statsTab(stats: ApiState<LibraryStats>) {
    item(key = "stats") {
        val s = stats.data
        when {
            s == null && stats.loading -> LoadingState()
            s == null -> EmptyState("stats unavailable", "library/stats endpoint did not return")
            else -> StatsGrid(s)
        }
    }
}

@Composable
private fun StatsGrid(s: LibraryStats) {
    val total = s.totalTracks
    val totalDur = s.totalDurationSec
    // added_via is an OPEN enum — plugins register their own values, so
    // the breakdown renders whatever buckets the server reports.
    val byVia = s.byAddedVia.entries.sortedByDescending { it.value }
    val topVia = byVia.firstOrNull()
    val restVia = byVia.drop(1)

    data class Tile(val label: String, val value: String, val sub: String?)
    val tiles = listOf(
        Tile("total tracks", "$total", "across all sources"),
        Tile(
            "total duration",
            if (totalDur != null && totalDur > 0) fmtBigDur(totalDur) else "—",
            if (totalDur != null && totalDur > 0) "${(totalDur / 60).roundToInt()} minutes"
            else "no durations indexed",
        ),
        Tile(
            "added via " + (topVia?.key ?: "—"),
            "${topVia?.value ?: 0}",
            restVia.takeIf { it.isNotEmpty() }
                ?.joinToString(" · ") { "${it.value} ${it.key}" },
        ),
        Tile(
            "enriched",
            if (total > 0) "${s.enrichedCount} / $total" else "—",
            if (total > 0) "${total - s.enrichedCount} pending" else null,
        ),
    )

    val cols = if (compactWidth()) 2 else 4
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        tiles.chunked(cols).forEach { row ->
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                row.forEach { t ->
                    Stat(t.label, t.value, t.sub, modifier = Modifier.weight(1f))
                }
                repeat(cols - row.size) { Spacer(Modifier.weight(1f)) }
            }
        }
    }
}
