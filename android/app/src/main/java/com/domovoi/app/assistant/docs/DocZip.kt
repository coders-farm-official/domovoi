package com.domovoi.app.assistant.docs

import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.util.zip.ZipException
import java.util.zip.ZipFile

/** A file refused, with the sentence the person reads (worded like the
 *  server's `domovoi/doc_extract` refusals). */
class DocRefused(message: String) : Exception(message)

/**
 * A .docx, .odt or .xlsx is a zip; this checks it before anything in it is
 * read, by the server's rules (`domovoi/doc_extract/safe.py`): at most
 * [MAX_MEMBERS] entries, at most [MAX_UNZIPPED] inflated in all, no member
 * over [RATIO_FLOOR] that inflates more than [MAX_RATIO] times its packed
 * size, and nothing encrypted. The sizes and flags come from the zip's own
 * central directory, read here; each member is then read through a bounded
 * stream, so a directory that lies about a size is caught as it inflates.
 */
class DocZip(private val file: File, private val what: String) : AutoCloseable {
    private val zip: ZipFile
    private var inflated = 0L

    init {
        check(file, what)
        zip = try {
            ZipFile(file)
        } catch (e: IOException) {
            throw DocRefused("That $what is damaged and couldn't be read.")
        }
    }

    fun has(name: String): Boolean = zip.getEntry(name) != null

    fun names(): List<String> = zip.entries().asSequence().map { it.name }.toList()

    /** Member [name], inflated through a bounded stream. */
    fun read(name: String, cap: Int = MAX_MEMBER_BYTES): ByteArray {
        val entry = zip.getEntry(name) ?: throw DocRefused("That $what is missing a part it needs.")
        try {
            zip.getInputStream(entry).use { input ->
                val out = java.io.ByteArrayOutputStream()
                val buf = ByteArray(16 * 1024)
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    out.write(buf, 0, n)
                    inflated += n
                    if (out.size() > cap) throw DocRefused("Part of that $what is too large to read.")
                    if (inflated > MAX_UNZIPPED) {
                        throw DocRefused("That $what unpacks to more than ${MAX_UNZIPPED / MB} MB, so Domovoi won't read it.")
                    }
                }
                return out.toByteArray()
            }
        } catch (e: ZipException) {
            throw DocRefused("That $what is damaged and couldn't be read.")
        } catch (e: IOException) {
            throw DocRefused("That $what is damaged and couldn't be read.")
        }
    }

    /** Member [name] as text (UTF-8, or by its BOM). */
    fun text(name: String): String = DocText.clean(decodeXml(read(name)))

    override fun close() = zip.close()

    companion object {
        const val MAX_MEMBERS = 5000
        const val MAX_UNZIPPED = 200L * 1024 * 1024
        const val MAX_MEMBER_BYTES = 64 * 1024 * 1024
        const val MAX_RATIO = 200
        const val RATIO_FLOOR = 1024L * 1024
        private const val MB = 1024 * 1024

        /** XML bytes as a string: UTF-16 by its BOM, else UTF-8. */
        fun decodeXml(bytes: ByteArray): String = when {
            bytes.size >= 2 && bytes[0] == 0xFF.toByte() && bytes[1] == 0xFE.toByte() -> String(bytes, Charsets.UTF_16LE).removePrefix("﻿")
            bytes.size >= 2 && bytes[0] == 0xFE.toByte() && bytes[1] == 0xFF.toByte() -> String(bytes, Charsets.UTF_16BE).removePrefix("﻿")
            else -> String(bytes, Charsets.UTF_8).removePrefix("﻿")
        }

        private fun u16(b: ByteArray, at: Int) = (b[at].toInt() and 0xFF) or ((b[at + 1].toInt() and 0xFF) shl 8)
        private fun u32(b: ByteArray, at: Int): Long =
            (u16(b, at).toLong()) or (u16(b, at + 2).toLong() shl 16)

        /** The central directory's checks (see the class note). */
        fun check(file: File, what: String) {
            RandomAccessFile(file, "r").use { raf ->
                val len = raf.length()
                val tailLen = minOf(len, 65557L).toInt()
                if (tailLen < 22) throw DocRefused("That $what is damaged and couldn't be read.")
                val tail = ByteArray(tailLen)
                raf.seek(len - tailLen)
                raf.readFully(tail)
                var eocd = -1
                for (i in tailLen - 22 downTo 0) {
                    if (tail[i] == 0x50.toByte() && tail[i + 1] == 0x4B.toByte() && tail[i + 2] == 5.toByte() && tail[i + 3] == 6.toByte()) {
                        eocd = i
                        break
                    }
                }
                if (eocd < 0) throw DocRefused("That $what is damaged and couldn't be read.")
                val entries = u16(tail, eocd + 10)
                val cdSize = u32(tail, eocd + 12)
                val cdOffset = u32(tail, eocd + 16)
                if (entries == 0xFFFF || cdOffset == 0xFFFFFFFFL) {
                    throw DocRefused("That $what is packed in a way Domovoi doesn't read (ZIP64).")
                }
                if (entries > MAX_MEMBERS) throw DocRefused("That $what has too many parts inside it to be read safely.")
                if (cdOffset + cdSize > len || cdSize > 64L * MB) throw DocRefused("That $what is damaged and couldn't be read.")
                val cd = ByteArray(cdSize.toInt())
                raf.seek(cdOffset)
                raf.readFully(cd)
                var p = 0
                var total = 0L
                var count = 0
                while (p + 46 <= cd.size && u32(cd, p) == 0x02014b50L) {
                    count++
                    if (count > MAX_MEMBERS) throw DocRefused("That $what has too many parts inside it to be read safely.")
                    val flags = u16(cd, p + 8)
                    val packed = u32(cd, p + 20)
                    val size = u32(cd, p + 24)
                    val nameLen = u16(cd, p + 28)
                    val extraLen = u16(cd, p + 30)
                    val commentLen = u16(cd, p + 32)
                    if (flags and 1 != 0) {
                        throw DocRefused("That $what is encrypted. Save a copy without a password, then attach that.")
                    }
                    total += size
                    if (total > MAX_UNZIPPED) {
                        throw DocRefused("That $what unpacks to more than ${MAX_UNZIPPED / MB} MB, so Domovoi won't read it.")
                    }
                    if (size > RATIO_FLOOR && size > MAX_RATIO * maxOf(packed, 1L)) {
                        throw DocRefused(
                            "That $what looks like a zip bomb (a part inflates ${String.format(java.util.Locale.US, "%,d", size / maxOf(packed, 1L))} " +
                                "times), so Domovoi won't read it.",
                        )
                    }
                    p += 46 + nameLen + extraLen + commentLen
                }
            }
        }
    }
}
