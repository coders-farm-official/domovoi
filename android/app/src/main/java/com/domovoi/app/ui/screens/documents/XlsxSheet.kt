package com.domovoi.app.ui.screens.documents

import com.domovoi.app.ui.screens.documents.MiniXml.Kind
import com.domovoi.app.ui.screens.documents.MiniXml.Tag
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.time.LocalDateTime
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream

/**
 * .xlsx on the phone, without Apache POI: a reader that turns the FIRST
 * worksheet into the editor's grid, and a writer that PATCHES only the cells
 * the user changed inside that worksheet's XML.
 *
 * Reading matches what the server's openpyxl read gives the editor
 * (web/backend/api/documents.py `_read_sheet_grid`): first sheet, a
 * [SHEET_MAX_ROWS] x [SHEET_MAX_COLS] window, formulas as "=..." (shared
 * formulas translated to each cell), numbers as Python prints them, booleans
 * as True/False, date-formatted numbers as Python datetimes print.
 *
 * Writing is where the phone does better than the server, deliberately. The
 * server's save builds a NEW workbook from the grid, so a save there drops
 * every style, column width, merge, chart and every sheet but the first. Here
 * the zip is copied entry for entry and only the cells whose text changed are
 * rewritten in the sheet XML; everything else (styles.xml, other sheets,
 * shared strings, the untouched cells' bytes) survives. A changed cell keeps
 * its style index. Text is written as an inline string so sharedStrings.xml
 * never has to change; numbers and formulas by the same rules as the server.
 * When formulas change, calcChain.xml is dropped (Excel rebuilds it, and a
 * stale one is what makes Excel offer to "repair" a file) and the workbook is
 * marked to recalculate on open, since a new formula has no cached value.
 */
internal object XlsxSheet {

    /** Refuse archives that inflate past this: a phone editor for household
     *  sheets. Counted while inflating, entry by entry, so a small archive
     *  that unpacks to gigabytes is dropped at this many bytes, not held. */
    internal const val MAX_UNZIPPED = 200L * 1024 * 1024

    // ── zip ─────────────────────────────────────────────────────────────

    private class Entry(val name: String, val bytes: ByteArray, val time: Long)

    private fun unzip(bytes: ByteArray, maxInflated: Long): List<Entry> {
        val out = mutableListOf<Entry>()
        var total = 0L
        ZipInputStream(ByteArrayInputStream(bytes)).use { zin ->
            while (true) {
                val e = zin.nextEntry ?: break
                val buf = ByteArrayOutputStream()
                val chunk = ByteArray(16 * 1024)
                while (true) {
                    val n = zin.read(chunk)
                    if (n < 0) break
                    total += n
                    if (total > maxInflated) throw IOException("spreadsheet is too large to open here")
                    buf.write(chunk, 0, n)
                }
                out += Entry(e.name, buf.toByteArray(), e.time)
            }
        }
        if (out.isEmpty()) throw IOException("not an xlsx file (empty or not a zip)")
        return out
    }

    private fun zip(entries: List<Entry>): ByteArray {
        val bos = ByteArrayOutputStream()
        ZipOutputStream(bos).use { zout ->
            for (e in entries) {
                val ze = ZipEntry(e.name)
                if (e.time > 0) ze.time = e.time
                zout.putNextEntry(ze)
                zout.write(e.bytes)
                zout.closeEntry()
            }
        }
        return bos.toByteArray()
    }

    private fun List<Entry>.text(name: String): String? =
        firstOrNull { it.name == name }?.bytes?.let { String(it, Charsets.UTF_8) }

    // ── package parts ───────────────────────────────────────────────────

    private class Rel(val id: String, val type: String, val target: String)

    private fun rels(xml: String?): List<Rel> =
        if (xml == null) {
            emptyList()
        } else {
            MiniXml.tags(xml).filter { it.name == "Relationship" && it.kind != Kind.Close }.map {
                Rel(it.attr("Id") ?: "", it.attr("Type") ?: "", it.attr("Target") ?: "")
            }.toList()
        }

    /** Resolve a relationship target against the directory of the part that owns it. */
    private fun resolve(baseDir: String, target: String): String {
        if (target.startsWith("/")) return target.removePrefix("/")
        val parts = (if (baseDir.isEmpty()) emptyList() else baseDir.split('/')).toMutableList()
        for (seg in target.split('/')) {
            when (seg) {
                "", "." -> {}
                ".." -> if (parts.isNotEmpty()) parts.removeAt(parts.lastIndex)
                else -> parts += seg
            }
        }
        return parts.joinToString("/")
    }

