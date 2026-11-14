package com.domovoi.app.ui.shell.player

import com.domovoi.app.player.LyricsResult
import com.domovoi.app.player.LyricsView

/*
 * The full player's panel tabs (the web's PlayerSheet, 2026-11-12): Lyrics ·
 * Queue · Hide. The web has an Equalizer tab too; the phone has no equalizer,
 * so it has none (no control that can't do what it says). The web's
 * visualizer is web only.
 */

internal enum class SheetTab(val label: String) {
    Lyrics("lyrics"),
    Queue("queue"),
    Hide("hide"),
}

/** The tab picked last, kept for this run of the app (the web keeps it for the browser tab's session). */
internal object SheetTabMemory {
    @Volatile var picked: SheetTab? = null
}

/** The tabs offered: Lyrics only for a song that can have them (a library track, a viewer who may read them). */
internal fun sheetTabs(lyricsTab: Boolean): List<SheetTab> =
    listOfNotNull(SheetTab.Lyrics.takeIf { lyricsTab }, SheetTab.Queue, SheetTab.Hide)

/**
 * Which panel shows (the web's playerSheetTab, minus the Equalizer).
 * [here]: the tab picked for THIS song — it stays, even Lyrics on a song with
 * none found: the person asked to look. Else the session's [picked] while it
 * still applies — Hide and Queue always do, Lyrics while the song has some
 * (or they are still loading) — else the default: Lyrics when the song has
 * them, otherwise the Queue.
 */
internal fun playerSheetTab(
    here: SheetTab?,
    picked: SheetTab?,
    lyricsTab: Boolean,
    lyricsFound: Boolean,
    lyricsLoading: Boolean,
): SheetTab {
    fun usable(t: SheetTab) = t != SheetTab.Lyrics || lyricsTab
    if (here != null && usable(here)) return here
    val withLyrics = lyricsTab && (lyricsFound || lyricsLoading)
    if (picked == SheetTab.Hide || picked == SheetTab.Queue) return picked
    if (withLyrics) return SheetTab.Lyrics
    return SheetTab.Queue
}

/** Whether a lyrics answer has words to show: timed lines or plain text. */
internal fun lyricsFound(result: LyricsResult?): Boolean {
    val doc = (result as? LyricsResult.Loaded)?.doc ?: return false
    val v = doc.view()
    return v is LyricsView.Timed || v is LyricsView.Plain
}
