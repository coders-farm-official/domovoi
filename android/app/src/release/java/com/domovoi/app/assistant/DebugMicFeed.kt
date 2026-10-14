package com.domovoi.app.assistant

import android.content.Context

/** Release builds: no scripted microphone feed, ever (the debug build's
 *  twin in src/debug feeds test audio on the emulator). */
object DebugMicFeed {
    @Suppress("UNUSED_PARAMETER")
    fun next(context: Context): AudioSource? = null
}
