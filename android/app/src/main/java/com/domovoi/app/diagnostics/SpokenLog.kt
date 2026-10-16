package com.domovoi.app.diagnostics

import com.domovoi.app.BuildConfig

/**
 * What logcat may say about spoken or transcribed words (delta security
 * review 2026-10-16, N7): the words themselves in a debug build, only how
 * many there were in a release build. Any app on a phone with a debugging
 * cable, a bug report, or a vendor's log collector can read logcat, so a
 * room phone's "heard: …" and the on-device voice's sentences stay out of
 * it on the builds households install. The server's twin is
 * `domovoi/log_privacy.py`'s `said()`.
 */
object SpokenLog {
    /** Whether logcat may carry the words: debug builds only. */
    val wordsAllowed: Boolean get() = BuildConfig.DEBUG

    /** [text] for a log line: as it is when [words], else `<n words>`. */
    fun said(text: String?, words: Boolean = wordsAllowed): String {
        val t = text.orEmpty()
        if (words) return t
        val n = t.trim().split(Regex("\\s+")).count { it.isNotEmpty() }
        return "<$n word${if (n == 1) "" else "s"}>"
    }
}
