package com.domovoi.app.assistant

import android.content.Context

/*
 * Pictures on the phone (N3, design-notes plan part 3): a phone chat can ask
 * about one photo, from the camera or the gallery, answered by the model on
 * the phone. The bench on the owner's Fold (N0, 2026-10-21) decided where:
 * the photo encoder on the GPU passed the gate (2.5 s to the first word,
 * 1.8 GB peak), on the CPU it failed (6.8 s, 2.8 GB). So pictures show only
 * on phones where the encoder loads on the GPU, found out once by trying
 * it ([VisionCheck]) and remembered per model file, app build and backend.
 *
 * The photo never leaves the phone and is never kept: it is held in memory
 * from the moment it is picked or taken until its reply ends
 * ([PendingPhoto]), and the conversation keeps a "photo" marker only
 * ([StoredMessage.photo]). This file is the pure part, unit-tested on the
 * JVM (PhoneVisionTest).
 */

/** What this phone's photo encoder does on the backend it is tried on. */
enum class VisionProbe {
    /** Not tried yet with this model file, app build and backend. */
    UNKNOWN,

    /** It loaded and read a test picture: photos are offered. */
    PASSED,

    /** It didn't (or the app died trying): no photo buttons. */
    FAILED,
}

/** Where the probe's answer is kept between runs: one string. */
interface VisionMemory {
    fun read(): String?

    /** Written at once (a crash right after must still find it). */
    fun write(value: String?)
}

class MemoryVisionMemory(var value: String? = null) : VisionMemory {
    override fun read(): String? = value
    override fun write(value: String?) {
        this.value = value
    }
}

/** The app's: the assistant's own SharedPreferences, committed at once. */
class PhoneVisionMemory(context: Context) : VisionMemory {
    private val prefs = context.applicationContext.getSharedPreferences("assistant", Context.MODE_PRIVATE)

    override fun read(): String? = prefs.getString(KEY, null)

    override fun write(value: String?) {
        prefs.edit().apply { if (value == null) remove(KEY) else putString(KEY, value) }.commit()
    }

    private companion object {
        const val KEY = "vision_probe"
    }
}

/**
 * What the probe's answer is good for: this model file (its id and hash),
 * this app build, the backend the text runs on and the one the encoder is
 * tried on. A new app version, a new model file or another backend tries
 * again.
 */
fun visionKey(model: CatalogModel, appBuild: String, text: LlmBackend, vision: LlmBackend): String =
    "${model.id}@${model.files.firstOrNull()?.sha256?.take(16).orEmpty()}|$appBuild|text=${text.label}|vision=${vision.label}"

/**
 * The GPU-vision probe's memory ([VisionProbe] for [key]). Before the encoder
 * is first tried, [trying] writes that it is being tried; a run that dies
 * there (a native crash in a GPU driver kills the process, no exception)
 * finds the mark at the next start and counts it as a failure, so a phone
 * whose driver crashes crashes once, not at every start.
 */
class VisionCheck(private val memory: VisionMemory, val key: String) {
    /** The answer for [key]. A try left unfinished reads as [VisionProbe.FAILED]
     *  (and is written so). */
    fun state(): VisionProbe {
        val raw = memory.read() ?: return VisionProbe.UNKNOWN
        val state = raw.substringBefore(SEP)
        if (raw.substringAfter(SEP, "") != key) return VisionProbe.UNKNOWN
        return when (state) {
            PASSED -> VisionProbe.PASSED
            FAILED -> VisionProbe.FAILED
            TRYING -> VisionProbe.FAILED.also { memory.write("$FAILED$SEP$key") }
            else -> VisionProbe.UNKNOWN
        }
    }

    /** The answer for [key] during a run: a try under way reads as not
     *  known yet (nothing is written). */
    fun stateNow(): VisionProbe {
        val raw = memory.read() ?: return VisionProbe.UNKNOWN
        if (raw.substringAfter(SEP, "") != key) return VisionProbe.UNKNOWN
        return when (raw.substringBefore(SEP)) {
            PASSED -> VisionProbe.PASSED
            FAILED -> VisionProbe.FAILED
            else -> VisionProbe.UNKNOWN
        }
    }

    /** A try for [key] was started and has no answer yet. */
    fun unfinished(): Boolean = memory.read() == "$TRYING$SEP$key"

    /** Forgets the answer (a try stopped part-way, by the person or a
     *  missing model file, is no verdict). */
    fun forget() = memory.write(null)

