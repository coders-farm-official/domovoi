package com.domovoi.app.assistant.docs

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.io.InputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import kotlin.random.Random

/** A document attached to a phone conversation, as the conversation keeps it
 *  (its text lives beside the file, in the store). */
@Serializable
data class StoredDocument(
    val id: String = "",
    val name: String = "",
    val kind: String = "",
    val pages: Int? = null,
    val sheets: Int? = null,
    val words: Int = 0,
    val chars: Int = 0,
    /** The one-line marker: "lease.pdf · 30 pages · about 9,800 words". */
    val summary: String = "",
    val notes: List<DocNote> = emptyList(),
    val scannedPages: List<Int> = emptyList(),
    val readByServer: Boolean = false,
    val added: Long = 0,
)

/**
 * Phone chats' attachments (documents plan part 10): each one copied into
 * app storage, `filesDir/chat-docs/attachments/<conversation>/<id>.<ext>`,
 * with its extracted parts cached beside it as `<id>.parts.json`, so a
 * document is read once per chat, not once per question. App-private and
 * outside every backup and transfer (the app's backup rules exclude all of
 * its data), deleted with the conversation, never sent anywhere.
 */
class ChatDocStore(val root: File) {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true; explicitNulls = false }

    private fun dir(conversation: String): File {
        require(SAFE.matches(conversation)) { "not a conversation id: $conversation" }
        return File(root, conversation)
    }

    private fun ext(name: String): String =
        name.substringAfterLast('.', "").lowercase().filter { it.isLetterOrDigit() }.take(10).ifEmpty { "bin" }

    /** The copy of document [id] (named [name]) of [conversation]. */
    fun file(conversation: String, id: String, name: String): File {
        require(SAFE.matches(id)) { "not a document id: $id" }
        return File(dir(conversation), "$id.${ext(name)}")
    }

    /**
     * Copies [input] in as a new document of [conversation]; at most
     * [DocExtract.MAX_FILE_BYTES], counted as it is copied (a larger one is
     * refused and nothing is kept). Returns its id and file.
     */
    fun import(conversation: String, name: String, input: InputStream, now: Long = System.currentTimeMillis()): Pair<String, File> {
        val id = "d-$now-${Random.nextInt(0, 1 shl 30).toString(36)}"
        val target = file(conversation, id, name)
        target.parentFile?.mkdirs()
        val tmp = File(target.parentFile, "${target.name}.part")
        var total = 0L
        try {
            input.use { inp ->
                tmp.outputStream().use { out ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        val n = inp.read(buf)
                        if (n < 0) break
                        total += n
                        if (total > DocExtract.MAX_FILE_BYTES) {
                            throw DocRefused("That file is too large for the chat: more than ${DocExtract.MAX_FILE_BYTES / (1024 * 1024)} MB.")
                        }
                        out.write(buf, 0, n)
                    }
                }
            }
            Files.move(tmp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING)
        } catch (e: Exception) {
            tmp.delete()
            throw e
        }
        return id to target
    }

    fun saveParts(conversation: String, id: String, extraction: DocExtraction) {
        require(SAFE.matches(id))
        val f = File(dir(conversation), "$id.parts.json")
        f.parentFile?.mkdirs()
        val tmp = File(f.parentFile, "${f.name}.tmp")
        tmp.writeText(json.encodeToString(DocExtraction.serializer(), extraction))
        Files.move(tmp.toPath(), f.toPath(), StandardCopyOption.REPLACE_EXISTING)
    }

    fun loadParts(conversation: String, id: String): DocExtraction? {
        if (!SAFE.matches(conversation) || !SAFE.matches(id)) return null
        val f = File(dir(conversation), "$id.parts.json")
        return if (!f.isFile) null else runCatching { json.decodeFromString(DocExtraction.serializer(), f.readText()) }.getOrNull()
    }

    /** Removes one document (a pending one the person dropped). */
    fun deleteDocument(conversation: String, id: String) {
        if (!SAFE.matches(conversation) || !SAFE.matches(id)) return
        dir(conversation).listFiles().orEmpty().filter { it.name.startsWith("$id.") }.forEach { it.delete() }
    }

    /** Removes everything [conversation] had attached. */
    fun delete(conversation: String): Boolean = !SAFE.matches(conversation) || dir(conversation).let { !it.exists() || it.deleteRecursively() }

    fun clearAll(): Boolean = !root.exists() || root.listFiles().orEmpty().map { it.deleteRecursively() }.all { it }

    /** Bytes the attachments take (Settings could show it). */
    fun bytes(): Long = root.walkTopDown().filter { it.isFile }.sumOf { it.length() }

    companion object {
        private val SAFE = Regex("^[a-z0-9-]{1,64}$")
    }
}
