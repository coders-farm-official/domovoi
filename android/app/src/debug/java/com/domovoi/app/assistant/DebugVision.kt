package com.domovoi.app.assistant

import android.content.Context
import java.io.File

/**
 * DEBUG BUILDS ONLY (src/debug; the release build's DebugVision answers
 * null): where the photo encoder runs, for testing pictures on a device
 * without a GPU that takes it (the emulator). A file named `debug-vision-cpu`
 * in the app's private files (only `adb shell run-as` reaches it, and only on
 * a debuggable build) puts the encoder on the CPU; without it, the GPU, as in
 * a release build. The probe's answer is kept per backend (visionKey), so
 * the file's coming or going tries again.
 */
object DebugVision {
    const val CPU_FLAG = "debug-vision-cpu"

    fun backend(context: Context): LlmBackend? =
        if (File(context.filesDir, CPU_FLAG).exists()) LlmBackend.CPU else null
}
