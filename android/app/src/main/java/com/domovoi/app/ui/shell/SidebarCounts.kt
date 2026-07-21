package com.domovoi.app.ui.shell

import androidx.compose.runtime.Composable
import com.domovoi.app.net.CAP_STATIONS
import com.domovoi.app.net.LocalCapabilities
import com.domovoi.app.net.rememberApi
import kotlinx.serialization.json.JsonArray

data class SidebarCounts(
    val people: Int? = null,
    val satellites: Int? = null,
    val calendar: Int? = null,
    val stations: Int? = null,
) {
    fun forRoute(r: Route): Int? = when (r) {
        Route.People -> people
        Route.Satellites -> satellites
        Route.Calendar -> calendar
        Route.Stations -> stations
        else -> null
    }
}

/** useSidebarCounts analog — refetch only on the row-count-changing events.
 *  Capability-gated badges are skipped entirely when the backing plugin is
 *  absent (design §8): no fetch, no badge. */
@Composable
fun rememberSidebarCounts(): SidebarCounts {
    val hasStations = LocalCapabilities.current.has(CAP_STATIONS)
    val events = setOf("calendar.events.changed", "radio.stations.changed")
    val state = rememberApi(hasStations, eventTypes = events, fetch = { app ->
        suspend fun lenOf(path: String): Int? = runCatching {
            (app.api.get(path) as? JsonArray)?.size
        }.getOrNull()
        SidebarCounts(
            people = lenOf("/api/people"),
            satellites = lenOf("/api/satellites"),
            calendar = lenOf("/api/calendar/events"),
            stations = if (hasStations) {
                lenOf("/api/plugins/radio/stations?favorited_only=true&limit=1000")
            } else {
                null
            },
        )
    })
    return state.data ?: SidebarCounts()
}
