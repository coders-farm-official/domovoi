package com.domovoi.app.assistant.docs

import com.domovoi.app.ui.screens.documents.CsvSheet
import com.domovoi.app.ui.screens.documents.XlsxSheet
import java.io.File
import java.math.BigDecimal
import java.math.RoundingMode
import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.Charset
import java.nio.charset.CodingErrorAction
import java.text.DecimalFormat
import java.text.DecimalFormatSymbols
import java.util.Locale

/**
 * Spreadsheets (.xlsx, .csv) as parts of 40 rows with the header row
 * repeated, and the column totals worked out by the app, by the server's
 * rules (`domovoi/doc_extract/sheets.py`): "Sheet Budget, rows 2-41",
 * "Sheet Budget, column totals"; a CSV has no "Sheet …, " prefix.
 */
object DocSheets {
    const val ROWS_PER_PART = 40
    /** A sheet's bounds for Chat (review round 1): columns past this are
     *  left out, a sheet stops at [MAX_CELLS] cells, and rows are never
     *  padded to the header's width. */
    const val MAX_COLS = 256
    const val MAX_CELLS = 200_000
    private const val NUMERIC_SHARE = 0.6
    private val XML_REFUSED = Regex("(?i)<!DOCTYPE|<!ENTITY")
    private val MIDNIGHT = Regex("""^(\d{4}-\d{2}-\d{2}) 00:00:00$""")
    private val DATETIME = Regex("""^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}):\d{2}(\.\d+)?$""")

    /** The number a cell shows ("$1,200.50", "(40)", "12%"), or null. */
    fun numberOf(s: String): BigDecimal? {
        var t = s.trim().replace(" ", "").replace(" ", "")
        if (t.isEmpty()) return null
        var neg = t.startsWith("(") && t.endsWith(")")
        t = t.trim('(', ')')
        for (sym in listOf("$", "€", "£", "¥", "%")) t = t.replace(sym, "")
        t = t.replace(",", "")
        if (t.startsWith("-")) {
            neg = !neg
            t = t.substring(1)
        }
        if (t.isEmpty()) return null
        val dotless = t.replaceFirst(".", "")
        if (dotless.isEmpty() || !dotless.all { it in '0'..'9' }) return null
        val d = runCatching { BigDecimal(t) }.getOrNull() ?: return null
        return if (neg) d.negate() else d
    }

    private fun fmt(d: BigDecimal): String {
        val whole = d.stripTrailingZeros().scale() <= 0
        val sym = DecimalFormatSymbols(Locale.US)
        return if (whole) {
            DecimalFormat("#,##0", sym).format(d.setScale(0, RoundingMode.HALF_EVEN))
        } else {
            DecimalFormat("#,##0.00", sym).format(d.setScale(2, RoundingMode.HALF_EVEN))
        }
    }

    fun columnTotals(header: List<String>, rows: List<List<String>>): List<String> {
        val out = ArrayList<String>()
        val width = if (rows.isEmpty()) header.size else maxOf(header.size, rows.maxOf { it.size })
        for (c in 0 until width) {
            val filled = rows.mapNotNull { r -> r.getOrNull(c)?.takeIf { it.isNotBlank() } }
            val nums = filled.mapNotNull { numberOf(it) }
            if (nums.size < 2 || nums.size < NUMERIC_SHARE * filled.size) continue
            val name = header.getOrNull(c)?.trim()?.ifEmpty { null } ?: "column ${c + 1}"
            val total = nums.fold(BigDecimal.ZERO) { a, b -> a.add(b) }
            val avg = total.divide(BigDecimal(nums.size), 10, RoundingMode.HALF_EVEN)
            out += "$name: total ${fmt(total)}, average ${fmt(avg)}, lowest ${fmt(nums.min())}, " +
                "highest ${fmt(nums.max())} (${nums.size} numbers)"
        }
        return out
    }

    /** One sheet's rows (row 1 first) as parts (sheets.py `sheet_parts`). */
    fun sheetParts(rows: List<List<String>>, sheet: String?): List<DocPart> =
        sheetPartsNumbered(rows.mapIndexed { i, r -> (i + 1) to r }, sheet)

