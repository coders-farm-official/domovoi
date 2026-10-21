package com.domovoi.app.assistant.drafts

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import kotlin.random.Random

/** A draft as a chat message points at it (the card): which draft and
 *  version, and what the turn did ("create", "revise", "edit", "print"). */
@Serializable
data class DraftRef(
    val id: String = "",
    val version: Int = 1,
    val kind: String = "letter",
    val title: String = "",
    val template: String = "letter-block",
    val words: Int = 0,
    val action: String = "create",
    /** What changed, in one line ("cut 120 words (380 → 260)"). */
    val change: String = "",
    /** Revised in this many parts (section by section); null for one. */
    val parts: Int? = null,
)

/** A file saved from a version: where it went and what the check said. */
@Serializable
data class SavedDraftFile(
    val format: String = "docx",
    val name: String = "",
    /** "phone" (Chat documents), "home" (the home server's From Chat) or
     *  "copy" (Save a copy to…). */
    val place: String = "phone",
    val check: String = "",
    val ok: Boolean = false,
    val at: Long = 0,
)

@Serializable
data class PhoneDraftVersion(
    val version: Int = 1,
    /** What produced it ("shorter"); null for a first draft or a hand edit. */
    val instruction: String? = null,
    /** "model", or "person" for "edited by you". */
    val author: String = "model",
    val at: Long = 0,
    val saved: List<SavedDraftFile> = emptyList(),
) {
    val source: String get() = if (author == "person") "edited" else if (instruction.isNullOrBlank()) "first" else "revision"
}

@Serializable
data class PhoneDraft(
    val id: String = "",
    val conversation: String = "",
    val kind: String = "letter",
    val template: String = "letter-block",
    val title: String = "",
    val created: Long = 0,
    val updated: Long = 0,
    val versions: List<PhoneDraftVersion> = emptyList(),
) {
    val latest: Int get() = versions.maxOfOrNull { it.version } ?: 0
}

/**
 * Phone chats' drafts (documents plan part 2): every version kept in app
 * storage, `filesDir/chat-docs/drafts/<conversation>/<draft>/v<N>.md` with a
 * small `draft.json` of metadata, outside every backup and transfer like the
 * conversations, deleted with the chat. Only a saved version becomes a file
 * someone can open ([ChatDocumentsLibrary]); nothing here leaves the phone.
 */
