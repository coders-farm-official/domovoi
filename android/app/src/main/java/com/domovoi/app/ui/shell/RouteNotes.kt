package com.domovoi.app.ui.shell

import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.serverChatOn

/** Why a route a link reached is not shown here, for its placeholder
 *  (ScreenRouter): the title and the line under it. Chat hidden because the
 *  server's AI features are off ([serverChatOn]) says so; anything else is a
 *  plugin that isn't installed. */
fun Route.unavailableNote(caps: Capabilities): Pair<String, String> =
    if (this == Route.Chat && !caps.serverChatOn()) {
        "${label.lowercase()} is turned off" to "this server has its AI features turned off"
    } else {
        "${label.lowercase()} isn't available" to "the plugin providing this screen isn't installed on this server"
    }
