package com.domovoi.app.assistant

import java.util.Locale

/** The phone the numbers came from. */
data class DeviceFacts(
    val manufacturer: String,
    val model: String,
    val soc: String?,
    val android: String,
    val api: Int,
    val totalRamBytes: Long,
    val lowRamDevice: Boolean,
    val abi: String,
    val app: String,
)

data class LoadResult(
    val backend: LlmBackend,
    val ms: Long,
    /** The first load in this app process (no warm file cache). */
    val firstInProcess: Boolean,
    val audioEncoder: Boolean = false,
)

data class TextTurnResult(
    val prompt: String,
    /** Send to the first visible text. */
    val firstTextMs: Long?,
    val totalMs: Long,
    val stats: LlmStats?,
    val reply: String,
)

data class VoiceTurnResult(
    /** "microphone", or "spoken by the phone" (the self-test). */
    val source: String,
    val sttModel: String,
    val utteranceS: Double,
    /** Silence the detector waits for before it calls the utterance over. */
    val endOfSpeechWaitMs: Long,
    val transcript: String,
    /** End of speech (detector) to transcript. */
    val sttMs: Long,
    /** Transcript to the model's first text. */
    val llmFirstTextMs: Long?,
    /** Transcript to the first complete sentence. */
    val firstSentenceMs: Long?,
    /** First sentence to its first audio at the speaker. */
    val ttsFirstAudioMs: Long?,
    /** End of speech (detector) to the first spoken word. */
    val toFirstAudioMs: Long?,
    val reply: String,
    val error: String? = null,
)

/** One line of the voice samples. */
data class VoiceSampleResult(
    val line: String,
    /** Asked to speak to its first spoken word. */
    val firstWordMs: Long?,
    /** Asked to speak to done. */
    val totalMs: Long,
    /** Length of the speech (0 when the engine does not say). */
    val audioS: Double,
)

data class AudioInResult(
    val source: String,
    val instruction: String,
    val firstTextMs: Long?,
    val totalMs: Long,
    val reply: String,
    val stats: LlmStats?,
    val error: String? = null,
)

/** One reading during the heat loop. */
data class HeatSample(
    val atS: Double,
    val decodeTokensPerS: Double,
    val batteryTempC: Double?,
    val thermalStatus: Int?,
    val online: Boolean,
)

data class HeatResult(
    val plannedMinutes: Int,
    val samples: List<HeatSample>,
    val stoppedEarly: Boolean,
)

data class MemoryReading(
    val label: String,
    /** Peak resident memory of the app process so far (VmHWM). */
    val peakRssKb: Long?,
    val rssKb: Long?,
    val availableRamBytes: Long?,
)

data class TrafficReading(val label: String, val rxBytes: Long, val txBytes: Long, val online: Boolean)

data class BenchResults(
    val loads: List<LoadResult> = emptyList(),
    val textTurns: List<TextTurnResult> = emptyList(),
    val voiceTurns: List<VoiceTurnResult> = emptyList(),
    val audioIn: List<AudioInResult> = emptyList(),
    val heat: HeatResult? = null,
    val samples: List<VoiceSampleResult> = emptyList(),
    /** Which engine and voice spoke (SpeechVoiceInfo.describe). */
    val voice: String? = null,
    val memory: List<MemoryReading> = emptyList(),
    val traffic: List<TrafficReading> = emptyList(),
    val notes: List<String> = emptyList(),
) {
    val isEmpty: Boolean
        get() = loads.isEmpty() && textTurns.isEmpty() && voiceTurns.isEmpty() && audioIn.isEmpty() &&
            heat == null && samples.isEmpty() && notes.isEmpty()
}

/** Peak and current resident memory from `/proc/self/status` (kB). */
fun parseProcStatus(status: String): Pair<Long?, Long?> {
    fun field(name: String): Long? =
        status.lineSequence().firstOrNull { it.startsWith("$name:") }
            ?.substringAfter(':')?.trim()?.substringBefore(' ')?.toLongOrNull()
    return field("VmHWM") to field("VmRSS")
}

/** Median of [values], or null when empty. */
fun median(values: List<Double>): Double? {
    if (values.isEmpty()) return null
    val sorted = values.sorted()
    val mid = sorted.size / 2
    return if (sorted.size % 2 == 1) sorted[mid] else (sorted[mid - 1] + sorted[mid]) / 2
}

/** Heat-loop summary: decode speed over the first and the last minute. */
data class HeatSummary(
    val turns: Int,
    val minutes: Double,
    val firstMinuteTps: Double?,
    val lastMinuteTps: Double?,
    val minTps: Double?,
    val startTempC: Double?,
    val endTempC: Double?,
    val worstThermalStatus: Int?,
    val offlineTurns: Int,
)

