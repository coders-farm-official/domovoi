package com.domovoi.app.assistant

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * What a voice turn runs on, opened on first use and kept until [close]
 * (the model's unload frees these with it): the microphone, the voice
 * detector, speech to text and the voice that speaks.
 */
interface VoiceKit : AutoCloseable {
    /** The mic for one utterance (one owner at a time: MicCapture.listen). */
    val mic: MicCapture

    suspend fun detector(): SpeechDetector

    suspend fun recognizer(): SpeechToText

    /** The voice that speaks the replies. */
    suspend fun speech(): SpeechOut

    /** Which voice speaks, in words, once it has been opened. */
    val voiceName: String?

    /** Loads what it can ahead of the first turn (best effort). */
    suspend fun warmUp() {
        runCatching { detector() }
        runCatching { recognizer() }
        runCatching { speech() }
    }
}

/**
 * The phone's: Silero VAD and Moonshine tiny through the app's sherpa-onnx
 * build (listening only; no text to speech is compiled in), and the phone's
 * own voice as Settings → Voice picks it ([phoneVoice]: Domovoi's Piper
 * voice once downloaded, the one the chat mic's replies use; Kristin by
 * default), else Android's own TextToSpeech, on the phone (owner decision
 * 2026-10-12, option A). The mic is the real one, or in a debug build a
 * scripted feed when a test has queued one ([DebugMicFeed]).
 */
class PhoneVoiceKit(
    context: Context,
    private val store: ModelStore,
    private val stt: CatalogModel = ModelCatalog.moonshineTinyEn,
    /** The phone's own Domovoi voice, opened WITHOUT asking any server
     *  (voice/PhoneVoice.openOnPhone); null when it is not downloaded. */
    private val phoneVoice: suspend () -> PhoneSpeech?,
) : VoiceKit {
    private val app = context.applicationContext
    private val lock = Mutex()
    private var vad: SherpaSpeechDetector? = null
    private var recognizerOpen: SpeechToText? = null
    private var out: PhoneSpeech? = null

    override val mic = MicCapture(source = { DebugMicFeed.next(app) ?: AndroidMic() })

    override var voiceName: String? = null
        private set

    override suspend fun detector(): SpeechDetector = lock.withLock {
        vad ?: withContext(Dispatchers.IO) { SherpaSpeechDetector.from(store) }.also { vad = it }
    }

    override suspend fun recognizer(): SpeechToText = lock.withLock {
        recognizerOpen ?: withContext(Dispatchers.IO) { SherpaSpeechToText.from(store, stt) }.also { recognizerOpen = it }
    }

    override suspend fun speech(): SpeechOut = lock.withLock {
        out?.let { return@withLock it }
        val opened = runCatching { phoneVoice() }.getOrNull() ?: AndroidSpeechOut.create(app)
        voiceName = (opened as? PhoneSpeech)?.let { describeVoice(it.voiceKey) }
        out = opened
        opened
    }

    override fun close() {
        runCatching { vad?.close() }
        runCatching { recognizerOpen?.close() }
        runCatching { out?.close() }
        vad = null
        recognizerOpen = null
        out = null
        voiceName = null
    }

    companion object {
        /** "Kristin" for a Domovoi voice, else the phone's own. */
        fun describeVoice(key: String): String =
            ModelCatalog.voice(key)?.name ?: "this phone's own text-to-speech voice"
    }
}

/** Audio focus for a voice turn through the chat mic's [VoiceFocus]
 *  (voice/ReplyPlayer.kt): exclusive while the mic listens (the music
 *  pauses), may-duck while Domovoi speaks; ExoPlayer resumes on release. */
class PhoneTurnFocus(context: android.content.Context) : TurnFocus {
    private val focus = com.domovoi.app.voice.VoiceFocus(context.applicationContext)

    override fun capture() = focus.hold(com.domovoi.app.voice.VoiceFocus.Kind.CAPTURE)

    override fun reply() = focus.hold(com.domovoi.app.voice.VoiceFocus.Kind.REPLY)

    override fun release() = focus.release()
}