    private fun relsPathOf(part: String): String {
        val dir = part.substringBeforeLast('/', "")
        val file = part.substringAfterLast('/')
        return (if (dir.isEmpty()) "" else "$dir/") + "_rels/$file.rels"
    }

    private class Book(
        val workbookPath: String,
        val sheetPath: String,
        val stylesPath: String?,
        val sharedStringsPath: String?,
        val date1904: Boolean,
    )

    private fun book(entries: List<Entry>): Book {
        val rootRels = rels(entries.text("_rels/.rels"))
        val wbPath = rootRels.firstOrNull { it.type.endsWith("/officeDocument") }
            ?.let { resolve("", it.target) } ?: "xl/workbook.xml"
        val wb = entries.text(wbPath) ?: throw IOException("not an xlsx file (no workbook)")
        val wbDir = wbPath.substringBeforeLast('/', "")
        val wbRels = rels(entries.text(relsPathOf(wbPath)))
        val firstSheetRid = MiniXml.tags(wb).firstOrNull { it.name == "sheet" && it.kind != Kind.Close }
            ?.attr("id") ?: throw IOException("workbook has no sheets")
        val sheetRel = wbRels.firstOrNull { it.id == firstSheetRid }
            ?: throw IOException("workbook's first sheet is missing")
        val pr = MiniXml.tags(wb).firstOrNull { it.name == "workbookPr" && it.kind != Kind.Close }
        val d1904 = pr?.attr("date1904")?.let { it == "1" || it.equals("true", true) } ?: false
        return Book(
            workbookPath = wbPath,
            sheetPath = resolve(wbDir, sheetRel.target),
            stylesPath = wbRels.firstOrNull { it.type.endsWith("/styles") }?.let { resolve(wbDir, it.target) },
            sharedStringsPath = wbRels.firstOrNull { it.type.endsWith("/sharedStrings") }?.let { resolve(wbDir, it.target) },
            date1904 = d1904,
        )
    }

    /** Plain text of an `<si>` or `<is>` element: its `<t>` runs, phonetic runs left out. */
    private fun richText(xml: String, from: Int, to: Int): String {
        val sb = StringBuilder()
        var skipDepth = 0
        var pos = from
        while (pos < to) {
            val t = MiniXml.tags(xml, pos, to).firstOrNull() ?: break
            when {
                t.name == "rPh" && t.kind == Kind.Open -> skipDepth++
                t.name == "rPh" && t.kind == Kind.Close -> skipDepth--
                t.name == "t" && t.kind == Kind.Open -> {
                    val end = MiniXml.elementEnd(xml, t)
                    if (skipDepth == 0) {
                        val close = xml.lastIndexOf('<', end - 1)
                        sb.append(MiniXml.textBetween(xml, t.end, close))
                    }
                    pos = end
                    continue
                }
            }
            pos = t.end
        }
        return sb.toString()
    }

    private fun sharedStrings(xml: String?): List<String> {
        if (xml == null) return emptyList()
        val out = mutableListOf<String>()
        var pos = 0
        while (true) {
            val t = MiniXml.tags(xml, pos).firstOrNull { it.name == "si" && it.kind != Kind.Close } ?: break
            if (t.kind == Kind.Empty) {
                out += ""
                pos = t.end
            } else {
                val end = MiniXml.elementEnd(xml, t)
                out += richText(xml, t.end, end)
                pos = end
            }
        }
        return out
    }

    // ── dates (openpyxl's is_date_format / from_excel) ───────────────────

    private val BUILTIN_DATE_IDS = setOf(14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47)
    private val TIMEDELTA_BUILTIN = setOf(46)
    private val STRIP = Regex("""\[(?!hh?\]|mm?\]|ss?\])[^\]]*\]|"[^"]*"|\\.|_.""")
    private val DATE_CHARS = Regex("""[dmhysDMHYS]""")
    private val TIMEDELTA = Regex("""\[(hh?|mm?|ss?)\]""", RegexOption.IGNORE_CASE)

    internal fun isDateFormat(code: String): Boolean {
        val first = code.split(';').first()
        return DATE_CHARS.containsMatchIn(STRIP.replace(first, ""))
    }