    /** The same for sparse rows: (row number, its cells), in order. */
    fun sheetPartsNumbered(rowsIn: List<Pair<Int, List<String>>>, sheet: String?): List<DocPart> {
        val numbered = rowsIn.toMutableList()
        while (numbered.isNotEmpty() && numbered.last().second.none { it.isNotBlank() }) numbered.removeAt(numbered.lastIndex)
        val first = numbered.indexOfFirst { (_, r) -> r.any { it.isNotBlank() } }
        if (first < 0) return emptyList()
        val (headerNo, header) = numbered[first]
        val data = numbered.drop(first + 1).filter { (_, r) -> r.any { it.isNotBlank() } }
        val prefix = if (sheet != null) "Sheet $sheet, " else ""
        if (data.isEmpty()) return listOf(DocPart("${prefix}row $headerNo".trim(), DocStructure.mdTable(listOf(header))))
        val head = DocStructure.mdTable(listOf(header))
        val parts = ArrayList<DocPart>()
        for (chunk in data.chunked(ROWS_PER_PART)) {
            DocCancel.check()
            // Each row as it is: never padded to the header's width.
            val body = chunk.joinToString("\n") { (_, r) -> DocStructure.mdRow(r, r.size) }
            val lo = chunk.first().first
            val hi = chunk.last().first
            parts += DocPart(if (hi != lo) "${prefix}rows $lo-$hi" else "${prefix}row $lo", body, head)
        }
        val totals = columnTotals(header, data.map { it.second })
        if (totals.isNotEmpty()) {
            val where = if (sheet != null) "sheet $sheet" else "this file"
            parts += DocPart(
                "${prefix}column totals",
                "Column totals for $where, worked out by Domovoi from rows ${data.first().first}-${data.last().first}:\n" +
                    totals.joinToString("\n"),
            )
        }
        return parts
    }

    /** A cell as the server writes it: dates without a midnight time,
     *  booleans in capitals. */
    private fun cellText(v: String): String = when {
        v == "True" -> "TRUE"
        v == "False" -> "FALSE"
        MIDNIGHT.matches(v) -> MIDNIGHT.find(v)!!.groupValues[1]
        DATETIME.matches(v) -> DATETIME.find(v)!!.let { "${it.groupValues[1]} ${it.groupValues[2]}" }
        else -> DocText.clean(v)
    }

    data class Workbook(val parts: List<DocPart>, val sheets: Int, val formulasWithoutValue: Int, val truncated: Boolean = false)

    /** Whether [bytes] declare a DOCTYPE or an entity, read as XML (by its
     *  BOM) and as raw bytes in the 8- and 16-bit forms, so a part with no
     *  XML name, or one in another encoding, can't carry one past us. */
    private fun declaresEntities(bytes: ByteArray): Boolean {
        if (XML_REFUSED.containsMatchIn(DocZip.decodeXml(bytes))) return true
        for (cs in listOf(Charsets.ISO_8859_1, Charsets.UTF_16LE, Charsets.UTF_16BE)) {
            if (XML_REFUSED.containsMatchIn(String(bytes, cs))) return true
        }
        return false
    }

    fun xlsx(file: File): Workbook {
        // Every part, whatever its name (a sheet can be reached through a
        // relationship as "sheet1.bin"), is read ONCE through the central
        // directory and checked; the reader then gets those same bytes, so
        // a second, local-only copy of a part is never what is parsed.
        val members = DocZip(file, "spreadsheet").use { z ->
            if (!z.has("xl/workbook.xml")) throw DocRefused("That isn't an Excel workbook Domovoi can read (no workbook part inside).")
            z.names().filter { !it.endsWith("/") }.map { name ->
                DocCancel.check()
                val bytes = z.read(name)
                if (declaresEntities(bytes)) {
                    throw DocRefused("That spreadsheet has XML entity declarations, which Domovoi doesn't read.")
                }
                name to bytes
            }
        }
        val book = try {
            XlsxSheet.readForChat(members, XlsxSheet.ChatLimits(cols = MAX_COLS, cells = MAX_CELLS, chars = DocExtract.MAX_TEXT_CHARS), DocCancel::check)
        } catch (e: DocRefused) {
            throw e
        } catch (e: Exception) {
            throw DocRefused("That spreadsheet is damaged and couldn't be read.")
        }
        val parts = book.sheets.flatMap { (name, rows) ->
            sheetPartsNumbered(rows.map { (n, r) -> n to r.map(::cellText) }, DocText.clean(name))
        }
        return Workbook(parts, book.sheets.size, book.formulasWithoutValue, book.truncated)
    }

