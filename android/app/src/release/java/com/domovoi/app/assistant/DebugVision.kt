package com.domovoi.app.assistant

import android.content.Context

/** Release builds: the photo encoder always goes on the GPU (the debug
 *  build's twin in src/debug can move it, for the emulator). */
object DebugVision {
    @Suppress("UNUSED_PARAMETER")
    fun backend(context: Context): LlmBackend? = null
}
