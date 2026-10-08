package com.domovoi.app

import android.content.Context
import android.util.Log
import androidx.compose.runtime.staticCompositionLocalOf
import com.domovoi.app.alerts.TimerAlerts
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.Discovery
import com.domovoi.app.net.IdentityGate
import com.domovoi.app.net.NetworkWatch
import com.domovoi.app.net.StateBus
import com.domovoi.app.player.LyricsRepository
import com.domovoi.app.player.PlayerController
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow

/** Process-wide singletons. Deliberately no DI framework — one small graph. */
class AppContainer(context: Context) {
    val prefs = Prefs(context)

    /** The networks the phone is on, as one fingerprint every identity
     *  verdict is keyed to (net/NetworkWatch.kt). Registered with
     *  ConnectivityManager by DomovoiApplication. */
    val network = NetworkWatch(
        onChange = { identity.networkChanged() },
        log = { Log.i("NetworkWatch", it) },
    )

    /** The saved server proves its identity on each network before the
     *  household token goes to it (net/IdentityGate.kt, A6-03). Its probe
     *  runs on a token-less copy of the app's client; the pins live in
     *  Prefs. */
    val identity: IdentityGate = IdentityGate(
        probe = { base, challenge ->
            IdentityGate.httpProbe(Discovery.client(api.http, IdentityGate.PROBE_TIMEOUT_MS), base, challenge)
        },
        pins = prefs,
        network = { network.fingerprint },
        log = { Log.i("IdentityGate", it) },
    )
    val api: ApiClient = ApiClient(prefs, identity)
    val bus = StateBus(api, prefs)
    val player = PlayerController(context, api, prefs)

    /** Lyrics for what plays, household tier only (player/Lyrics.kt). */
    val lyrics = LyricsRepository(api)

    /** Timer and reminder alerts: live notifications plus the local alarm
     *  mirror (alerts/TimerAlerts.kt). Started by DomovoiApplication. */
    val alerts = TimerAlerts(context, api, bus, prefs)

    /** A screen something outside the UI asked for ("home" from a timer
     *  alert's tap); AppShell navigates there and clears it. */
    val pendingRoute = MutableStateFlow<String?>(null)

    /** App-lifetime scope for fire-and-forget work that must outlive a
     *  composable (e.g. the video position save on player dispose). */
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
}

val LocalApp = staticCompositionLocalOf<AppContainer> {
    error("AppContainer not provided")
}

/** Bottom-center toast, the web useToast() analog. Provided by the shell. */
val LocalToast = staticCompositionLocalOf<(String) -> Unit> { {} }