    internal fun isTimedeltaFormat(code: String): Boolean =
        TIMEDELTA.containsMatchIn(code.split(';').first())

    private class Styles(val dates: Set<Int>, val timedeltas: Set<Int>)

    private fun styles(xml: String?): Styles {
        if (xml == null) return Styles(emptySet(), emptySet())
        val custom = HashMap<Int, String>()
        val xfFormats = mutableListOf<Int>()
        var inXfs = false
        for (t in MiniXml.tags(xml)) {
            when {
                t.name == "numFmt" && t.kind != Kind.Close ->
                    t.attr("numFmtId")?.toIntOrNull()?.let { custom[it] = t.attr("formatCode") ?: "" }
                t.name == "cellXfs" -> inXfs = t.kind == Kind.Open
                inXfs && t.name == "xf" && t.kind != Kind.Close ->
                    xfFormats += t.attr("numFmtId")?.toIntOrNull() ?: 0
            }
        }
        val dates = HashSet<Int>()
        val tds = HashSet<Int>()
        xfFormats.forEachIndexed { i, id ->
            val code = custom[id]
            if (code != null) {
                if (isDateFormat(code)) dates += i
                if (isTimedeltaFormat(code)) tds += i
            } else if (id in BUILTIN_DATE_IDS) {
                dates += i
                if (id in TIMEDELTA_BUILTIN) tds += i
            }
        }
        return Styles(dates, tds)
    }

    private fun two(n: Long) = n.toString().padStart(2, '0')

    private fun micros(us: Long) = if (us == 0L) "" else "." + us.toString().padStart(6, '0')

    /** Python's str() of what openpyxl's from_excel returns for [value]. */
    internal fun excelDateText(value: Double, date1904: Boolean, timedelta: Boolean): String {
        if (timedelta) {
            var totalMs = Math.round(value * 86_400_000.0)
            val neg = totalMs < 0
            if (neg) totalMs = -totalMs
            val days = totalMs / 86_400_000
            val rem = totalMs % 86_400_000
            val h = rem / 3_600_000
            val m = rem % 3_600_000 / 60_000
            val s = rem % 60_000 / 1000
            val ms = rem % 1000
            val clock = "$h:${two(m)}:${two(s)}" + micros(ms * 1000)
            val d = if (neg) -days else days
            return if (days == 0L) clock else "$d day${if (Math.abs(d) == 1L) "" else "s"}, $clock"
        }
        val day = Math.floor(value)
        val frac = value - day
        var ms = Math.round(frac * 86_400_000.0)
        var dayL = day.toLong()
        if (ms >= 86_400_000) { dayL += 1; ms -= 86_400_000 }
        val clock = "${two(ms / 3_600_000)}:${two(ms % 3_600_000 / 60_000)}:${two(ms % 60_000 / 1000)}" +
            micros(ms % 1000 * 1000)
        if (value >= 0 && value < 1 && dayL == 0L) return clock
        val epoch = if (date1904) LocalDateTime.of(1904, 1, 1, 0, 0) else LocalDateTime.of(1899, 12, 30, 0, 0)
        if (!date1904 && value > 0 && value < 60) dayL += 1
        val dt = epoch.plusDays(dayL)
        return "%04d-%02d-%02d %s".format(dt.year, dt.monthValue, dt.dayOfMonth, clock)
    }

    // ── numbers (openpyxl's _cast_number, then Python's str) ────────────

    private fun numberText(raw: String): String {
        val s = raw.trim()
        return if (s.contains('.') || s.contains('E') || s.contains('e')) {
            s.toDoubleOrNull()?.let { pyFloat(it) } ?: raw
        } else {
            runCatching { java.math.BigInteger(s).toString() }.getOrDefault(raw)
        }
    }

    // ── shared formulas (openpyxl's Translator, simplified) ─────────────

    private val TOKEN = Regex(
        """"(?:[^"]|"")*"|'(?:[^']|'')*'!?|(\$?)([A-Za-z]{1,3})(\$?)(\d+)(?![\w(])|(\$?)([A-Za-z]{1,3}):(\$?)([A-Za-z]{1,3})(?![\w(])|(\$?)(\d+):(\$?)(\d+)(?![\w(])""",
    )

