package com.domovoi.app.assistant

import android.content.Context
import android.util.Log
import java.io.File

/**
 * DEBUG BUILDS ONLY (src/debug; the release build's DebugMicFeed answers
 * null): scripted speech in place of the microphone, for testing the voice
 * turn on the emulator without the host's microphone.
 *
 * A test pushes WAV files into the app's private `files/debug-mic-feed/`
 * (only `adb shell run-as` reaches it, and only on a debuggable build). The
 * next voice turn takes the first file by name, deletes it, and "hears" it
 * at the microphone's own pace (16 kHz in 32 ms chunks, in real time): a
 * quarter second of silence, the speech, then silence for as long as the
 * turn listens, so the voice detector decides where it ends exactly as it
 * does live. No file there: the real microphone, as in a release build.
 */
object DebugMicFeed {
    private const val TAG = "DebugMicFeed"
    const val DIR = "debug-mic-feed"

    fun next(context: Context): AudioSource? {
        val dir = File(context.filesDir, DIR)
        val file = dir.listFiles { f -> f.isFile && f.name.endsWith(".wav") }?.minByOrNull { it.name } ?: return null
        val wav = runCatching { parseWav(file.readBytes()) }.getOrNull()
        file.delete()
        if (wav == null) {
            Log.w(TAG, "${file.name}: not a WAV this can read; the microphone is used instead")
            return null
        }
        val speech = resample(wav.samples, wav.sampleRate, SPEECH_SAMPLE_RATE)
        Log.i(TAG, "feeding ${file.name}: ${"%.2f".format(speech.size / SPEECH_SAMPLE_RATE.toDouble())} s of speech")
        return PacedFeed(speech)
    }
}

/** [speech] (16 kHz floats) after a short lead of silence, then silence,
 *  delivered no faster than a microphone would. */
private class PacedFeed(speech: FloatArray) : AudioSource {
    private val pcm: ShortArray = ShortArray(LEAD + speech.size) { i ->
        if (i < LEAD) 0 else (speech[i - LEAD].coerceIn(-1f, 1f) * 32767f).toInt().toShort()
    }
    private var delivered = 0L
    private var startedNs = 0L
    @Volatile private var open = false

    override fun start() {
        startedNs = System.nanoTime()
        open = true
    }

    override fun read(buffer: ShortArray): Int {
        if (!open) return -1
        val dueNs = startedNs + (delivered + buffer.size) * 1_000_000_000L / SPEECH_SAMPLE_RATE
        val waitMs = (dueNs - System.nanoTime()) / 1_000_000
        if (waitMs > 0) Thread.sleep(waitMs)
        for (i in buffer.indices) {
            val at = delivered + i
            buffer[i] = if (at < pcm.size) pcm[at.toInt()] else 0
        }
        delivered += buffer.size
        return buffer.size
    }

    override fun stop() {
        open = false
    }

    private companion object {
        const val LEAD = SPEECH_SAMPLE_RATE / 4
    }
}
