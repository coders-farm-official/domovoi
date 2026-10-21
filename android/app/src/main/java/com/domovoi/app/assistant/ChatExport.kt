package com.domovoi.app.assistant

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.ByteArrayOutputStream
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * Phone chats as files the person takes away (assistant plan part 7):
 * one chat as text for the share sheet ([text]), and every phone chat as
 * Markdown plus JSON in one zip saved through Android's file picker
 * ([bundle], [zip]). Nothing here sends anything: the person's own tap on
 * the share sheet or the picker is the only way out of the phone.
 */
object ChatExport {
    /** The files of "export all phone chats", before they are zipped. */
    data class Bundle(val markdown: String, val json: String, val count: Int, val fileName: String)

    /** The JSON's top level: a format version, when it was written, and the
     *  conversations as the phone keeps them. */
    @Serializable
    data class Export(val format: Int = FORMAT, val exported: Long = 0, val conversations: List<StoredConversation> = emptyList())

    const val FORMAT = 1
    const val MARKDOWN_NAME = "domovoi-phone-chats.md"
    const val JSON_NAME = "domovoi-phone-chats.json"

    private val json = Json { prettyPrint = true; encodeDefaults = true; explicitNulls = false }
    private val stamp = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm", Locale.US)
    private val day = DateTimeFormatter.ofPattern("yyyy-MM-dd", Locale.US)

    private fun at(ms: Long, zone: ZoneId): String = stamp.format(Instant.ofEpochMilli(ms).atZone(zone))

    /** Who said a message, as the export writes it. */
    private fun who(m: StoredMessage): String = if (m.fromUser) "You" else "Domovoi"

    /** One message's words as exported: a failed or stopped reply says so. */
    private fun words(m: StoredMessage): String = buildString {
        // A photo question: the mark only, as the chat keeps it (N3).
        if (m.photo) append("[photo]")
        if (m.photo && m.text.isNotBlank()) append(" ")
        append(m.text.trim())
        m.error?.let { if (isNotEmpty()) append("\n"); append("[").append(it).append("]") }
        if (m.stopped) { if (isNotEmpty()) append(" "); append("[stopped]") }
    }

    /** [c] as plain text for the share sheet: its title, then each message. */
    fun text(c: StoredConversation, zone: ZoneId = ZoneId.systemDefault()): String = buildString {
        append(c.title).append("\n")
        append("Chat with Domovoi on this phone, ").append(at(c.created.takeIf { it > 0 } ?: c.updated, zone)).append("\n")
        c.messages.forEach { m ->
            append("\n").append(who(m)).append(": ").append(words(m)).append("\n")
        }
    }

    /** Every conversation in [all], newest first, as Markdown and JSON. */
    fun bundle(all: List<StoredConversation>, now: Long, zone: ZoneId = ZoneId.systemDefault()): Bundle {
        val sorted = all.sortedWith(compareByDescending<StoredConversation> { it.updated }.thenByDescending { it.id })
        val md = buildString {
            append("# Domovoi phone chats\n\n")
            append("Exported ").append(at(now, zone)).append(" · ").append(sorted.size)
                .append(if (sorted.size == 1) " chat" else " chats").append(" kept on this phone\n")
            sorted.forEach { c ->
                append("\n## ").append(c.title.replace("\n", " ")).append("\n\n")
                append("_").append(at(c.created.takeIf { it > 0 } ?: c.updated, zone))
                if (c.archived) append(" · archived")
                append("_\n")
                c.messages.forEach { m ->
                    append("\n**").append(who(m)).append("**")
                    if (m.at > 0) append(" · ").append(at(m.at, zone))
                    if (m.spoken) append(" · spoken")
                    append("\n\n").append(words(m)).append("\n")
                }
            }
        }
        val js = json.encodeToString(Export.serializer(), Export(FORMAT, now, sorted))
        return Bundle(md, js, sorted.size, fileName(now, zone))
    }

    /** The zip's name, by the day it is written: `domovoi-phone-chats-2026-10-21.zip`. */
    fun fileName(now: Long, zone: ZoneId = ZoneId.systemDefault()): String =
        "domovoi-phone-chats-${day.format(Instant.ofEpochMilli(now).atZone(zone))}.zip"

    /** [b] as one zip holding [MARKDOWN_NAME] and [JSON_NAME]. */
    fun zip(b: Bundle): ByteArray {
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { z ->
            z.putNextEntry(ZipEntry(MARKDOWN_NAME))
            z.write(b.markdown.toByteArray(Charsets.UTF_8))
            z.closeEntry()
            z.putNextEntry(ZipEntry(JSON_NAME))
            z.write(b.json.toByteArray(Charsets.UTF_8))
            z.closeEntry()
        }
        return out.toByteArray()
    }

    /** Reads [JSON_NAME]'s text back (what a later import would read). */
    fun read(text: String): Export = Json { ignoreUnknownKeys = true }.decodeFromString(Export.serializer(), text)
}
