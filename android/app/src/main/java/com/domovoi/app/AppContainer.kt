package com.domovoi.app

import android.content.Context
import androidx.compose.runtime.staticCompositionLocalOf
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.StateBus
import com.domovoi.app.player.PlayerController

/** Process-wide singletons. Deliberately no DI framework — one small graph. */
class AppContainer(context: Context) {
    val prefs = Prefs(context)
    val api = ApiClient(prefs)
    val bus = StateBus(api, prefs)
    val player = PlayerController(context, api, prefs)
}

val LocalApp = staticCompositionLocalOf<AppContainer> {
    error("AppContainer not provided")
}

/** Bottom-center toast, the web useToast() analog. Provided by the shell. */
val LocalToast = staticCompositionLocalOf<(String) -> Unit> { {} }
