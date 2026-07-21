package com.domovoi.app.net

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import kotlinx.coroutines.CancellationException

/**
 * Compose analogs of the web hooks in web/static/data.js.
 *
 * `rememberApi` == useApiObject / useApiList: fetch once per key, refetch when
 * any of the named WS event types arrive, expose { data, loading, error,
 * refresh }. `OnStateEvents` == useStateEvents (react without refetching).
 */
class ApiState<T>(
    val data: T?,
    val loading: Boolean,
    val error: String?,
    val refresh: () -> Unit,
)

@Composable
fun <T> rememberApi(
    vararg keys: Any?,
    eventTypes: Set<String> = emptySet(),
    fetch: suspend (AppContainer) -> T,
): ApiState<T> {
    val app = LocalApp.current
    var data by remember { mutableStateOf<T?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var tick by remember { mutableIntStateOf(0) }

    LaunchedEffect(app.prefs.serverUrl.value, tick, *keys) {
        loading = data == null
        try {
            data = fetch(app)
            error = null
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            error = e.message ?: "request failed"
        }
        loading = false
    }

    if (eventTypes.isNotEmpty()) {
        LaunchedEffect(eventTypes) {
            app.bus.events.collect { ev ->
                if (ev.type in eventTypes) tick++
            }
        }
    }

    return ApiState(data, loading, error) { tick++ }
}

/** Subscribe to WS events without refetching — the useStateEvents analog. */
@Composable
fun OnStateEvents(eventTypes: Set<String>, onEvent: (WsEvent) -> Unit) {
    val app = LocalApp.current
    LaunchedEffect(eventTypes) {
        app.bus.events.collect { ev ->
            if (ev.type in eventTypes) onEvent(ev)
        }
    }
}
