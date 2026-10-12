package com.domovoi.app.ui.shell

import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.staticCompositionLocalOf
import com.domovoi.app.net.Capabilities
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json

/**
 * The phone bar: the bottom bar's tabs at compact width — the web's phone
 * strip (web/static/components.jsx PHONE_BAR_DEFAULT / phoneBarRoutes).
 * home · music · videos · chat until someone picks others on THIS phone
 * (Settings → Phone bar): Home always first, at most [MAX], at least [MIN],
 * any screen a launcher may offer here ([Route.visibleOn] — so the plugin
 * screens this server allows too, and on a shared screen never the
 * personal ones, whatever was saved). Kept in this install's Prefs
 * (`phone_bar`, route names), so another phone keeps its own.
 */
object PhoneBar {
    const val MAX = 5
    const val MIN = 2

    /** Screens that may sit on the bar, in the drawer's order: Home, then
     *  the workspace (Settings and the manual stay on the grid, as on the
     *  web). */
    val Choices: List<Route> = listOf(Route.Home) + WorkspaceRoutes

    private val codec = ListSerializer(String.serializer())

    /** Route names as Prefs keeps them. */
    fun encode(routes: List<Route>): String = Json.encodeToString(codec, routes.map { it.name })

    /** A saved bar, or null (never saved, or unreadable: the default). Names
     *  this build doesn't know are dropped. */
    fun decode(raw: String?): List<Route>? {
        if (raw.isNullOrBlank()) return null
        val names = runCatching { Json.decodeFromString(codec, raw) }.getOrNull() ?: return null
        return names.mapNotNull { n -> Route.entries.firstOrNull { it.name == n } }
    }

    /**
     * The tabs, left to right, for [saved] (null = the default) on a server
     * with [caps], on a [shared] screen or not: Home first, then the saved
     * screens a launcher may offer here, at most [MAX]. A saved bar left
     * with fewer than [MIN] (its screens gone: a plugin removed, the screen
     * made shared) falls back to the default.
     */
    fun routes(saved: List<Route>?, caps: Capabilities, shared: Boolean): List<Route> {
        fun pick(list: List<Route>): List<Route> {
            val out = mutableListOf(Route.Home)
            for (r in list) {
                if (out.size >= MAX) break
                if (r != Route.Home && r in Choices && r !in out && r.visibleOn(caps, shared)) out += r
            }
            return out
        }
        val mine = saved?.let { pick(it) }
        return if (mine != null && mine.size >= MIN) mine else pick(DefaultPhoneBar)
    }

    /** One edit to the bar [current] ([routes]' answer). Home never moves or
     *  goes, and the limits hold: an edit past them is no edit (the same
     *  list back). The web's phoneBarEdit. */
    fun edit(current: List<Route>, op: Op, route: Route): List<Route> {
        val rest = current.filter { it != Route.Home }.toMutableList()
        val i = rest.indexOf(route)
        val size = rest.size + 1
        when (op) {
            Op.Add -> if (route != Route.Home && i < 0 && route in Choices && size < MAX) rest += route else return current
            Op.Remove -> if (i >= 0 && size > MIN) rest.removeAt(i) else return current
            Op.Left, Op.Right -> {
                val j = if (op == Op.Left) i - 1 else i + 1
                if (i < 0 || j < 0 || j >= rest.size) return current
                val moved = rest[i]
                rest[i] = rest[j]
                rest[j] = moved
            }
        }
        return listOf(Route.Home) + rest
    }

    enum class Op { Add, Remove, Left, Right }
}

/** Opens Settings on a tab, by its key ("satellites", "phonebar", …): the
 *  web's openSettingsTab. The shell provides it. */
val LocalOpenSettingsTab = staticCompositionLocalOf<(String) -> Unit> { {} }

/** A Settings tab a link asked for and Settings has not taken yet, and how
 *  Settings says it took it. */
class SettingsTabRequest(val tab: String?, val take: () -> Unit)

val LocalSettingsTabRequest = compositionLocalOf { SettingsTabRequest(null) {} }
