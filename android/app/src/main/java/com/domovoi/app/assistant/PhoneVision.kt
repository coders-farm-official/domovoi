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

    /** It didn't load or failed its test picture: no photo buttons. */
    FAILED,

    /** The app crashed while the encoder ran (a GPU driver's native
     *  crash): no photo buttons, and the model settings say why. */
    CRASHED,
}

/** Where the probe's answer and its crash guard are kept between runs. */
interface VisionMemory {
    fun read(): String?

    /** Written at once (a crash right after must still find it). */
    fun write(value: String?)

    fun readGuard(): String?

    fun writeGuard(value: String?)
}

class MemoryVisionMemory(var value: String? = null, var guard: String? = null) : VisionMemory {
    override fun read(): String? = value
    override fun write(value: String?) {
        this.value = value
    }
    override fun readGuard(): String? = guard
    override fun writeGuard(value: String?) {
        guard = value
    }
}

/** The app's: the assistant's own SharedPreferences, committed at once. */
class PhoneVisionMemory(context: Context) : VisionMemory {
    private val prefs = context.applicationContext.getSharedPreferences("assistant", Context.MODE_PRIVATE)

    override fun read(): String? = prefs.getString(KEY, null)

    override fun write(value: String?) = put(KEY, value)

    override fun readGuard(): String? = prefs.getString(GUARD, null)

    override fun writeGuard(value: String?) = put(GUARD, value)

    private fun put(key: String, value: String?) {
        prefs.edit().apply { if (value == null) remove(key) else putString(key, value) }.commit()
    }

    private companion object {
        const val KEY = "vision_probe"
        const val GUARD = "vision_guard"
    }
}

/** How the app's last run ended, as Android recorded it. */
enum class ExitKind {
    /** A crash (Java or native) or an ANR: the encoder is to blame. */
    CRASH,

    /** Killed for memory, swiped away, stopped, updated...: not the
     *  encoder's doing; the probe tries again. */
    NOT_A_CRASH,

    /** Android doesn't say (before Android 11, or no record). */
    UNKNOWN,
}

/** Why the app's process last ended at or after a moment (epoch ms). */
fun interface ExitReasons {
    fun since(ms: Long): ExitKind
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
 * The GPU-vision probe's memory ([VisionProbe] for [key]) and its crash
 * guard. Every use of the encoder (its load and test picture, and every
 * photo turn) is wrapped in [begin] / [end]: [begin] writes a mark with the
 * time, [end] clears it. A native crash in a GPU driver kills the process
 * with no exception, so the mark is still there at the next start
 * ([resolveAtStart]), which asks Android how that run ended ([ExitReasons],
 * Android 11+): a crash or an ANR is [VisionProbe.CRASHED] (no photos until
 * the next update or a new model file); a memory kill or a swipe away
 * changes nothing (the probe tries again). Where Android can't say, the
 * first such death is forgiven and the second is a crash ([MAX_STRIKES]).
 */
class VisionCheck(private val memory: VisionMemory, val key: String) {
    private data class Guard(val key: String, val since: Long, val strikes: Int)

    private fun guard(): Guard? = memory.readGuard()?.split(SEP)?.takeIf { it.size == 3 }?.let { p ->
        val since = p[1].toLongOrNull() ?: return null
        val strikes = p[2].toIntOrNull() ?: return null
        Guard(p[0], since, strikes)
    }

    private fun writeGuard(since: Long, strikes: Int) = memory.writeGuard("$key$SEP$since$SEP$strikes")

    /** The answer for [key]. */
    fun state(): VisionProbe {
        val raw = memory.read() ?: return VisionProbe.UNKNOWN
        if (raw.substringAfter(SEP, "") != key) return VisionProbe.UNKNOWN
        return when (raw.substringBefore(SEP)) {
            PASSED -> VisionProbe.PASSED
            FAILED -> VisionProbe.FAILED
            CRASHED -> VisionProbe.CRASHED
            else -> VisionProbe.UNKNOWN
        }
    }

    /**
     * Once per run, before the encoder is used: a mark the last run left
     * means it died while the encoder ran. Returns the answer after that.
     */
    fun resolveAtStart(exits: ExitReasons): VisionProbe {
        val g = guard() ?: return state()
        if (g.key != key) {
            memory.writeGuard(null)
            return state()
        }
        if (g.since <= 0) return state()        // only strikes kept: no run under way
        when (exits.since(g.since)) {
            ExitKind.CRASH -> crashed()
            ExitKind.NOT_A_CRASH -> writeGuard(0, g.strikes)
            ExitKind.UNKNOWN -> if (g.strikes + 1 >= MAX_STRIKES) crashed() else writeGuard(0, g.strikes + 1)
        }
        return state()
    }

    /** The encoder is about to run: the mark, with the time. */
    fun begin(now: Long) = writeGuard(now.coerceAtLeast(1), guard()?.takeIf { it.key == key }?.strikes ?: 0)

    /** The encoder's run ended (well or not) without taking the app down. */
    fun end() = memory.writeGuard(null)

    /** The encoder's run is under way (a mark for [key] with a time). */
    fun running(): Boolean = guard()?.let { it.key == key && it.since > 0 } == true

    /** Forgets the answer and the guard. */
    fun forget() {
        memory.write(null)
        memory.writeGuard(null)
    }

    fun passed() = memory.write("$PASSED$SEP$key")
    fun failed() = memory.write("$FAILED$SEP$key")
    private fun crashed() {
        memory.write("$CRASHED$SEP$key")
        memory.writeGuard(null)
    }

    companion object {
        private const val SEP = "#"
        private const val PASSED = "passed"
        private const val FAILED = "failed"
        private const val CRASHED = "crashed"

        /** Deaths Android can't explain before the encoder counts as crashing. */
        const val MAX_STRIKES = 2
    }
}

/** What the model settings say when photos are off because of a crash. */
const val VISION_CRASHED_NOTE =
    "Photos are off on this phone: the app closed unexpectedly while reading one. They'll be tried again after " +
        "the next app update."

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
