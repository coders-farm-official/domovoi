package com.domovoi.app.assistant

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import kotlin.random.Random

/** Who said a message in an on-phone conversation. */
@Serializable
enum class Speaker { USER, DOMOVOI }

/**
 * One message of an on-phone conversation, as it is kept. Every field has a
 * default, so a file written by an older (or newer) app still reads
 * (android/CONVENTIONS.md: tolerate missing fields).
 */
@Serializable
data class StoredMessage(
    val from: Speaker = Speaker.USER,
    val text: String = "",
    /** When it was sent or finished, epoch ms. */
    val at: Long = 0,
    /** Why the reply failed, in plain words; null for a good one. */
    val error: String? = null,
    /** The reply was stopped part-way by the user. */
    val stopped: Boolean = false,
    /** Send to the first words on screen, ms. */
    val firstTextMs: Long? = null,
    /** The model's own decode speed for this reply, tokens a second. */
    val tokensPerS: Double? = null,
    /** Where the model ran: "cpu" or "gpu". */
    val backend: String? = null,
    /** A question asked by voice (its text is the transcript; the audio
     *  itself is never kept). */
    val spoken: Boolean = false,
    /** A spoken turn's reply: the end of speech to its first spoken word, ms
     *  (the voice detector's own silence wait not included). */
    val voiceMs: Long? = null,
    /** ...the silence the detector waited for before deciding, ms. */
    val silenceWaitMs: Long? = null,
    /** ...speech to text, ms. */
    val sttMs: Long? = null,
    /** A reply stopped while it was spoken: the tap to the voice silenced, ms. */
    val stopMs: Long? = null,
    /** A reply from the phone's own clock (ClockQuestions), not the model. */
    val fromClock: Boolean = false,
) {
    val fromUser: Boolean get() = from == Speaker.USER
}

@Serializable
data class StoredConversation(
    val id: String = "",
    val created: Long = 0,
    val updated: Long = 0,
    val messages: List<StoredMessage> = emptyList(),
) {
    /** The first thing the user asked, cut short: the history list's title. */
    val title: String
        get() = messages.firstOrNull { it.fromUser && it.text.isNotBlank() }?.text?.trim()?.lineSequence()?.first()
            ?.let { if (it.length > TITLE_CHARS) it.take(TITLE_CHARS).trimEnd() + "…" else it }
            ?: "new conversation"

    companion object {
        const val TITLE_CHARS = 60
    }
}

/** A conversation as the history list shows it. */
data class ConversationSummary(val id: String, val title: String, val updated: Long, val messages: Int)

/**
 * The on-phone conversations: one JSON file per conversation in [dir]
 * (`filesDir/assistant/`), app-private, outside every backup and transfer
 * (the app's backup rules exclude all domains) and removed on uninstall.
 * Nothing here ever goes to a Domovoi server, and the server's chat history
 * never comes here. "Clear all" deletes every file.
 *
 * Writes go to a temporary file first and are moved into place, so a
 * process killed mid-write leaves the previous version, never half a file;
 * a file that cannot be read (damaged, or from a future format that dropped
 * a field type) is left out of the list instead of breaking it.
 */
class ConversationStore(val dir: File) {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true; explicitNulls = false; coerceInputValues = true }

    private fun file(id: String): File {
        require(isValidId(id)) { "not a conversation id: $id" }
        return File(dir, "$id.json")
    }

    /** Every readable conversation, newest first. */
    fun list(): List<ConversationSummary> =
        files().mapNotNull { f -> read(f)?.let { ConversationSummary(it.id, it.title, it.updated, it.messages.size) } }
            .sortedWith(compareByDescending<ConversationSummary> { it.updated }.thenByDescending { it.id })

    fun load(id: String): StoredConversation? = if (isValidId(id)) read(file(id)) else null

    fun save(conversation: StoredConversation) {
        dir.mkdirs()
        val target = file(conversation.id)
        val tmp = File(dir, "${conversation.id}.json.tmp")
        tmp.writeText(json.encodeToString(StoredConversation.serializer(), conversation))
        Files.move(tmp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
    }

    fun delete(id: String): Boolean = !isValidId(id) || file(id).let { !it.exists() || it.delete() }

    /** Deletes every conversation (and any leftover temporary file). */
    fun clearAll(): Boolean {
        val all = dir.listFiles().orEmpty().filter { it.isFile && (it.name.endsWith(".json") || it.name.endsWith(".json.tmp")) }
        return all.map { it.delete() }.all { it }
    }

    /** A new, empty conversation (not saved until it has a message). */
    fun create(now: Long): StoredConversation = StoredConversation(newId(now), now, now)

    private fun files(): List<File> =
        dir.listFiles().orEmpty().filter { it.isFile && it.name.endsWith(".json") && isValidId(it.name.removeSuffix(".json")) }

    private fun read(f: File): StoredConversation? =
        if (!f.isFile) null else runCatching { json.decodeFromString(StoredConversation.serializer(), f.readText()) }
            .getOrNull()
            // The file's name is the id; an older file with no id field still opens.
            ?.let { if (it.id == f.name.removeSuffix(".json")) it else it.copy(id = f.name.removeSuffix(".json")) }

    companion object {
        private val ID = Regex("^[a-z0-9-]{1,64}$")

        fun isValidId(id: String): Boolean = ID.matches(id)

        /** Sortable by time, unique enough for one phone: `c-<ms>-<random>`. */
        fun newId(now: Long): String = "c-$now-${Random.nextInt(0, 1 shl 30).toString(36)}"
    }
}