fun summarizeHeat(samples: List<HeatSample>): HeatSummary {
    val end = samples.maxOfOrNull { it.atS } ?: 0.0
    val first = samples.filter { it.atS <= 60.0 }.map { it.decodeTokensPerS }
    val last = samples.filter { it.atS >= end - 60.0 }.map { it.decodeTokensPerS }
    return HeatSummary(
        turns = samples.size,
        minutes = end / 60.0,
        firstMinuteTps = first.takeIf { it.isNotEmpty() }?.average(),
        lastMinuteTps = last.takeIf { it.isNotEmpty() }?.average(),
        minTps = samples.minOfOrNull { it.decodeTokensPerS },
        startTempC = samples.firstNotNullOfOrNull { it.batteryTempC },
        endTempC = samples.lastOrNull { it.batteryTempC != null }?.batteryTempC,
        worstThermalStatus = samples.mapNotNull { it.thermalStatus }.maxOrNull(),
        offlineTurns = samples.count { !it.online },
    )
}

/** Android's PowerManager.THERMAL_STATUS_* names. */
fun thermalName(status: Int?): String = when (status) {
    null -> "unknown"
    0 -> "none"
    1 -> "light"
    2 -> "moderate"
    3 -> "severe"
    4 -> "critical"
    5 -> "emergency"
    6 -> "shutdown"
    else -> "status $status"
}

private fun ms(v: Long?): String = v?.let { "$it ms" } ?: "—"
private fun f1(v: Double?): String = v?.let { String.format(Locale.US, "%.1f", it) } ?: "—"
private fun gb(bytes: Long?): String = bytes?.let { String.format(Locale.US, "%.1f GB", it / 1e9) } ?: "—"
private fun mb(kb: Long?): String = kb?.let { String.format(Locale.US, "%.0f MB", it / 1024.0) } ?: "—"
private fun clip(text: String, max: Int = 160): String =
    text.replace('\n', ' ').let { if (it.length > max) it.take(max - 1) + "…" else it }

/**
 * The benchmark's results as plain text: what "copy results" puts on the
 * clipboard, in the order the M0 gate reads them.
 */
