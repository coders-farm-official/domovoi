package com.domovoi.app.assistant

import com.domovoi.app.assistant.docs.DocChunk
import com.domovoi.app.assistant.docs.DocLong
import com.domovoi.app.assistant.docs.DocNote
import com.domovoi.app.assistant.docs.DocSpeed
import com.domovoi.app.assistant.docs.SpeedRow
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import kotlin.random.Random

/**
 * A summary of a long document read in parts on this phone (documents plan
 * D2), kept with its conversation ([StoredConversation.job]) so it survives
 * the app leaving the screen or being killed: each part's notes are saved as
 * the part finishes, and a job carries on from its next part when Chat is
 * open again. Every field has a default (a file from another version still
 * reads).
 */
@Serializable
data class PhoneDocJob(
    val id: String = "",
    /** What the person asked ("summarize the lease"). */
    val question: String = "",
    /** The conversation's documents it reads, by id, in order. */
    val docIds: List<String> = emptyList(),
    val chunks: List<DocChunk> = emptyList(),
    /** One entry per chunk: its checked notes, or null for a part not read yet. */
    val notes: List<String?> = emptyList(),
    /** running | paused (Chat left: carries on when it's back) | stopped
     *  (the person stopped it: "carry on" reads the rest) | server (handed
     *  to the home server, [serverJob]). */
    val state: String = STATE_RUNNING,
    /** The time estimated when it started, from measured speeds; null when
     *  nothing was measured yet. */
    val estimateMs: Long? = null,
    val started: Long = 0,
    /** Each part read: how long it took and how many characters it was. */
    val partMs: List<Long> = emptyList(),
    val partChars: List<Int> = emptyList(),
    /** The job's own counts (the engine's), for its time left. */
    val stats: SpeedRow = SpeedRow(),
    val steps: Int = 0,
    /** The home server's private job reading it, once handed over. */
    val serverJob: String? = null,
    /** Where its parts were sized to run: "GPU" (an 8K engine loaded for
     *  the job) or "CPU" (the chat's 4K engine). */
    val backend: String = "CPU",
) {
    val read: Int get() = notes.count { it != null }
    val total: Int get() = chunks.size

    fun remainingChars(): Int = chunks.indices.filter { notes.getOrNull(it) == null }.sumOf { chunks[it].chars }

    fun remaining(): List<Int> = chunks.indices.filter { notes.getOrNull(it) == null }.map { chunks[it].chars }

    fun done(): List<Pair<Int, Double>> = partChars.zip(partMs.map { it / 1000.0 })

    fun jobSpeed(): DocSpeed? = if ((stats.promptTokens ?: 0.0) > 0 && (stats.outputTokens ?: 0.0) > 0) DocLong.speedFromStats(listOf(stats)) else null

    companion object {
        const val STATE_RUNNING = "running"
        const val STATE_PAUSED = "paused"
        const val STATE_STOPPED = "stopped"
        const val STATE_SERVER = "server"

        /** The home server couldn't finish it (failed, lost, out of reach):
         *  it waits for the person to read it on this phone (review round 1). */
        const val STATE_HELD = "held"
    }
}

/** What the progress card shows of a job. */
data class DocJobView(
    val id: String,
    val conversation: String,
    /** reading | reducing | paused | stopped | server */
    val state: String,
    /** The part being read (1-based), or the parts read when not reading. */
    val part: Int,
    val parts: Int,
    val read: Int,
    /** Time left, seconds; null until something was measured. */
    val etaS: Double?,
    val line: String,
    val names: List<String>,
    /** Characters still to read on this phone. */
    val remainingChars: Int,
    /** The whole document's characters: what the home server would read
     *  (it starts from the beginning; review round 1). */
    val totalChars: Int = 0,
    /** The job reads one document (the home server takes one file). */
    val single: Boolean,
)

/** The pure rules of phone document jobs (tested on the JVM). */
object PhoneDocJobs {
    /** Speeds kept per engine (CPU, GPU): this many replies' figures. */
    const val MAX_SPEED_ROWS = 20

    /** A file reaches the home server at about this rate (a conservative
     *  home Wi-Fi), added to the server's time before it is compared. */
    const val UPLOAD_BYTES_PER_S = 1_500_000.0

    fun newId(now: Long): String = "j-$now-${Random.nextInt(0, 1 shl 30).toString(36)}"

    /** One reply's figures from the engine's counters ([LlmStats]): what the
     *  server keeps in `chat_messages.stats`, for the same estimate. */
    fun row(s: LlmStats?, promptChars: Int): SpeedRow? {
        if (s == null || s.prefillTokens <= 0 || s.prefillTokensPerS <= 0 || s.decodeTokens <= 0 || s.decodeTokensPerS <= 0) return null
        return SpeedRow(
            promptTokens = s.prefillTokens.toDouble(),
            promptMs = s.prefillTokens / s.prefillTokensPerS * 1000.0,
            outputTokens = s.decodeTokens.toDouble(),
            generateMs = s.decodeTokens / s.decodeTokensPerS * 1000.0,
            promptChars = promptChars.toDouble().takeIf { it > 0 },
        )
    }