    fun trying() = memory.write("$TRYING$SEP$key")
    fun passed() = memory.write("$PASSED$SEP$key")
    fun failed() = memory.write("$FAILED$SEP$key")

    private companion object {
        const val SEP = "#"
        const val TRYING = "trying"
        const val PASSED = "passed"
        const val FAILED = "failed"
    }
}

/** A photo waiting for its question: a JPEG made on the phone ([PhotoPrep]),
 *  in memory only. [id] tells two photos apart (the bytes are not compared). */
class PendingPhoto(
    val jpeg: ByteArray,
    val width: Int,
    val height: Int,
    /** "camera" or "gallery". */
    val source: String,
    /** Decoding and resizing it took, ms. */
    val prepMs: Long,
    val id: Long = nextId(),
) {
    override fun equals(other: Any?): Boolean = other is PendingPhoto && other.id == id
    override fun hashCode(): Int = id.hashCode()
    override fun toString(): String = "PendingPhoto(#$id, ${width}x$height, ${jpeg.size} bytes, $source)"

    private companion object {
        private var last = 0L
        @Synchronized fun nextId(): Long = ++last
    }
}

object PhonePhotos {
    /** Image tokens per photo: Gemma 4's default, and the most the E2B file
     *  takes (it refused 560 on the bench). Whole pages read best at this
     *  budget (D0: 98.6% of a letter, against 84.8% for two halves). */
    const val IMAGE_TOKENS = 280

    /** The long edge a photo is scaled to before the model sees it (plan
     *  part 3); 280 tokens of 48 px cover about 800 x 800, so more is wasted. */
    const val LONG_EDGE = 1024

    const val JPEG_QUALITY = 90

    /** One photo per question (LiteRT-LM #2799: several images in one
     *  conversation can crash near the token limit). */
    const val MAX_PER_QUESTION = 1

    /** The question a photo sent with nothing typed is asked with. */
    const val DEFAULT_QUESTION = "What's in this photo? Describe it in two or three sentences."

    /** How a photo question reads to the model in later turns: the photo is
     *  gone, the words stay. */
    const val HISTORY_MARKER = "[shared a photo, no longer available]"

    /** The longest a photo answer may run before it is cut off, tokens. */
    const val MAX_ANSWER_TOKENS = 400

    /** What a photo turn adds to the system prompt. */
    const val PHOTO_INSTRUCTION =
        "The person has shared a photo with this question. Answer from what you can see in the photo, in one to " +
            "three short sentences. If you can't make something out, say so instead of guessing."

    /** The system prompt for a photo turn: the usual one, plus [PHOTO_INSTRUCTION]. */
    fun systemPrompt(base: String): String = "$base\n\n$PHOTO_INSTRUCTION"

    /** What the model is asked about the photo: what was typed, or [DEFAULT_QUESTION]. */
    fun question(typed: String): String = typed.trim().ifEmpty { DEFAULT_QUESTION }

    /** A photo question as later turns see it ([HISTORY_MARKER], then the words). */
    fun historyText(typed: String): String = listOf(HISTORY_MARKER, typed.trim()).filter { it.isNotEmpty() }.joinToString(" ")

    /** Biggest picture file read from the gallery, bytes: past this it is
     *  refused rather than read whole into memory. */
    const val MAX_PICKED_BYTES = 64L * 1024 * 1024
}

/**
 * Whether the camera and gallery buttons show in a phone chat: the model
 * takes pictures, the GPU probe passed, and nothing else is going on (no
 * reply being written, no voice turn, no photo already waiting: one per
 * question). Home-server chats never use this: their photos go to the server
 * as before.
 */
fun photoButtonsShown(
    modelTakesImages: Boolean,
    vision: VisionProbe,
    installed: Boolean,
    replying: Boolean,
    voiceIdle: Boolean,
    photoWaiting: Boolean,
): Boolean = modelTakesImages && vision == VisionProbe.PASSED && installed && !replying && voiceIdle && !photoWaiting

/** The same rule over the assistant's state. */
fun photoButtonsShown(ui: AssistantUi): Boolean = photoButtonsShown(
    modelTakesImages = ui.modelTakesImages,
    vision = ui.vision,
    installed = ui.installed,
    replying = ui.replying,
    voiceIdle = ui.voicePhase == VoicePhase.Idle,
    photoWaiting = ui.pendingPhoto != null,
)