    /** Shift the relative references of [formula] (with its "=") by the given offsets. */
    internal fun translate(formula: String, dRow: Int, dCol: Int): String {
        if (dRow == 0 && dCol == 0) return formula
        return TOKEN.replace(formula) { m ->
            val g = m.groupValues
            val start = m.range.first
            // A name directly preceded by a letter, digit, '_' or '.' is part of
            // a longer identifier (a function or a defined name), not a ref.
            val prev = if (start > 0) formula[start - 1] else ' '
            if (prev.isLetterOrDigit() || prev == '_' || prev == '.') return@replace m.value
            when {
                g[2].isNotEmpty() -> {
                    val col = if (g[1] == "$") g[2] else colName((colIndex(g[2]) + dCol).coerceAtLeast(0))
                    val row = if (g[3] == "$") g[4] else ((g[4].toInt() + dRow).coerceAtLeast(1)).toString()
                    g[1] + col + g[3] + row
                }
                g[6].isNotEmpty() -> {
                    fun c(d: String, l: String) = if (d == "$") l else colName((colIndex(l) + dCol).coerceAtLeast(0))
                    g[5] + c(g[5], g[6]) + ":" + g[7] + c(g[7], g[8])
                }
                g[10].isNotEmpty() -> {
                    fun r(d: String, n: String) = if (d == "$") n else (n.toInt() + dRow).coerceAtLeast(1).toString()
                    g[9] + r(g[9], g[10]) + ":" + g[11] + r(g[11], g[12])
                }
                else -> m.value // a quoted string or sheet name
            }
        }
    }

    // ── the worksheet scan ──────────────────────────────────────────────

    private class CellSpan(
        val at: CellAt,
        val open: Tag,
        val end: Int,
    ) {
        val start: Int get() = open.start
    }

    private class RowSpan(
        val row: Int,
        val open: Tag,
        /** Offset just past `</row>` (or the empty tag). */
        val end: Int,
        val cells: List<CellSpan>,
    )

    private class SheetScan(
        val xml: String,
        /** `<sheetData>` open tag. */
        val dataOpen: Tag,
        /** Offset of `</sheetData>`, or dataOpen.end for an empty element. */
        val dataInnerEnd: Int,
        val rows: List<RowSpan>,
        val prefix: String,
    )

    private fun scan(xml: String): SheetScan {
        val dataOpen = MiniXml.tags(xml).firstOrNull { it.name == "sheetData" && it.kind != Kind.Close }
            ?: throw IOException("worksheet has no sheetData")
        val prefix = if (dataOpen.qname.contains(':')) dataOpen.qname.substringBefore(':') + ":" else ""
        val dataEnd = MiniXml.elementEnd(xml, dataOpen)
        val innerEnd = if (dataOpen.kind == Kind.Empty) dataOpen.end else xml.lastIndexOf('<', dataEnd - 1)
        val rows = mutableListOf<RowSpan>()
        var pos = dataOpen.end
        var lastRow = -1
        while (pos < innerEnd) {
            val t = MiniXml.tags(xml, pos, innerEnd).firstOrNull() ?: break
            if (t.name != "row" || t.kind == Kind.Close) { pos = t.end; continue }
            val r = t.attr("r")?.toIntOrNull()?.minus(1) ?: (lastRow + 1)
            lastRow = r
            val rowEnd = MiniXml.elementEnd(xml, t)
            val cells = mutableListOf<CellSpan>()
            if (t.kind == Kind.Open) {
                val rowInnerEnd = xml.lastIndexOf('<', rowEnd - 1)
                var cp = t.end
                var lastCol = -1
                while (cp < rowInnerEnd) {
                    val c = MiniXml.tags(xml, cp, rowInnerEnd).firstOrNull() ?: break
                    if (c.name != "c" || c.kind == Kind.Close) { cp = c.end; continue }
                    val col = c.attr("r")?.let { parseRef(it)?.col } ?: (lastCol + 1)
                    lastCol = col
                    val cEnd = MiniXml.elementEnd(xml, c)
                    cells += CellSpan(CellAt(r, col), c, cEnd)
                    cp = cEnd
                }
            }
            rows += RowSpan(r, t, rowEnd, cells)
            pos = rowEnd
        }
        return SheetScan(xml, dataOpen, innerEnd, rows, prefix)
    }

    /** The first child element named [name] inside a cell, if any. */
    private fun child(xml: String, cell: CellSpan, name: String): Tag? =
        if (cell.open.kind == Kind.Empty) {
            null
        } else {
            MiniXml.tags(xml, cell.open.end, cell.end).firstOrNull { it.name == name && it.kind != Kind.Close }
        }

