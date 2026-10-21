package com.domovoi.app.assistant.docs

import android.content.Context
import android.content.SharedPreferences
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** This phone's answers to the heads-up (documents plan D3): the master
 *  switch and the kinds it said "don't warn me about this again" for. */
data class HeadsUpPrefs(val off: Boolean = false, val skip: Set<String> = emptySet())

/**
 * Where this phone keeps [HeadsUpPrefs]: for its own chats and, sent with
 * every message, for its home-server chats (the server's rules decide when
 * a card shows; the phone only says what it silenced). A SharedPreferences
 * file of its own (`heads_up`), app private and outside backups like the
 * rest of the app's storage; in memory only for tests ([memory]).
 */
class HeadsUpStore private constructor(private val sp: SharedPreferences?) {
    private val _state = MutableStateFlow(read())
    val state: StateFlow<HeadsUpPrefs> = _state.asStateFlow()
    val prefs: HeadsUpPrefs get() = _state.value

    private fun read(): HeadsUpPrefs {
        val p = sp ?: return HeadsUpPrefs()
        return HeadsUpPrefs(
            off = p.getBoolean(K_OFF, false),
            skip = p.getStringSet(K_SKIP, emptySet()).orEmpty().filter { it in DocHeadsUp.KINDS }.toSet(),
        )
    }

    private fun write(v: HeadsUpPrefs) {
        sp?.edit()?.putBoolean(K_OFF, v.off)?.putStringSet(K_SKIP, v.skip.toSet())?.apply()
        _state.value = v
    }

    fun setOff(off: Boolean) = write(prefs.copy(off = off))
    fun neverWarn(kind: String) = write(prefs.copy(skip = prefs.skip + kind))
    fun warnAgain(kind: String) = write(prefs.copy(skip = prefs.skip - kind))

    companion object {
        private const val K_OFF = "off"
        private const val K_SKIP = "skip"

        fun forApp(context: Context): HeadsUpStore =
            HeadsUpStore(context.applicationContext.getSharedPreferences("heads_up", Context.MODE_PRIVATE))

        fun memory(): HeadsUpStore = HeadsUpStore(null)

        /** What each kind is about, for "no more heads-ups about …" and Settings. */
        val KIND_WORDS = mapOf(
            "rewrite" to "rewriting a whole document", "compare" to "comparing parts of a long document",
            "calc" to "exact sums, averages and dates", "long" to "long summaries that take a while",
            "scanned" to "scanned pages", "language" to "languages not tested yet", "blurry" to "blurry photos",
            "handwriting" to "handwriting",
        )
    }
}
