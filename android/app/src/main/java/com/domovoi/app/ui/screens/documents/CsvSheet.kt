package com.domovoi.app.ui.screens.documents

/**
 * .csv on the phone, by the rules of the server's Python `csv` module
 * (web/backend/api/documents.py: `csv.reader` / `csv.writer`, excel dialect):
 * comma-separated, `"` quotes a field, `""` inside quotes is one quote, a
 * newline inside quotes stays in the field, a UTF-8 BOM is dropped, bad bytes
 * become U+FFFD. Writes quote only fields that need it.
 *
 * One difference on save, on purpose: the server writes the editor's whole
 * padded grid (blank rows and columns included). The phone keeps each row as
 * wide as it was or as far as its last non-empty cell, and drops trailing
 * blank rows the file did not have, so opening and saving a file without
 * edits gives back the same data. Rows and columns past the editor's window
 * (which the server would drop) are carried over as they were. Line endings
 * follow the file ("\r\n" for a new one, as Python writes).
 */
internal object CsvSheet {

    fun decode(bytes: ByteArray): String {
        var text = bytes.decodeToString()
        if (text.startsWith("﻿")) text = text.substring(1)
        return text
    }

    private enum class St { StartRecord, StartField, InField, InQuoted, QuoteInQuoted }

    /** Every record, unbounded, by Python's reader state machine. A blank line is an empty record. */
    fun parse(text: String): List<List<String>> {
        val rows = mutableListOf<List<String>>()
        var row = mutableListOf<String>()
        val field = StringBuilder()
        var st = St.StartRecord
        fun saveField() { row.add(field.toString()); field.setLength(0) }
        fun endRecord() { rows.add(row); row = mutableListOf(); st = St.StartRecord }
        var i = 0
        while (i < text.length) {
            val c = text[i]
            val nl = c == '\n' || c == '\r'
            // CR LF is one line end.
            val step = if (c == '\r' && i + 1 < text.length && text[i + 1] == '\n') 2 else 1
            val wasQuoted = st == St.InQuoted
            when (st) {
                St.StartRecord -> if (nl) {
                    endRecord()
                } else {
                    st = St.StartField
                    continue
                }
                St.StartField -> when {
                    nl -> { saveField(); endRecord() }
                    c == '"' -> st = St.InQuoted
                    c == ',' -> saveField()
                    else -> { field.append(c); st = St.InField }
                }
                St.InField -> when {
                    nl -> { saveField(); endRecord() }
                    c == ',' -> { saveField(); st = St.StartField }
                    else -> field.append(c)
                }
                St.InQuoted -> if (c == '"') st = St.QuoteInQuoted else field.append(c)
                St.QuoteInQuoted -> when {
                    c == '"' -> { field.append('"'); st = St.InQuoted }
                    c == ',' -> { saveField(); st = St.StartField }
                    nl -> { saveField(); endRecord() }
                    else -> { field.append(c); st = St.InField }
                }
            }
            i += if (nl && !wasQuoted) step else 1
        }
        if (st != St.StartRecord) { saveField(); rows.add(row) }
        return rows
    }

    /** The editor's grid for [bytes], bounded to the server's window. */
    fun read(bytes: ByteArray): List<List<SheetCell>> =
        parse(decode(bytes)).take(SHEET_MAX_ROWS).map { r -> r.take(SHEET_MAX_COLS).map { SheetCell(v = it) } }

    private fun quote(field: String): String =
        if (field.any { it == ',' || it == '"' || it == '\r' || it == '\n' }) {
            "\"" + field.replace("\"", "\"\"") + "\""
        } else {
            field
        }

    /** Python's csv.writer line for [row]: a lone empty field is written as "". */
    fun line(row: List<String>): String =
        if (row.size == 1 && row[0].isEmpty()) "\"\"" else row.joinToString(",") { quote(it) }

    /**
     * The file to write for the edited [grid] (formula text kept verbatim, as
     * the server does), shaped by the [original] file's rows as described above.
     */
    fun write(grid: List<List<String>>, original: List<List<String>>, newline: String): String {
        if (grid.any { r -> r.drop(SHEET_MAX_COLS).any { it.isNotEmpty() } }) {
            throw SheetRefused("more than $SHEET_MAX_COLS columns")
        }
        val lastFilled = grid.take(SHEET_MAX_ROWS).indexOfLast { r -> r.any { it.isNotEmpty() } }
        val rowCount = maxOf(original.size, lastFilled + 1)
        val sb = StringBuilder()
        for (r in 0 until rowCount) {
            val orig = original.getOrNull(r)
            // Past the editor's window the file is carried over as it was.
            if (r >= SHEET_MAX_ROWS) {
                sb.append(line(orig.orEmpty())).append(newline)
                continue
            }
            val cells = grid.getOrNull(r).orEmpty()
            val lastCell = cells.take(SHEET_MAX_COLS).indexOfLast { it.isNotEmpty() }
            val width = maxOf(orig?.size ?: 0, lastCell + 1)
            val row = (0 until width).map { c ->
                if (c < SHEET_MAX_COLS) cells.getOrNull(c) ?: "" else orig?.getOrNull(c) ?: ""
            }
            sb.append(line(row)).append(newline)
        }
        return sb.toString()
    }

    /** "\n" when the file uses bare newlines, else "\r\n" (Python's default). */
    fun newlineOf(text: String): String = if (!text.contains("\r\n") && text.contains('\n')) "\n" else "\r\n"
}
