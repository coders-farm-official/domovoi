package com.domovoi.app.assistant

import com.domovoi.app.data.AtomicFiles
import com.domovoi.app.assistant.docs.DocNote
import com.domovoi.app.assistant.docs.DocSource
import com.domovoi.app.assistant.docs.StoredDocument
import com.domovoi.app.assistant.lookup.LookupCard
import com.domovoi.app.assistant.lookup.SourceLine
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
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
    /** A reply read from the phone itself (PhoneQuestions: "battery",
     *  "timers"…) or a hand-off ("handoff"), not the model; null for the
     *  model's. */
    val fastPath: String? = null,
    /** A card under the reply: a hand-off waiting for its tap, or a
     *  permission asked on first use (HandOffs.kt). */
    val card: PhoneCard? = null,
    /** The read tools the model called for this reply (names only: their
     *  results are never kept). */
    val tools: List<String> = emptyList(),
    /** Documents attached with this message (ids of [StoredConversation.documents]). */
    val documents: List<String> = emptyList(),
    /** A reply about the chat's documents: "question", "summary" or "attached". */
    val docMode: String? = null,
    /** ...the passages it was given, each with its matched sentence and
     *  whether the reply cited it (only passages the app gave). */
    val sources: List<DocSource> = emptyList(),
    /** ...the notes under it ("couldn't find", "uncited", a quote that
     *  doesn't match). */
    val notes: List<DocNote> = emptyList(),
    /** A lookup's permission card (part 5, W1): the exact words, where
     *  they'd go and the person's answer. Not a reply: the model never
     *  sees it. */
    val lookupCard: LookupCard? = null,
    /** A reply from a lookup: what was fetched (the sources line under it
     *  is written from these, never by the model). */
    val lookupSources: List<SourceLine> = emptyList(),
    /** A summary written by a long-document job ([PhoneDocJob.id]): a
     *  stopped one offers "carry on" while the job is kept. */
    val jobId: String? = null,
) {
    val fromUser: Boolean get() = from == Speaker.USER
}

@Serializable
data class StoredConversation(
    val id: String = "",
    val created: Long = 0,
    val updated: Long = 0,
    val messages: List<StoredMessage> = emptyList(),
    /** The chat's name once the model or the person gave it one; null:
     *  the first words of the first message ([title]). */
    val name: String? = null,
    /** The person named it (rename): the model's title never replaces it. */
    val titleByUser: Boolean = false,
    /** The model has written [name] (the title job doesn't run again). */
    val titled: Boolean = false,
    /** Model titles that failed or weren't usable ([ChatTitles.MAX_TRIES]). */
    val titleTries: Int = 0,
    /** In the history list's "archived" view, not the main list. */
    val archived: Boolean = false,
    /** Where the model's view of this chat began on the last reply, as an
     *  index into [messages]; null when it saw it all. The conversation
     *  view draws "earlier messages are beyond what Domovoi can keep in
     *  mind" above that message. */
    val windowFrom: Int? = null,
    /** Documents attached in this conversation (kept in [ChatDocStore]). */
    val documents: List<StoredDocument> = emptyList(),
    /** "Allow in this chat" was chosen, or the chip turned it on: this
     *  chat's lookups go ahead without asking (part 5, W1). */
    val internetAllowed: Boolean = false,
    /** A lookup in this chat went online: the history list shows a globe. */
    val usedInternet: Boolean = false,
    /** A long document being summarized in parts (documents plan D2): its
     *  notes so far, so it carries on after the app was away. */
    val job: PhoneDocJob? = null,
) {
    /** The history list's title: the name it was given, or the first five
     *  words of the first thing asked ([ChatTitles.firstWords]); "About
     *  lease.pdf" for a document attached with no question yet. */
    val title: String
        get() = name?.takeIf { it.isNotBlank() }
            ?: ChatTitles.firstWords(messages.firstOrNull { it.fromUser && it.text.isNotBlank() }?.text)
            ?: documents.firstOrNull()?.let { "About ${it.name}" }
            ?: NEW_TITLE

    /** The first question and the first good answer after it, for the
     *  model's title; null until there is one. */
    fun firstExchange(): Pair<String, String>? {
        val q = messages.indexOfFirst { it.fromUser && it.text.isNotBlank() }
        if (q < 0) return null
        val a = messages.drop(q + 1).firstOrNull { !it.fromUser && it.text.isNotBlank() && it.error == null && !it.stopped }
            ?: return null
        return messages[q].text to a.text
    }

    /** The title job has something to do for this chat. */
    val needsTitle: Boolean
        get() = !titleByUser && !titled && titleTries < ChatTitles.MAX_TRIES && firstExchange() != null

    /** This chat with [other]'s name, archive mark and title state (the same
     *  chat changed meanwhile: renamed, or titled, while a reply ran). */
    fun withMetaOf(other: StoredConversation): StoredConversation =
        if (other.id != id) this
        else copy(name = other.name, titleByUser = other.titleByUser, titled = other.titled,
            titleTries = other.titleTries, archived = other.archived, internetAllowed = other.internetAllowed,
            usedInternet = usedInternet || other.usedInternet)

    companion object {
        const val TITLE_CHARS = ChatTitles.MAX_CHARS
        const val NEW_TITLE = "new chat"
    }
}

