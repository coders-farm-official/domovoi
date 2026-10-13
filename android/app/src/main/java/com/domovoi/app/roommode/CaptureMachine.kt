package com.domovoi.app.roommode

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** Why a capture ended (the `utterance_end` `exit_reason`). */
enum class ExitReason(val wire: String) {
    SILENCE("vad_silence_after_speech"),
    MAX_LENGTH("max_record_seconds"),
    SHUTDOWN("shutdown"),
    SERVER("server_endpoint"),
}

/** The listening rules: the Pi's `[listen]` defaults, in 30 ms frames. */
data class CaptureRules(
    /** Silence after speech that ends a capture (`silence_timeout`, 1.2 s). */
    val silenceLimit: Int = 1_200 / RoomProtocol.FRAME_MS,
    /** The longest capture (`max_record_seconds`, 30 s). */
    val maxFrames: Int = 30_000 / RoomProtocol.FRAME_MS,
    /** Silence after speech worth a `speech_pause` (240 ms). */
    val pauseFrames: Int = 8,
    /** How long a follow-up waits for the person to start (8 s). */
    val followupPreSpeech: Int = 8_000 / RoomProtocol.FRAME_MS,
)

/**
 * One capture, frame by frame — the Pi's `_capture` loop without the mic or
 * the socket (satellite/client.py). Fed one 30 ms frame's verdict at a
 * time, it says what goes to the core around that frame's audio:
 *
 * - every frame's audio, in order;
 * - `speech_resume` BEFORE the audio of the frame that ends a reported
 *   pause, so every frame the core receives while a pause stands is one
 *   the phone called silence (the early-commit hold counts on it);
 * - `speech_pause` after the [CaptureRules.pauseFrames]th silent frame of
 *   a run (once per run), with the frame count and the last voiced frame;
 * - the end: silence after speech, the length cap, the core's own
 *   `end_capture`, or — for a follow-up nobody answers — giving up with no
 *   `utterance_end` at all, so the core never transcribes pure silence.
 *
 * [hints] is whether the core's `ready` listed `speech_pause`: an older core
 * answers an unknown type with `error`.
 */
class CaptureMachine(
    val utt: Int,
    private val rules: CaptureRules = CaptureRules(),
    private val hints: Boolean = true,
    /** Follow-up captures stop waiting after this many silent frames. */
    private val preSpeechLimit: Int? = null,
) {
    /** What to do around one frame. */
    data class Step(
        /** Send `speech_resume` (with [resumeFrame]) before this frame's audio. */
        val resumeBefore: Boolean = false,
        val resumeFrame: Int = 0,
        /** Send this frame's audio. */
        val sendAudio: Boolean = true,
        /** Send `speech_pause` after it (fields from [pauseFields]). */
        val pauseAfter: Boolean = false,
        /** The capture is over (after this frame). */
        val end: ExitReason? = null,
        /** A follow-up nobody answered: no `utterance_end`, back to the wake word. */
        val abandoned: Boolean = false,
    )

    var sent = 0; private set
    var lastVoiced = -1; private set
    var voicedFrames = 0; private set
    var silentFrames = 0; private set
    var speaking = false; private set
    var ended: ExitReason? = null; private set
    private var pauseReported = false
    private var preSpeechSilent = 0

    fun onFrame(isSpeech: Boolean): Step {
        check(ended == null) { "the capture is over" }
        var resume = false
        var resumeAt = 0
        if (isSpeech && pauseReported) {
            pauseReported = false
            if (hints) {
                resume = true
                resumeAt = sent
            }
        }
        sent += 1
        var pause = false
        var end: ExitReason? = null
        when {
            isSpeech -> {
                speaking = true
                silentFrames = 0
                preSpeechSilent = 0
                voicedFrames += 1
                lastVoiced = sent - 1
            }
            speaking -> {
                silentFrames += 1
                if (silentFrames == rules.pauseFrames && hints) {
                    pauseReported = true
                    pause = true
                }
                if (silentFrames >= rules.silenceLimit) end = ExitReason.SILENCE
            }
            preSpeechLimit != null -> {
                preSpeechSilent += 1
                if (preSpeechSilent >= preSpeechLimit) {
                    ended = ExitReason.SILENCE
                    return Step(resumeBefore = resume, resumeFrame = resumeAt, pauseAfter = false, abandoned = true)
                }
            }
        }
        if (end == null && sent >= rules.maxFrames) end = ExitReason.MAX_LENGTH
        ended = end
        return Step(resumeBefore = resume, resumeFrame = resumeAt, pauseAfter = pause, end = end)
    }

    /** The core's `end_capture` for this capture (or shutdown). */
    fun endNow(reason: ExitReason) {
        if (ended == null) ended = reason
    }

    /** `speech_pause`'s fields, after the frame that earned it. */
    fun pausePayload(ack: Map<String, JsonElement>, clock: Map<String, Int>): JsonObject = buildJsonObject {
        put("type", "speech_pause")
        put("utt", utt)
        put("frame", sent)
        put("last_voiced_frame", lastVoiced)
        ack.forEach { (k, v) -> put(k, v) }
        clock.forEach { (k, v) -> put(k, v) }
    }

    fun resumePayload(frame: Int, clock: Map<String, Int>): JsonObject = buildJsonObject {
        put("type", "speech_resume")
        put("utt", utt)
        put("frame", frame)
        clock.forEach { (k, v) -> put(k, v) }
    }

    /** `utterance_end`, with exact frame accounting for the core. */
    fun endPayload(
        ack: Map<String, JsonElement>,
        clock: Map<String, Int>,
        afterEnd: Map<String, Int> = emptyMap(),
    ): JsonObject = buildJsonObject {
        put("type", "utterance_end")
        ack.forEach { (k, v) -> put(k, v) }
        put("utt", utt)
        put("frames", sent)
        if (lastVoiced >= 0) put("last_voiced_frame", lastVoiced) else put("last_voiced_frame", kotlinx.serialization.json.JsonNull)
        put("exit_reason", (ended ?: ExitReason.SHUTDOWN).wire)
        put("voiced_frames", voicedFrames)
        put("trailing_silent_frames", silentFrames)
        put("silence_limit_frames", rules.silenceLimit)
        afterEnd.forEach { (k, v) -> put(k, v) }
        clock.forEach { (k, v) -> put(k, v) }
    }

    /** How many more frames the phone would have listened, after the core
     *  ended it (the Pi's `_listen_after_end` budget). */
    fun listenAfterEndBudget(): Int =
        maxOf(0, minOf(rules.silenceLimit - silentFrames, rules.maxFrames - sent))

    companion object {
        /** What the capture says about the wake acknowledgement. */
        fun ackFields(greetingClip: String?, ackBeforeCapture: Boolean): Map<String, JsonElement> = buildMap {
            put("greeting_played", JsonPrimitive(greetingClip != null))
            if (greetingClip != null) put("greeting_clip", JsonPrimitive(greetingClip))
            if (ackBeforeCapture) put("ack_before_capture", JsonPrimitive(true))
        }
    }
}
