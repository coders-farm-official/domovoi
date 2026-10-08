package com.domovoi.app.ui.screens.documents

import kotlinx.serialization.Serializable

/**
 * The sheet editor's grid model, shared by the server's `/api/documents/sheet`
 * (web/backend/api/documents.py) and the phone's own .xlsx/.csv code
 * (XlsxSheet.kt, CsvSheet.kt): `rows[r][c] = {v, f}` where `f` is a formula
 * string with its leading "=" and `v` the stored value as text.
 */
@Serializable
internal data class SheetCell(val v: String? = null, val f: String? = null)

/** Same bounds as the server: reads truncate to this window, writes refuse beyond it. */
internal const val SHEET_MAX_ROWS = 1000
internal const val SHEET_MAX_COLS = 60

/** A save the sheet code will not do; [message] is what the toast says. */
internal class SheetRefused(message: String) : Exception(message)

/** The format can't be edited here (the server's 415): offer the raw file instead. */
internal class SheetUnsupported(message: String = "unsupported") : Exception(message)

/** What the editor shows for a cell: the formula when there is one, else the value. */
internal fun SheetCell?.display(): String = this?.f ?: this?.v ?: ""

/** One cell address, 0-based. */
internal data class CellAt(val row: Int, val col: Int)

/**
 * Cells whose text differs between the grid as loaded and the grid as edited
 * — the only cells a phone save writes. Both grids are the editor's padded
 * string grids; a missing cell reads as "". Refuses a grid past the server's
 * column bound the way `PUT /sheet` does (413).
 */
internal fun sheetChanges(loaded: List<List<String>>, edited: List<List<String>>): Map<CellAt, String> {
    if (edited.size > SHEET_MAX_ROWS) throw SheetRefused("more than $SHEET_MAX_ROWS rows")
    val out = LinkedHashMap<CellAt, String>()
    val rows = maxOf(loaded.size, edited.size)
    for (r in 0 until rows) {
        val a = loaded.getOrNull(r).orEmpty()
        val b = edited.getOrNull(r).orEmpty()
        val cols = maxOf(a.size, b.size)
        for (c in 0 until cols) {
            val old = a.getOrNull(c) ?: ""
            val new = b.getOrNull(c) ?: ""
            if (old != new) {
                if (c >= SHEET_MAX_COLS) throw SheetRefused("more than $SHEET_MAX_COLS columns")
                out[CellAt(r, c)] = new
            }
        }
    }
    return out
}

/** How a typed cell is stored, by the server's rules (documents.py _write_sheet_grid). */
internal sealed interface CellWrite {
    data object Blank : CellWrite
    data class Formula(val text: String) : CellWrite      // without the leading "="
    data class Number(val text: String) : CellWrite       // as it goes into <v>
    data class Text(val text: String) : CellWrite
}

private val NUMBER = Regex("""[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?""")

/**
 * Classify what the user typed: "=..." is a formula, something Python's
 * float() would take is a number (an integral one written as an integer, as
 * the server's `int(num) if num.is_integer()` does), anything else is text.
 */
internal fun cellWrite(typed: String): CellWrite {
    if (typed.isEmpty()) return CellWrite.Blank
    if (typed.startsWith("=") && typed.length > 1) return CellWrite.Formula(typed.substring(1))
    val t = typed.trim()
    if (NUMBER.matches(t)) {
        val d = t.toDoubleOrNull()
        if (d != null && !d.isNaN() && !d.isInfinite()) {
            return CellWrite.Number(
                if (d == Math.floor(d) && Math.abs(d) < 1e15) {
                    java.math.BigDecimal(d).toBigInteger().toString()
                } else {
                    pyFloat(d)
                },
            )
        }
    }
    return CellWrite.Text(typed)
}

/** Python's repr(float): shortest digits, scientific below 1e-4 and from 1e16. */
internal fun pyFloat(d: Double): String {
    if (d.isNaN()) return "nan"
    if (d.isInfinite()) return if (d > 0) "inf" else "-inf"
    if (d == 0.0) return if (1.0 / d < 0) "-0.0" else "0.0"
    val bd = java.math.BigDecimal(d.toString()).stripTrailingZeros()
    val digits = bd.unscaledValue().abs().toString()
    val exp = digits.length - 1 - bd.scale()
    val sign = if (d < 0) "-" else ""
    return if (exp in -4..15) {
        val plain = bd.abs().toPlainString()
        sign + if (plain.contains('.')) plain else "$plain.0"
    } else {
        val mant = digits.substring(0, 1) + if (digits.length > 1) "." + digits.substring(1) else ""
        sign + mant + "e" + (if (exp < 0) "-" else "+") + Math.abs(exp).toString().padStart(2, '0')
    }
}

/** "A" → 0, "AB" → 27. */
internal fun colIndex(letters: String): Int {
    var n = 0
    for (ch in letters) n = n * 26 + (ch.uppercaseChar() - 'A' + 1)
    return n - 1
}

/** 0 → "A", 27 → "AB". */
internal fun colName(index: Int): String {
    var n = index + 1
    val sb = StringBuilder()
    while (n > 0) {
        val rem = (n - 1) % 26
        sb.append('A' + rem)
        n = (n - 1) / 26
    }
    return sb.reverse().toString()
}

private val REF = Regex("""\$?([A-Za-z]{1,3})\$?(\d+)""")

/** "B12" → CellAt(11, 1); null for anything else. */
internal fun parseRef(ref: String): CellAt? {
    val m = REF.matchEntire(ref.trim()) ?: return null
    return CellAt(m.groupValues[2].toInt() - 1, colIndex(m.groupValues[1]))
}

internal fun refOf(at: CellAt): String = colName(at.col) + (at.row + 1)