/** A conversation as the history list shows it. */
data class ConversationSummary(
    val id: String,
    val title: String,
    val updated: Long,
    val messages: Int,
    val archived: Boolean = false,
    val titleByUser: Boolean = false,
    /** A lookup in it went online (the list's globe). */
    val usedInternet: Boolean = false,
)

/** A conversation a search found: [snippet] is the words around the first
 *  match in a message (null when only the title matched). */
data class ConversationHit(val summary: ConversationSummary, val snippet: String?)

internal fun StoredConversation.summary() =
    ConversationSummary(id, title, updated, messages.size, archived, titleByUser, usedInternet)

/**
 * The conversations matching [query], newest first: those whose title or
 * any message contains it, case and spacing ignored; [conversations] as
 * the store reads them. A blank query finds nothing.
 */
fun searchConversations(conversations: List<StoredConversation>, query: String): List<ConversationHit> {
    val q = query.trim().split(SPACES).filter { it.isNotEmpty() }.joinToString(" ")
    if (q.isEmpty()) return emptyList()
    return conversations.mapNotNull { c ->
        val inMessage = c.messages.asReversed().firstNotNullOfOrNull { matchSnippet(it.text, q) }
        when {
            inMessage != null -> ConversationHit(c.summary(), inMessage)
            c.title.contains(q, ignoreCase = true) -> ConversationHit(c.summary(), null)
            else -> null
        }
    }.sortedWith(compareByDescending<ConversationHit> { it.summary.updated }.thenByDescending { it.summary.id })
}

private val SPACES = Regex("\\s+")

/** Characters shown either side of a search match. */
private const val MATCH_CONTEXT = 40

/** The words around the first place [q] appears in [text] (case ignored,
 *  spacing flattened), "…" where it was cut; null when it isn't there. */
internal fun matchSnippet(text: String, q: String): String? {
    if (q.isEmpty()) return null
    val flat = text.split(SPACES).filter { it.isNotEmpty() }.joinToString(" ")
    val at = flat.indexOf(q, ignoreCase = true)
    if (at < 0) return null
    val start = (at - MATCH_CONTEXT).coerceAtLeast(0)
    val end = (at + q.length + MATCH_CONTEXT).coerceAtMost(flat.length)
    return (if (start > 0) "…" else "") + flat.substring(start, end).trim() + (if (end < flat.length) "…" else "")
}

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
        files().mapNotNull { f -> read(f)?.summary() }
            .sortedWith(compareByDescending<ConversationSummary> { it.updated }.thenByDescending { it.id })

    /** Every readable conversation, whole (search, export). */
    fun all(): List<StoredConversation> = files().mapNotNull { read(it) }

    /** The conversations whose title or text contains [query] ([searchConversations]). */
    fun search(query: String): List<ConversationHit> = searchConversations(all(), query)

    fun load(id: String): StoredConversation? = if (isValidId(id)) read(file(id)) else null

    fun save(conversation: StoredConversation) {
        dir.mkdirs()
        val target = file(conversation.id)
        val tmp = File(dir, "${conversation.id}.json.tmp")
        tmp.writeText(json.encodeToString(StoredConversation.serializer(), conversation))
        AtomicFiles.replace(tmp, target)
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
