package com.domovoi.app.ui.screens.music

import android.content.Context
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import com.domovoi.app.ui.theme.Domovoi

/**
 * The Music page's rooms strip is folded away until asked for (owner
 * 2026-11-11): a simple page by default. The words are the web's
 * (web/static/music.jsx, the rooms toggle).
 */
internal object RoomsToggle {
    fun label(open: Boolean, playingCount: Int): String = when {
        open -> "Hide rooms"
        playingCount > 0 -> "Show rooms ($playingCount playing)"
        else -> "Show rooms"
    }

    /** The rooms the now-playing read already on screen says are playing. */
    fun playingCount(nowPlaying: List<NowPlayingRoom>): Int = nowPlaying.count { it.state == "play" }
}

/**
 * Whether the rooms strip is open, kept on this phone (the web keeps it per
 * browser, `domovoi.music.roomsOpen`). Closed until someone opens it.
 */
internal class RoomsOpenStore(private val flags: Flags) {

    /** The storage it sits on: SharedPreferences in the app, a map in tests. */
    interface Flags {
        fun get(key: String): Boolean?
        fun put(key: String, value: Boolean)
    }

    var open: Boolean
        get() = flags.get(KEY) ?: false
        set(value) = flags.put(KEY, value)

    companion object {
        private const val KEY = "rooms_open"

        fun forApp(context: Context): RoomsOpenStore {
            val prefs = context.applicationContext.getSharedPreferences("music_ui", Context.MODE_PRIVATE)
            return RoomsOpenStore(object : Flags {
                override fun get(key: String): Boolean? = if (prefs.contains(key)) prefs.getBoolean(key, false) else null
                override fun put(key: String, value: Boolean) { prefs.edit().putBoolean(key, value).apply() }
            })
        }
    }
}

/** "Show rooms" / "Show rooms (2 playing)" / "Hide rooms": a text link that
 *  is a button, read out as expanded or collapsed. */
@Composable
internal fun RoomsToggleLink(open: Boolean, playingCount: Int, onToggle: () -> Unit) {
    Box(
        Modifier
            .heightIn(min = 48.dp)
            .clickable(onClickLabel = if (open) "hide rooms" else "show rooms", onClick = onToggle)
            .semantics {
                role = Role.Button
                stateDescription = if (open) "expanded" else "collapsed"
            }
            .padding(horizontal = 4.dp, vertical = 10.dp),
        contentAlignment = Alignment.CenterStart,
    ) {
        Text(
            RoomsToggle.label(open, playingCount),
            style = MaterialTheme.typography.bodyMedium.copy(textDecoration = TextDecoration.Underline),
            color = Domovoi.colors.brand,
        )
    }
}
