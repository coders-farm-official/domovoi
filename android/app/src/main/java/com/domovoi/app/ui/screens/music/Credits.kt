package com.domovoi.app.ui.screens.music

import androidx.compose.runtime.Composable
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import com.domovoi.app.ui.theme.Domovoi

/**
 * The artists a track credits beyond the one its artist line already names
 * (a track's `artists`, in the file's order, V058): what the library shows,
 * quietly, as "with B, C" — the web's creditedWith (components.jsx), word
 * for word. A name is left out when it is blank or already appears, in any
 * case, anywhere in the artist line ("Main feat. Guest" leaves Guest out).
 * The rest are joined with ", " however many there are (no "and"). "" when
 * there are none.
 *
 * Shown after the artist in a library row and a playlist's song rows
 * (" · with B, C", fainter than the artist: [artistLine]), and on a line of
 * its own under the artist in the track drawer — where the web shows it.
 */
internal fun creditedWith(artist: String?, artists: List<String>): String {
    val shown = (artist ?: "").lowercase()
    val extra = artists.filter { it.isNotBlank() && !shown.contains(it.trim().lowercase()) }
    return if (extra.isEmpty()) "" else "with " + extra.joinToString(", ")
}

/** [creditedWith] for a library row. */
internal fun creditedWith(t: LibraryTrack): String = creditedWith(t.artist, t.artists)

/** A row's artist line as text: [artist], then " · " and [credits] when there are any. */
internal fun artistLine(artist: String, credits: String): String =
    if (credits.isEmpty()) artist else "$artist · $credits"

/** [artistLine] with " · " and the credits in the fainter colour (music.jsx's rows). */
@Composable
internal fun artistWithCredits(artist: String, credits: String): AnnotatedString {
    val faint = Domovoi.colors.fgFaint
    return buildAnnotatedString {
        append(artist)
        if (credits.isNotEmpty()) {
            pushStyle(SpanStyle(color = faint))
            append(artistLine(artist, credits).removePrefix(artist))
            pop()
        }
    }
}