    private fun innerText(xml: String, t: Tag): String? {
        if (t.kind == Kind.Empty) return null
        val end = MiniXml.elementEnd(xml, t)
        return MiniXml.textBetween(xml, t.end, xml.lastIndexOf('<', end - 1))
    }

    // ── read ─────────────────────────────────────────────────────────────

    /**
     * The first worksheet as the editor's grid, bounded to the server's
     * window, trailing empty rows and cells trimmed.
     */
    fun read(
        bytes: ByteArray,
        maxRows: Int = SHEET_MAX_ROWS,
        maxCols: Int = SHEET_MAX_COLS,
        maxInflated: Long = MAX_UNZIPPED,
    ): List<List<SheetCell>> {
        val entries = unzip(bytes, maxInflated)
        val book = book(entries)
        val sheet = entries.text(book.sheetPath) ?: throw IOException("first worksheet is missing")
        val strings = sharedStrings(book.sharedStringsPath?.let { entries.text(it) })
        val st = styles(book.stylesPath?.let { entries.text(it) })
        val sc = scan(sheet)

        // Shared-formula masters, by si: their formula and where they sit.
        val masters = HashMap<String, Pair<String, CellAt>>()
        val grid = ArrayList<MutableList<SheetCell>>()
        for (row in sc.rows) {
            if (row.row >= maxRows) break
            for (cell in row.cells) {
                val at = cell.at
                val value = cellValue(sheet, cell, strings, st, book.date1904, masters) ?: continue
                if (at.col >= maxCols) continue
                while (grid.size <= at.row) grid += mutableListOf<SheetCell>()
                val r = grid[at.row]
                while (r.size <= at.col) r += SheetCell()
                r[at.col] = value
            }
        }
        // Trim: empty trailing cells per row, then empty trailing rows.
        for (r in grid) while (r.isNotEmpty() && r.last().v == null && r.last().f == null) r.removeAt(r.lastIndex)
        while (grid.isNotEmpty() && grid.last().isEmpty()) grid.removeAt(grid.lastIndex)
        return grid
    }

    private fun cellValue(
        xml: String,
        cell: CellSpan,
        strings: List<String>,
        st: Styles,
        date1904: Boolean,
        masters: HashMap<String, Pair<String, CellAt>>,
    ): SheetCell? {
        val type = cell.open.attr("t") ?: "n"
        val style = cell.open.attr("s")?.toIntOrNull() ?: 0
        val f = child(xml, cell, "f")
        if (f != null) {
            var formula = "=" + (innerText(xml, f) ?: "")
            if (f.attr("t") == "shared") {
                val si = f.attr("si") ?: ""
                val master = masters[si]
                if (master != null) {
                    formula = translate(master.first, cell.at.row - master.second.row, cell.at.col - master.second.col)
                } else if (formula != "=") {
                    masters[si] = formula to cell.at
                }
            }
            return SheetCell(f = formula)
        }
        if (type == "inlineStr") {
            val isTag = child(xml, cell, "is") ?: return null
            if (isTag.kind == Kind.Empty) return SheetCell(v = "")
            return SheetCell(v = richText(xml, isTag.end, MiniXml.elementEnd(xml, isTag)))
        }
        val v = child(xml, cell, "v")?.let { innerText(xml, it) }?.takeIf { it.isNotEmpty() } ?: return null
        val text = when (type) {
            "s" -> strings.getOrNull(v.trim().toIntOrNull() ?: -1) ?: ""
            "b" -> if ((v.trim().toIntOrNull() ?: 0) != 0) "True" else "False"
            "str", "e" -> v
            "d" -> v.replace('T', ' ')
            else -> {
                if (style in st.dates) {
                    v.trim().toDoubleOrNull()?.let { excelDateText(it, date1904, style in st.timedeltas) } ?: numberText(v)
                } else {
                    numberText(v)
                }
            }
        }
        return SheetCell(v = text)
    }

    // ── patch ────────────────────────────────────────────────────────────