    /** (text, encoding): a UTF-8 BOM dropped, UTF-16 by its BOM, then strict
     *  UTF-8, then cp1252. */
    fun decodeText(raw: ByteArray): Pair<String, String> {
        if (raw.size >= 3 && raw[0] == 0xEF.toByte() && raw[1] == 0xBB.toByte() && raw[2] == 0xBF.toByte()) {
            return String(raw, 3, raw.size - 3, Charsets.UTF_8) to "utf-8-sig"
        }
        if (raw.size >= 2 && ((raw[0] == 0xFF.toByte() && raw[1] == 0xFE.toByte()) || (raw[0] == 0xFE.toByte() && raw[1] == 0xFF.toByte()))) {
            return String(raw, Charsets.UTF_16).removePrefix("﻿") to "utf-16"
        }
        val utf8 = try {
            Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(raw)).toString()
        } catch (_: CharacterCodingException) {
            null
        }
        return if (utf8 != null) utf8 to "utf-8" else String(raw, Charset.forName("windows-1252")) to "cp1252"
    }

    /** The delimiter of CSV [text]: comma, semicolon or tab, whichever splits
     *  the first lines most consistently (comma on a tie). */
    fun sniffDelimiter(text: String): Char {
        val lines = text.lineSequence().filter { it.isNotBlank() }.take(20).toList()
        if (lines.isEmpty()) return ','
        var best = ','
        var bestScore = -1
        for (d in listOf(',', ';', '\t')) {
            val counts = lines.map { l -> countOutsideQuotes(l, d) }
            val mode = counts.filter { it > 0 }.groupingBy { it }.eachCount().maxByOrNull { it.value }?.key ?: 0
            val score = if (mode == 0) 0 else counts.count { it == mode } * 100 + mode
            if (score > bestScore) {
                best = d
                bestScore = score
            }
        }
        return if (bestScore <= 0) ',' else best
    }

    private fun countOutsideQuotes(line: String, d: Char): Int {
        var n = 0
        var quoted = false
        for (c in line) {
            if (c == '"') quoted = !quoted else if (c == d && !quoted) n++
        }
        return n
    }

    data class Csv(val parts: List<DocPart>, val encoding: String, val delimiter: Char, val truncated: Boolean = false)

    /** CSV rows within the bounds: at most [maxCols] fields a row (the rest
     *  of a row is skipped, never stored), at most [maxCells] fields and
     *  [maxChars] characters in all (rows past them are left out). The same
     *  quoting rules as [CsvSheet.parse]. (rows, cut short) */
    fun parseCapped(
        text: String,
        delimiter: Char,
        maxCols: Int = MAX_COLS,
        maxCells: Int = MAX_CELLS,
        maxChars: Int = DocExtract.MAX_TEXT_CHARS,
    ): Pair<List<List<String>>, Boolean> {
        val rows = ArrayList<List<String>>()
        var row = ArrayList<String>()
        val field = StringBuilder()
        var quoted = false
        var afterQuote = false
        var cells = 0
        var chars = 0
        var cut = false
        var i = 0
        fun save() {
            if (row.size < maxCols) {
                row.add(field.toString())
                cells++
                chars += field.length
            } else {
                cut = true
            }
            field.setLength(0)
        }
        fun end() {
            rows.add(row)
            row = ArrayList()
        }
        while (i < text.length) {
            if (i and 0xFFFF == 0) DocCancel.check()
            if (cells >= maxCells || chars >= maxChars) {
                cut = true
                break
            }
            val c = text[i]
            if (quoted) {
                if (c == '"') {
                    if (i + 1 < text.length && text[i + 1] == '"') {
                        if (row.size < maxCols) field.append('"')
                        i += 2
                        continue
                    }
                    quoted = false
                    afterQuote = true
                } else if (row.size < maxCols) {
                    field.append(c)
                }
                i++
                continue
            }
            when {
                c == '"' && field.isEmpty() && !afterQuote -> quoted = true
                c == delimiter -> { save(); afterQuote = false }
                c == '\r' || c == '\n' -> {
                    save()
                    end()
                    afterQuote = false
                    if (c == '\r' && i + 1 < text.length && text[i + 1] == '\n') i++
                }
                else -> if (row.size < maxCols) field.append(c)
            }
            i++
        }
        if (!cut && (field.isNotEmpty() || row.isNotEmpty())) {
            save()
            end()
        }
        return rows to cut
    }

    fun csv(file: File): Csv {
        val (text, enc) = decodeText(file.readBytes())
        val d = sniffDelimiter(text)
        val (raw, cut) = parseCapped(text, d)
        val rows = raw.map { r -> r.map { DocText.clean(it) } }
        return Csv(sheetParts(rows, null), enc, d, cut)
    }
}