fun renderBenchReport(device: DeviceFacts, results: BenchResults, madeAt: String): String = buildString {
    appendLine("Domovoi on-device assistant benchmark (M0)")
    appendLine("made $madeAt")
    appendLine("phone: ${device.manufacturer} ${device.model}${device.soc?.let { ", $it" } ?: ""}")
    appendLine("Android ${device.android} (API ${device.api}), ${device.abi}, RAM ${gb(device.totalRamBytes)}" +
        if (device.lowRamDevice) ", low-RAM device" else "")
    appendLine("app ${device.app}")
    appendLine("models: ${ModelCatalog.gemma4E2b.name} (LiteRT-LM 0.18.0); listening: sherpa-onnx 1.13.8 " +
        "built without text to speech, on ONNX Runtime 1.28.0; speaking: Android TextToSpeech")
    results.voice?.let { appendLine("voice: $it") }

    if (results.loads.isNotEmpty()) {
        appendLine()
        appendLine("LOAD (target: 10 s or less, cold)")
        results.loads.forEach {
            appendLine("  ${it.backend}${if (it.audioEncoder) " + audio encoder" else ""}: ${ms(it.ms)}" +
                if (it.firstInProcess) " (first in this app run)" else "")
        }
    }

    if (results.textTurns.isNotEmpty()) {
        val turns = results.textTurns
        appendLine()
        appendLine("TEXT TURNS (targets: first words 1.5 s or less, decode 8 tokens/s or more)")
        appendLine("  median first text: ${ms(median(turns.mapNotNull { it.firstTextMs?.toDouble() })?.toLong())}")
        appendLine("  median decode: ${f1(median(turns.mapNotNull { it.stats?.decodeTokensPerS }))} tokens/s, " +
            "prefill ${f1(median(turns.mapNotNull { it.stats?.prefillTokensPerS }))} tokens/s")
        turns.forEachIndexed { i, t ->
            appendLine("  ${i + 1}. first text ${ms(t.firstTextMs)}, total ${ms(t.totalMs)}, " +
                "decode ${f1(t.stats?.decodeTokensPerS)} t/s over ${t.stats?.decodeTokens ?: "—"} tokens, " +
                "prefill ${t.stats?.prefillTokens ?: "—"} tokens")
            appendLine("     Q: ${clip(t.prompt, 80)}")
            appendLine("     A: ${clip(t.reply)}")
        }
    }

    if (results.voiceTurns.isNotEmpty()) {
        appendLine()
        appendLine("VOICE TURNS (target: 3 s or less from end of speech to the first spoken word)")
        results.voiceTurns.forEachIndexed { i, v ->
            if (v.error != null) {
                appendLine("  ${i + 1}. ${v.source}: failed: ${v.error}")
                return@forEachIndexed
            }
            val withWait = v.toFirstAudioMs?.plus(v.endOfSpeechWaitMs)
            appendLine("  ${i + 1}. ${v.source}, ${v.sttModel}: end of speech to first word ${ms(v.toFirstAudioMs)} " +
                "(${ms(withWait)} counting the ${v.endOfSpeechWaitMs} ms silence wait)")
            appendLine("     speech to text ${ms(v.sttMs)} for ${f1(v.utteranceS)} s of audio; model first text " +
                "${ms(v.llmFirstTextMs)}; first sentence ${ms(v.firstSentenceMs)}; voice to its first word ${ms(v.ttsFirstAudioMs)}")
            appendLine("     heard: ${clip(v.transcript, 100)}")
            appendLine("     said: ${clip(v.reply)}")
        }
    }

    if (results.samples.isNotEmpty()) {
        val samples = results.samples
        appendLine()
        appendLine("VOICE SAMPLES (this phone's voice reading the comparison lines)")
        appendLine("  median to the first word: ${ms(median(samples.mapNotNull { it.firstWordMs?.toDouble() })?.toLong())}")
        samples.forEach { s ->
            val n = VoiceSamples.LINES.indexOf(s.line).takeIf { it >= 0 }?.let { "${it + 1}" } ?: "-"
            appendLine("  $n. first word ${ms(s.firstWordMs)}, done ${ms(s.totalMs)}" +
                (if (s.audioS > 0) ", ${f1(s.audioS)} s of speech" else "") + ": ${clip(s.line, 70)}")
        }
    }

    if (results.audioIn.isNotEmpty()) {
        appendLine()
        appendLine("AUDIO INPUT EXPERIMENT (the model hears the question itself)")
        results.audioIn.forEachIndexed { i, a ->
            if (a.error != null) {
                appendLine("  ${i + 1}. ${a.source}: failed: ${a.error}")
                return@forEachIndexed
            }
            appendLine("  ${i + 1}. ${a.source}: first text ${ms(a.firstTextMs)}, total ${ms(a.totalMs)}, " +
                "prefill ${a.stats?.prefillTokens ?: "—"} tokens")
            appendLine("     asked: ${clip(a.instruction, 80)}")
            appendLine("     A: ${clip(a.reply)}")
        }
    }

    results.heat?.let { heat ->
        val s = summarizeHeat(heat.samples)
        appendLine()
        appendLine("HEAT LOOP (target: decode stays above about 4 tokens/s)")
        appendLine("  ${s.turns} replies over ${f1(s.minutes)} of ${heat.plannedMinutes} minutes" +
            if (heat.stoppedEarly) " (stopped early)" else "")
        appendLine("  decode first minute ${f1(s.firstMinuteTps)} t/s, last minute ${f1(s.lastMinuteTps)} t/s, " +
            "lowest ${f1(s.minTps)} t/s")
        appendLine("  battery ${f1(s.startTempC)} °C → ${f1(s.endTempC)} °C; worst thermal status " +
            thermalName(s.worstThermalStatus))
        if (s.offlineTurns > 0) appendLine("  ${s.offlineTurns} of ${s.turns} replies ran with no network")
    }

    if (results.memory.isNotEmpty()) {
        appendLine()
        appendLine("MEMORY (app process)")
        results.memory.forEach {
            appendLine("  ${it.label}: peak ${mb(it.peakRssKb)}, now ${mb(it.rssKb)}, phone free ${gb(it.availableRamBytes)}")
        }
    }

    // Only the traffic DURING a test: each "before …" reading paired with
    // the one that ends that test, not the idle time between tests.
    // A test that failed before its closing reading leaves two "before"
    // readings in a row; that pair is not a test either.
    val during = results.traffic.zipWithNext()
        .filter { (a, b) -> a.label.startsWith("before ") && !b.label.startsWith("before ") }
    if (during.isNotEmpty()) {
        appendLine()
        appendLine("NETWORK (this app's traffic, all of it, including a connected server)")
        during.forEach { (a, b) ->
            appendLine("  during the ${b.label}: ${b.rxBytes - a.rxBytes} B in, ${b.txBytes - a.txBytes} B out" +
                when {
                    !a.online && !b.online -> " (no network)"
                    !b.online -> " (network gone by the end)"
                    else -> ""
                })
        }
    }

    if (results.notes.isNotEmpty()) {
        appendLine()
        appendLine("NOTES")
        results.notes.forEach { appendLine("  $it") }
    }
}.trimEnd()