    /**
     * Rewrite [changes] (cell → what the user typed) into the first worksheet
     * of [bytes] and return the new file. Every other zip entry, and every
     * untouched cell's XML, is carried over byte for byte.
     */
    fun patch(bytes: ByteArray, changes: Map<CellAt, String>, maxInflated: Long = MAX_UNZIPPED): ByteArray {
        if (changes.isEmpty()) return bytes
        changes.keys.firstOrNull { it.col >= SHEET_MAX_COLS }?.let {
            throw SheetRefused("more than $SHEET_MAX_COLS columns")
        }
        val entries = unzip(bytes, maxInflated).toMutableList()
        val book = book(entries)
        val sheetXml = entries.text(book.sheetPath) ?: throw IOException("first worksheet is missing")
        val sc = scan(sheetXml)
        val p = sc.prefix

        val existing = HashMap<CellAt, CellSpan>()
        sc.rows.forEach { r -> r.cells.forEach { existing[it.at] = it } }

        // Replacement XML per cell. Starts with the user's edits...
        val replace = HashMap<CellAt, String?>()   // null value = drop the cell
        var formulasTouched = false
        var newFormulas = false
        for ((at, typed) in changes) {
            val old = existing[at]
            if (old != null && child(sheetXml, old, "f") != null) formulasTouched = true
            val w = cellWrite(typed)
            if (w is CellWrite.Formula) { formulasTouched = true; newFormulas = true }
            replace[at] = renderCell(p, at, old?.open, w)
        }
        // ...then any shared-formula group an edit cut into is expanded into
        // plain formulas, so no dependent is left pointing at a master that
        // is gone.
        val groups = HashMap<String, MutableList<CellSpan>>()
        for (cell in existing.values) {
            val f = child(sheetXml, cell, "f") ?: continue
            if (f.attr("t") == "shared") groups.getOrPut(f.attr("si") ?: "") { mutableListOf() } += cell
        }
        for ((_, members) in groups) {
            if (members.none { it.at in changes }) continue
            val master = members.firstOrNull { m ->
                child(sheetXml, m, "f")?.let { (innerText(sheetXml, it) ?: "").isNotEmpty() } == true
            } ?: continue
            val masterF = "=" + (innerText(sheetXml, child(sheetXml, master, "f")!!) ?: "")
            for (m in members) {
                if (m.at in changes) continue
                val text = translate(masterF, m.at.row - master.at.row, m.at.col - master.at.col).substring(1)
                replace[m.at] = replaceFormula(sheetXml, m, p, text)
            }
        }

        val newSheet = rebuild(sc, replace)
        val withDim = updateDimension(newSheet, replace.filterValues { it != null }.keys)
        entries.replaceText(book.sheetPath, withDim)

        if (formulasTouched) dropCalcChain(entries, book)
        if (newFormulas) markRecalc(entries, book.workbookPath)
        return zip(entries)
    }

    private fun MutableList<Entry>.replaceText(name: String, text: String) {
        val i = indexOfFirst { it.name == name }
        val bytes = text.toByteArray(Charsets.UTF_8)
        if (i >= 0) this[i] = Entry(name, bytes, this[i].time) else add(Entry(name, bytes, 0))
    }

    /** The XML of a cell rewritten to [w], keeping the old cell's style. Null drops it. */
    private fun renderCell(p: String, at: CellAt, old: Tag?, w: CellWrite): String? {
        val style = old?.attr("s")?.takeIf { it.isNotEmpty() && it != "0" }
        val head = StringBuilder("<${p}c r=\"${refOf(at)}\"")
        if (style != null) head.append(" s=\"").append(MiniXml.escape(style)).append('"')
        return when (w) {
            CellWrite.Blank -> if (style == null) null else "$head/>"
            is CellWrite.Formula -> "$head><${p}f>${MiniXml.escape(w.text)}</${p}f></${p}c>"
            is CellWrite.Number -> "$head><${p}v>${w.text}</${p}v></${p}c>"
            is CellWrite.Text -> {
                val space = if (w.text != w.text.trim() || w.text.contains('\n')) " xml:space=\"preserve\"" else ""
                "$head t=\"inlineStr\"><${p}is><${p}t$space>${MiniXml.escape(w.text)}</${p}t></${p}is></${p}c>"
            }
        }
    }

    /** A shared-formula member's XML with its `<f>` made a plain formula. */
    private fun replaceFormula(xml: String, cell: CellSpan, p: String, text: String): String {
        val f = child(xml, cell, "f")!!
        val fEnd = MiniXml.elementEnd(xml, f)
        return xml.substring(cell.start, f.start) + "<${p}f>${MiniXml.escape(text)}</${p}f>" +
            xml.substring(fEnd, cell.end)
    }