    /** [a] and [b] added up (a job's running total). */
    fun plus(a: SpeedRow, b: SpeedRow?): SpeedRow {
        if (b == null) return a
        fun add(x: Double?, y: Double?) = if (x == null && y == null) null else (x ?: 0.0) + (y ?: 0.0)
        return SpeedRow(add(a.promptTokens, b.promptTokens), add(a.promptMs, b.promptMs), add(a.outputTokens, b.outputTokens),
            add(a.generateMs, b.generateMs), add(a.promptChars, b.promptChars))
    }

    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }
    private val rowsSerializer = MapSerializer(String.serializer(), ListSerializer(SpeedRow.serializer()))

    /** The kept rows per engine, from the settings' JSON. */
    fun readRows(text: String): Map<String, List<SpeedRow>> =
        if (text.isBlank()) emptyMap() else runCatching { json.decodeFromString(rowsSerializer, text) }.getOrDefault(emptyMap())

    /** [text] with [row] added for [backend], the newest [MAX_SPEED_ROWS] kept. */
    fun addRow(text: String, backend: LlmBackend, row: SpeedRow): String {
        val all = readRows(text).toMutableMap()
        all[backend.name] = (all[backend.name].orEmpty() + row).takeLast(MAX_SPEED_ROWS)
        return json.encodeToString(rowsSerializer, all)
    }

    fun speed(text: String, backend: LlmBackend): DocSpeed? = DocLong.speedFromStats(readRows(text)[backend.name].orEmpty())

    /** Time left in [job], seconds ([partStartedMs]: when the part being read
     *  began, so the time counts down while it is read). */
    fun eta(job: PhoneDocJob, fallback: DocSpeed?, partStartedMs: Long?, now: Long, reducing: Boolean): Double? {
        val left = DocLong.etaSeconds(job.done(), job.remaining(), job.jobSpeed(), fallback) ?: return null
        val gone = if (partStartedMs != null) (now - partStartedMs) / 1000.0 else 0.0
        val floor = if (reducing || job.remaining().isNotEmpty()) 1.0 else 0.0
        return maxOf(floor, left - gone)
    }

    fun view(conversation: String, job: PhoneDocJob, names: List<String>, state: String, eta: Double?): DocJobView {
        val part = if (state == "reading") minOf(job.read + 1, job.total) else job.read
        val line = when (state) {
            "reading", "reducing" -> DocLong.progressLine(state, part, job.total, eta)
            "paused" -> DocLong.progressLine("paused", minOf(job.read + 1, job.total), job.total, null)
            "held" -> HELD_LINE
            else -> ""
        }
        return DocJobView(job.id, conversation, state, part, job.total, job.read, eta, line, names, job.remainingChars(),
            job.chunks.sumOf { it.chars }, job.docIds.size == 1)
    }

    /**
     * The home-server offer (plan part 4): its time when the server's
     * measured estimate, plus sending the file, beats this phone's time
     * left; null otherwise: AI features off there, nothing measured on
     * either side, or the phone is as fast. The phone always works on its
     * own; the offer is never a requirement.
     */
    fun offer(phoneEtaS: Double?, serverEstimateS: Double?, uploadBytes: Long, serverAi: Boolean): Double? {
        if (!serverAi || phoneEtaS == null || serverEstimateS == null) return null
        val total = serverEstimateS + uploadBytes / UPLOAD_BYTES_PER_S
        return if (total < phoneEtaS) total else null
    }

    /** What "use home server" does, said before it is tapped. */
    fun handOverText(name: String): String = "Sends $name to your home server to read. Nothing is kept there."

    /** A job the home server couldn't finish, waiting for the person. */
    const val HELD_LINE = "the home server couldn't finish it · read it on this phone?"

    /**
     * What a view of the home server's job means for this phone: "progress",
     * "failed" (its error, or a "result" with no words: never filed as a
     * summary; review round 1) or "done".
     */
    fun serverOutcome(live: Boolean, hasError: Boolean, content: String?): String = when {
        hasError -> "failed"
        live -> "progress"
        content.isNullOrBlank() -> "failed"
        else -> "done"
    }

    /** The note under a summary the home server wrote. */
    val SERVER_NOTE = DocNote("read_by_server", "Read by your home server; nothing was kept there.")
}

/** What the screen needs to send a job to the home server. */
data class HandOver(val conversation: String, val jobId: String, val file: java.io.File, val name: String, val question: String)
