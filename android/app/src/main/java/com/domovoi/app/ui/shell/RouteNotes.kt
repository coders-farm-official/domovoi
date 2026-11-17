package com.domovoi.app.ui.shell

import com.domovoi.app.net.Capabilities

/** Why a route a link reached is not shown here, for its placeholder
 *  (ScreenRouter): the title and the line under it. Chat hidden because the
 *  server's AI features are off ([Capabilities.aiEnabled]) says so. A screen
 *  that uses a plugin of ours this server doesn't run (Stations, Images)
 *  names neither the screen nor the plugin: on such a server the app
 *  doesn't mention them at all, and the shell takes the person home at
 *  once (AppShell). */
fun Route.unavailableNote(caps: Capabilities): Pair<String, String> =
    when {
        this == Route.Chat && !caps.aiEnabled ->
            "${label.lowercase()} is turned off" to "this server has its AI features turned off"
        requiredCapability() != null -> "this screen isn't available" to "this server doesn't offer it"
        else -> "${label.lowercase()} isn't available" to "this server doesn't offer it"
    }