    private val SPANS = Regex("""\s+spans\s*=\s*("[^"]*"|'[^']*')""")

    /** A row/cell open tag with an `r` attribute (added when it was implied). */
    private fun withR(xml: String, tag: Tag, r: String): String {
        val raw = xml.substring(tag.start, tag.end)
        if (tag.attrs.containsKey("r")) return raw
        val at = 1 + tag.qname.length
        return raw.substring(0, at) + " r=\"$r\"" + raw.substring(at)
    }

    private fun rebuild(sc: SheetScan, replace: Map<CellAt, String?>): String {
        val xml = sc.xml
        val p = sc.prefix
        val byRow = replace.keys.groupBy { it.row }
        val out = StringBuilder(xml.length + 256)
        // Everything before the sheetData content.
        if (sc.dataOpen.kind == Kind.Empty) {
            val raw = xml.substring(sc.dataOpen.start, sc.dataOpen.end)
            out.append(xml, 0, sc.dataOpen.start).append(raw.removeSuffix("/>").trimEnd()).append('>')
        } else {
            out.append(xml, 0, sc.dataOpen.end)
        }

        val existingRows = sc.rows.associateBy { it.row }
        val rowNumbers = (sc.rows.map { it.row } + byRow.keys).toSortedSet()
        var copyFrom = sc.dataOpen.end
        for (r in rowNumbers) {
            val row = existingRows[r]
            val edits = byRow[r].orEmpty()
            if (row != null) {
                // Whatever sat between the previous row and this one (whitespace).
                if (row.open.start > copyFrom) out.append(xml, copyFrom, row.open.start)
                copyFrom = maxOf(copyFrom, row.end)
                if (edits.isEmpty()) {
                    out.append(withR(xml, row.open, (r + 1).toString()))
                    out.append(xml, row.open.end, row.end)
                    continue
                }
                var openTag = withR(xml, row.open, (r + 1).toString()).replace(SPANS, "")
                if (row.open.kind == Kind.Empty) openTag = openTag.removeSuffix("/>").trimEnd() + ">"
                out.append(openTag)
                val cells = row.cells.associateBy { it.at.col }
                val cols = (row.cells.map { it.at.col } + edits.map { it.col }).toSortedSet()
                for (c in cols) {
                    val at = CellAt(r, c)
                    if (replace.containsKey(at)) {
                        replace[at]?.let { out.append(it) }
                    } else {
                        val cell = cells.getValue(c)
                        out.append(withR(xml, cell.open, refOf(at)))
                        out.append(xml, cell.open.end, cell.end)
                    }
                }
                out.append("</${p}row>")
            } else {
                val cells = edits.sortedBy { it.col }.mapNotNull { replace[it] }
                if (cells.isEmpty()) continue
                out.append("<${p}row r=\"${r + 1}\">")
                cells.forEach { out.append(it) }
                out.append("</${p}row>")
            }
        }
        out.append(xml, copyFrom, sc.dataInnerEnd)
        if (sc.dataOpen.kind == Kind.Empty) {
            out.append("</${sc.dataOpen.qname}>").append(xml, sc.dataOpen.end, xml.length)
        } else {
            out.append(xml, sc.dataInnerEnd, xml.length)
        }
        return out.toString()
    }

    /** Grow `<dimension ref>` to cover cells written outside it. */
    private fun updateDimension(xml: String, written: Set<CellAt>): String {
        if (written.isEmpty()) return xml
        val dim = MiniXml.tags(xml).firstOrNull { it.name == "dimension" && it.kind != Kind.Close } ?: return xml
        val ref = dim.attr("ref") ?: return xml
        val a = parseRef(ref.substringBefore(':')) ?: return xml
        val b = parseRef(ref.substringAfter(':', ref.substringBefore(':'))) ?: a
        val maxRow = maxOf(b.row, written.maxOf { it.row })
        val maxCol = maxOf(b.col, written.maxOf { it.col })
        val minRow = minOf(a.row, written.minOf { it.row })
        val minCol = minOf(a.col, written.minOf { it.col })
        val next = refOf(CellAt(minRow, minCol)) + ":" + refOf(CellAt(maxRow, maxCol))
        if (next == ref) return xml
        val raw = xml.substring(dim.start, dim.end)
        val replaced = raw.replace(Regex("""ref\s*=\s*("[^"]*"|'[^']*')"""), "ref=\"$next\"")
        return xml.substring(0, dim.start) + replaced + xml.substring(dim.end)
    }