class PhoneDraftStore(val root: File) {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true; explicitNulls = false }

    private fun convDir(conversation: String): File {
        require(SAFE.matches(conversation)) { "not a conversation id: $conversation" }
        return File(root, conversation)
    }

    private fun dir(conversation: String, id: String): File {
        require(SAFE.matches(id)) { "not a draft id: $id" }
        return File(convDir(conversation), id)
    }

    private fun write(file: File, text: String) {
        file.parentFile?.mkdirs()
        val tmp = File(file.parentFile, "${file.name}.tmp")
        tmp.writeText(text)
        Files.move(tmp.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
    }

    fun load(conversation: String, id: String): PhoneDraft? = runCatching {
        json.decodeFromString(PhoneDraft.serializer(), File(dir(conversation, id), "draft.json").readText())
    }.getOrNull()

    fun body(conversation: String, id: String, version: Int): String? =
        runCatching { File(dir(conversation, id), "v$version.md").readText() }.getOrNull()

    /** The chat's drafts, newest first. */
    fun list(conversation: String): List<PhoneDraft> =
        convDir(conversation).listFiles().orEmpty().filter { it.isDirectory && SAFE.matches(it.name) }
            .mapNotNull { load(conversation, it.name) }
            .sortedWith(compareByDescending<PhoneDraft> { it.updated }.thenByDescending { it.id })

    /** The chat's newest draft and its latest version's text, or null. */
    fun latest(conversation: String): Pair<PhoneDraft, String>? {
        val d = list(conversation).firstOrNull() ?: return null
        val body = body(conversation, d.id, d.latest) ?: return null
        return d to body
    }

    fun create(conversation: String, kind: String, template: String, title: String, body: String, now: Long): PhoneDraft {
        val id = "dr-$now-${Random.nextInt(0, 1 shl 30).toString(36)}"
        val d = PhoneDraft(id, conversation, kind, template, title.take(200), now, now,
            listOf(PhoneDraftVersion(1, null, "model", now)))
        write(File(dir(conversation, id), "v1.md"), body)
        write(File(dir(conversation, id), "draft.json"), json.encodeToString(PhoneDraft.serializer(), d))
        return d
    }

    /** A new version of draft [id]; null when the draft is gone. */
    fun addVersion(conversation: String, id: String, body: String, instruction: String?, author: String, now: Long): PhoneDraft? {
        val d = load(conversation, id) ?: return null
        val v = d.latest + 1
        write(File(dir(conversation, id), "v$v.md"), body)
        val next = d.copy(updated = now, versions = d.versions + PhoneDraftVersion(v, instruction?.take(500), author, now))
        write(File(dir(conversation, id), "draft.json"), json.encodeToString(PhoneDraft.serializer(), next))
        return next
    }

    fun recordSave(conversation: String, id: String, version: Int, saved: SavedDraftFile): PhoneDraft? {
        val d = load(conversation, id) ?: return null
        val next = d.copy(versions = d.versions.map { if (it.version == version) it.copy(saved = it.saved + saved) else it })
        write(File(dir(conversation, id), "draft.json"), json.encodeToString(PhoneDraft.serializer(), next))
        return next
    }

    fun delete(conversation: String): Boolean = !SAFE.matches(conversation) || convDir(conversation).deleteRecursively()

    fun clearAll(): Boolean = root.deleteRecursively()

    companion object {
        private val SAFE = Regex("^[A-Za-z0-9-]{1,80}$")
    }
}

/**
 * The phone's "Chat documents" library (plan Q16): where a phone chat's
 * saved drafts land, app-private (`filesDir/Chat documents/`), shown in
 * Files under the phone's own files. "Save a copy to…" puts a copy
 * anywhere else through the system's file picker.
 */
class ChatDocumentsLibrary(val dir: File) {
    fun files(): List<File> = dir.listFiles().orEmpty().filter { it.isFile && !it.name.startsWith(".") }
        .sortedByDescending { it.lastModified() }

    fun file(name: String): File? {
        if (!NAME.matches(name) || name.startsWith(".")) return null
        return File(dir, name).takeIf { it.isFile }
    }

    /** Writes [bytes] as [name] (a new name when it is taken); the file. */
    fun save(name: String, bytes: ByteArray): File {
        dir.mkdirs()
        var target = File(dir, name)
        if (target.exists()) {
            val stem = name.substringBeforeLast('.')
            val ext = name.substringAfterLast('.', "")
            var i = 1
            while (target.exists()) {
                target = File(dir, if (ext.isEmpty()) "$stem ($i)" else "$stem ($i).$ext")
                i++
            }
        }
        val tmp = File(dir, ".${target.name}.part")
        tmp.writeBytes(bytes)
        Files.move(tmp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
        return target
    }

    fun delete(name: String): Boolean = file(name)?.delete() ?: false

    companion object {
        const val FOLDER = "Chat documents"
        private val NAME = Regex("^[^/\\\\:*?\"<>|\\u0000-\\u001f]{1,200}$")
        private val BAD = Regex("[\\\\/:*?\"<>|\\u0000-\\u001f\\u007f]+")

        /** "Cover letter for Acme v3.docx", as the server names it
         *  (chat_drafts.file_name). */
        fun fileName(title: String, version: Int, format: String): String {
            val stem = BAD.replace(title, " ").split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
                .trim(' ', '.').take(100).ifEmpty { "Draft" }
            return "$stem v$version.$format"
        }
    }
}