    /** Remove calcChain.xml with its relationship and content-type override. */
    private fun dropCalcChain(entries: MutableList<Entry>, book: Book) {
        val wbDir = book.workbookPath.substringBeforeLast('/', "")
        val relsPath = relsPathOf(book.workbookPath)
        val relsXml = entries.text(relsPath) ?: return
        val rel = MiniXml.tags(relsXml).firstOrNull {
            it.name == "Relationship" && it.kind != Kind.Close && (it.attr("Type") ?: "").endsWith("/calcChain")
        } ?: return
        val part = resolve(wbDir, rel.attr("Target") ?: "")
        entries.replaceText(relsPath, relsXml.substring(0, rel.start) + relsXml.substring(MiniXml.elementEnd(relsXml, rel)))
        entries.removeAll { it.name == part }
        val ct = entries.text("[Content_Types].xml") ?: return
        val ov = MiniXml.tags(ct).firstOrNull {
            it.name == "Override" && it.kind != Kind.Close && (it.attr("PartName") ?: "").removePrefix("/") == part
        } ?: return
        entries.replaceText("[Content_Types].xml", ct.substring(0, ov.start) + ct.substring(MiniXml.elementEnd(ct, ov)))
    }

    /** Ask Excel to recalculate on open: new formulas carry no cached value. */
    private fun markRecalc(entries: MutableList<Entry>, workbookPath: String) {
        val wb = entries.text(workbookPath) ?: return
        val calc = MiniXml.tags(wb).firstOrNull { it.name == "calcPr" && it.kind != Kind.Close }
        val next = if (calc != null) {
            if (calc.attrs.containsKey("fullCalcOnLoad")) return
            val at = 1 + calc.qname.length
            val raw = wb.substring(calc.start, calc.end)
            wb.substring(0, calc.start) + raw.substring(0, at) + " fullCalcOnLoad=\"1\"" + raw.substring(at) +
                wb.substring(calc.end)
        } else {
            // calcPr sits after sheets / functionGroups / externalReferences /
            // definedNames in the schema's sequence.
            val anchor = listOf("definedNames", "externalReferences", "functionGroups", "sheets")
                .firstNotNullOfOrNull { n ->
                    MiniXml.tags(wb).firstOrNull { it.name == n && it.kind != Kind.Close }
                } ?: return
            val end = MiniXml.elementEnd(wb, anchor)
            val p = if (anchor.qname.contains(':')) anchor.qname.substringBefore(':') + ":" else ""
            wb.substring(0, end) + "<${p}calcPr fullCalcOnLoad=\"1\"/>" + wb.substring(end)
        }
        entries.replaceText(workbookPath, next)
    }

    // ── a blank workbook (tests, and a new sheet on the phone) ──────────

    /** A minimal valid one-sheet workbook. */
    fun blank(): ByteArray = zip(
        listOf(
            Entry(
                "[Content_Types].xml",
                ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" +
                    "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">" +
                    "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>" +
                    "<Default Extension=\"xml\" ContentType=\"application/xml\"/>" +
                    "<Override PartName=\"/xl/workbook.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml\"/>" +
                    "<Override PartName=\"/xl/worksheets/sheet1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/>" +
                    "</Types>").toByteArray(),
                0,
            ),
            Entry(
                "_rels/.rels",
                ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" +
                    "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">" +
                    "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"xl/workbook.xml\"/>" +
                    "</Relationships>").toByteArray(),
                0,
            ),
            Entry(
                "xl/workbook.xml",
                ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" +
                    "<workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" " +
                    "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">" +
                    "<sheets><sheet name=\"Sheet1\" sheetId=\"1\" r:id=\"rId1\"/></sheets></workbook>").toByteArray(),
                0,
            ),
            Entry(
                "xl/_rels/workbook.xml.rels",
                ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" +
                    "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">" +
                    "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet1.xml\"/>" +
                    "</Relationships>").toByteArray(),
                0,
            ),
            Entry(
                "xl/worksheets/sheet1.xml",
                ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" +
                    "<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">" +
                    "<sheetData/></worksheet>").toByteArray(),
                0,
            ),
        ),
    )
}
