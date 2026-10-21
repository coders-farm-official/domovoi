package com.domovoi.app.assistant.drafts

import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.util.Locale
import java.util.zip.Deflater

/**
 * The phone's own small PDF writer for drafts (review round 1): text only,
 * the laid-out lines ([DraftLayout]) written one `Tj` per line in the
 * bundled Liberation fonts, embedded and subset (TrueType outlines as a
 * CIDFontType2, Identity-H, with a ToUnicode map), every space a real space
 * glyph and no per-glyph positioning or kerning.
 *
 * Why not Android's `PdfDocument`: Skia writes no glyph for a space and
 * places glyphs one by one, so readers rebuild the words from gaps, and the
 * common ones guess differently (poppler `-raw` read "reviewof", pdfminer
 * "T oledo"). Written like this, pdfminer, pypdf, poppler (default and
 * `-raw`) and pdfium all read exactly the lines the server's ReportLab PDF
 * reads. Plain JVM code, so the unit tests write and read it too.
 */
object DraftPdfWriter {
    /** A font the lines use: its metrics and its TrueType file. */
    class Face(val font: DraftFont, val ttf: ByteArray)

    fun write(lay: DraftLaidOut, face: (String) -> Face, title: String = ""): ByteArray {
        val s = DraftLayout.SCALE
        val stems = lay.pages.flatten().map { it.font }.distinct()
        val faces = stems.associateWith(face)
        // Which glyphs each font draws, and the text each glyph stands for.
        val used = stems.associateWith { sortedMapOf(0 to "") }
        fun glyphs(stem: String, text: String): IntArray {
            val f = faces.getValue(stem).font
            val out = ArrayList<Int>()
            var i = 0
            while (i < text.length) {
                val cp = text.codePointAt(i)
                i += Character.charCount(cp)
                val g = f.glyph(cp)
                out += g
                used.getValue(stem).putIfAbsent(g, String(Character.toChars(cp)))
            }
            return out.toIntArray()
        }

        val pdf = Out()
        val catalog = pdf.reserve()
        val pages = pdf.reserve()
        val fontIds = stems.associateWith { pdf.reserve() }
        val fontNames = stems.withIndex().associate { (i, st) -> st to "F${i + 1}" }

        // Pages first (their content fixes which glyphs each font needs).
        val pageIds = ArrayList<Int>()
        for (lines in lay.pages) {
            val c = StringBuilder()
            // Each string in its own text object, as the server's ReportLab
            // writes it (position, font, leading, string, next line), so
            // even the readers that group by text object read the same.
            fun put(stem: String, size: Int, x: Int, y: Int, text: String) {
                if (text.isEmpty()) return
                c.append("BT 1 0 0 1 ").append(num(x, s)).append(' ').append(num(lay.height - y, s)).append(" Tm /")
                    .append(fontNames.getValue(stem)).append(' ').append(num(size, s)).append(" Tf ")
                    .append(num(size * 6 / 5, s)).append(" TL <")
                for (g in glyphs(stem, text)) c.append(String.format(Locale.ROOT, "%04X", g))
                c.append("> Tj T* ET\n")
            }
            for (ln in lines) {
                if (ln.marker.isNotEmpty()) put(ln.font, ln.size, ln.markerX, ln.y, ln.marker + " ")
                // A wrap's kept space isn't drawn (the line break keeps the
                // words apart), the same on the server (pdf_file.py).
                put(ln.font, ln.size, ln.x, ln.y, ln.text.trimEnd(' '))
            }
            val content = pdf.stream("", c.toString().toByteArray(Charsets.ISO_8859_1))
            val fonts = fontIds.entries.joinToString(" ") { (st, id) -> "/${fontNames.getValue(st)} $id 0 R" }
            pageIds += pdf.obj(
                "<< /Type /Page /Parent $pages 0 R /MediaBox [0 0 ${num(lay.width, s)} ${num(lay.height, s)}] " +
                    "/Resources << /Font << $fonts >> >> /Contents $content 0 R >>",
            )
        }
        for (stem in stems) writeFont(pdf, fontIds.getValue(stem), stem, faces.getValue(stem), used.getValue(stem))
        pdf.obj(pages, "<< /Type /Pages /Kids [${pageIds.joinToString(" ") { "$it 0 R" }}] /Count ${pageIds.size} >>")
        pdf.obj(catalog, "<< /Type /Catalog /Pages $pages 0 R >>")
        val info = pdf.obj("<< /Title ${utf16(title)} /Creator (Domovoi) /Producer (Domovoi) >>")
        return pdf.finish(catalog, info)
    }

    private fun writeFont(pdf: Out, id: Int, stem: String, face: Face, used: Map<Int, String>) {
        val t = Ttf(face.ttf)
        val upem = face.font.unitsPerEm
        fun em(v: Int): String = trim(String.format(Locale.ROOT, "%.3f", v * 1000.0 / upem))
        val subset = t.subset(used.keys)
        val tag = MessageDigest.getInstance("SHA-1").digest((stem + used.keys.joinToString(",")).toByteArray())
            .take(6).joinToString("") { ('A' + ((it.toInt() and 0xFF) % 26)).toString() }
        val name = "$tag+$stem"
        val file = pdf.stream("/Length1 ${subset.size}", subset)
        val bold = stem.contains("Bold")
        val serif = stem.contains("Serif")
        val desc = pdf.obj(
            "<< /Type /FontDescriptor /FontName /$name /Flags ${if (serif) 34 else 32} " +
                "/FontBBox [${em(t.xMin)} ${em(t.yMin)} ${em(t.xMax)} ${em(t.yMax)}] /ItalicAngle 0 " +
                "/Ascent ${em(t.ascent)} /Descent ${em(t.descent)} /CapHeight ${em(t.capHeight)} " +
                "/StemV ${if (bold) 140 else 80} /FontFile2 $file 0 R >>",
        )
        val widths = used.keys.joinToString(" ") { g -> "$g [${em(t.advance(g))}]" }
        val cid = pdf.obj(
            "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /$name " +
                "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> " +
                "/FontDescriptor $desc 0 R /DW 1000 /W [$widths] /CIDToGIDMap /Identity >>",
        )
        val toUnicode = pdf.stream("", toUnicode(used).toByteArray(Charsets.ISO_8859_1))
        pdf.obj(
            id,
            "<< /Type /Font /Subtype /Type0 /BaseFont /$name /Encoding /Identity-H " +
                "/DescendantFonts [$cid 0 R] /ToUnicode $toUnicode 0 R >>",
        )
    }

    /** The glyph → text map readers use to turn the codes back into words. */
    private fun toUnicode(used: Map<Int, String>): String {
        val entries = used.filter { it.value.isNotEmpty() }.entries.toList()
        val b = StringBuilder(
            "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n" +
                "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n" +
                "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n" +
                "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n",
        )
        for (chunk in entries.chunked(100)) {
            b.append(chunk.size).append(" beginbfchar\n")
            for ((g, text) in chunk) {
                b.append(String.format(Locale.ROOT, "<%04X> <", g))
                for (ch in text) b.append(String.format(Locale.ROOT, "%04X", ch.code))
                b.append(">\n")
            }
            b.append("endbfchar\n")
        }
        b.append("endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")
        return b.toString()
    }

    /** Hundredths of a point as a PDF number ("72", "711.5"). */
    private fun num(c: Int, scale: Int): String {
        val neg = c < 0
        val a = Math.abs(c)
        val whole = a / scale
        val frac = a % scale
        val f = if (frac == 0) "" else "." + frac.toString().padStart(2, '0').trimEnd('0')
        return (if (neg) "-" else "") + whole + f
    }

    private fun trim(s: String): String = if (s.contains('.')) s.trimEnd('0').trimEnd('.') else s

    /** A PDF text string: plain when it's printable ASCII, else UTF-16BE. */
    private fun utf16(text: String): String {
        if (text.all { it.code in 0x20..0x7E && it != '(' && it != ')' && it != '\\' }) return "($text)"
        val b = StringBuilder("<FEFF")
        for (ch in text) b.append(String.format(Locale.ROOT, "%04X", ch.code))
        return b.append('>').toString()
    }

    /** The file being written: objects by number, byte offsets for the xref. */
    private class Out {
        private val buf = ByteArrayOutputStream()
        private val offsets = HashMap<Int, Int>()
        private var next = 1

        init {
            buf.write("%PDF-1.4\n%".toByteArray(Charsets.ISO_8859_1))
            buf.write(byteArrayOf(0xE2.toByte(), 0xE3.toByte(), 0xCF.toByte(), 0xD3.toByte(), '\n'.code.toByte()))
        }

        fun reserve(): Int = next++

        fun obj(body: String): Int = reserve().also { obj(it, body) }

        fun obj(id: Int, body: String) {
            offsets[id] = buf.size()
            buf.write("$id 0 obj\n$body\nendobj\n".toByteArray(Charsets.ISO_8859_1))
        }

        /** A Flate stream; [extra] goes in its dictionary. */
        fun stream(extra: String, data: ByteArray): Int {
            val z = deflate(data)
            val id = reserve()
            offsets[id] = buf.size()
            buf.write("$id 0 obj\n<< /Length ${z.size} /Filter /FlateDecode $extra>>\nstream\n".toByteArray(Charsets.ISO_8859_1))
            buf.write(z)
            buf.write("\nendstream\nendobj\n".toByteArray(Charsets.ISO_8859_1))
            return id
        }

        fun finish(root: Int, info: Int): ByteArray {
            val xref = buf.size()
            val n = next
            val b = StringBuilder("xref\n0 $n\n0000000000 65535 f \n")
            for (i in 1 until n) b.append(String.format(Locale.ROOT, "%010d 00000 n \n", offsets.getValue(i)))
            b.append("trailer\n<< /Size $n /Root $root 0 R /Info $info 0 R >>\nstartxref\n$xref\n%%EOF\n")
            buf.write(b.toString().toByteArray(Charsets.ISO_8859_1))
            return buf.toByteArray()
        }

        private fun deflate(data: ByteArray): ByteArray {
            val d = Deflater(Deflater.BEST_COMPRESSION)
            try {
                d.setInput(data)
                d.finish()
                val out = ByteArrayOutputStream()
                val chunk = ByteArray(8192)
                while (!d.finished()) {
                    val k = d.deflate(chunk)
                    out.write(chunk, 0, k)
                }
                return out.toByteArray()
            } finally {
                d.end()
            }
        }
    }

    /**
     * Just enough of a TrueType file to embed a subset: the glyphs drawn
     * (and the parts of composite ones) keep their outlines and numbers;
     * every other glyph is emptied, and the tables a PDF reader doesn't need
     * (layout, kerning, names of glyphs) are left out.
     */
    private class Ttf(private val data: ByteArray) {
        private val b = ByteBuffer.wrap(data).order(ByteOrder.BIG_ENDIAN)
        private fun u16(at: Int) = b.getShort(at).toInt() and 0xFFFF
        private fun s16(at: Int) = b.getShort(at).toInt()
        private fun u32(at: Int) = b.getInt(at)

        private val tables: Map<String, Pair<Int, Int>> = (0 until u16(4)).associate { i ->
            val at = 12 + 16 * i
            String(data, at, 4, Charsets.ISO_8859_1) to (u32(at + 8) to u32(at + 12))
        }
        private fun at(tag: String) = tables.getValue(tag).first

        val numGlyphs = u16(at("maxp") + 4)
        private val longLoca = s16(at("head") + 50) == 1
        val xMin = s16(at("head") + 36)
        val yMin = s16(at("head") + 38)
        val xMax = s16(at("head") + 40)
        val yMax = s16(at("head") + 42)
        val ascent = s16(at("hhea") + 4)
        val descent = s16(at("hhea") + 6)
        val capHeight: Int = tables["OS/2"]?.let { (o, len) -> if (u16(o) >= 2 && len >= 90) s16(o + 88) else null } ?: ascent
        private val nHm = u16(at("hhea") + 34)

        fun advance(g: Int): Int = u16(at("hmtx") + 4 * minOf(g, nHm - 1))

        private fun loca(g: Int): Int = if (longLoca) u32(at("loca") + 4 * g) else 2 * u16(at("loca") + 2 * g)

        private fun glyph(g: Int): ByteArray {
            val start = loca(g)
            val end = loca(g + 1)
            return data.copyOfRange(at("glyf") + start, at("glyf") + end)
        }

        /** The glyphs a composite glyph is made of. */
        private fun parts(g: ByteArray): List<Int> {
            if (g.size < 10 || ByteBuffer.wrap(g).short >= 0) return emptyList()
            val gb = ByteBuffer.wrap(g)
            val out = ArrayList<Int>()
            var p = 10
            while (true) {
                val flags = gb.getShort(p).toInt() and 0xFFFF
                out += gb.getShort(p + 2).toInt() and 0xFFFF
                p += 4 + (if (flags and 0x1 != 0) 4 else 2)
                p += when {
                    flags and 0x8 != 0 -> 2
                    flags and 0x40 != 0 -> 4
                    flags and 0x80 != 0 -> 8
                    else -> 0
                }
                if (flags and 0x20 == 0) break
            }
            return out
        }

        fun subset(keep: Set<Int>): ByteArray {
            val all = HashSet<Int>()
            val todo = ArrayDeque(keep.filter { it in 0 until numGlyphs })
            while (todo.isNotEmpty()) {
                val g = todo.removeFirst()
                if (!all.add(g)) continue
                parts(glyph(g)).forEach { if (it in 0 until numGlyphs && it !in all) todo += it }
            }
            val glyf = ByteArrayOutputStream()
            val loca = ByteBuffer.allocate(4 * (numGlyphs + 1)).order(ByteOrder.BIG_ENDIAN)
            for (g in 0 until numGlyphs) {
                loca.putInt(glyf.size())
                if (g in all) {
                    val bytes = glyph(g)
                    glyf.write(bytes)
                    repeat((4 - bytes.size % 4) % 4) { glyf.write(0) }
                }
            }
            loca.putInt(glyf.size())
            val head = data.copyOfRange(at("head"), at("head") + tables.getValue("head").second)
            ByteBuffer.wrap(head).order(ByteOrder.BIG_ENDIAN).apply {
                putInt(8, 0)              // checkSumAdjustment: not checked by readers
                putShort(50, 1)           // long loca
            }
            val out = sortedMapOf<String, ByteArray>()
            for (tag in listOf("OS/2", "cvt ", "fpgm", "hhea", "hmtx", "maxp", "prep")) {
                tables[tag]?.let { (o, len) -> out[tag] = data.copyOfRange(o, o + len) }
            }
            out["head"] = head
            out["loca"] = loca.array()
            out["glyf"] = glyf.toByteArray()
            return sfnt(out)
        }

        private fun sfnt(tables: Map<String, ByteArray>): ByteArray {
            val n = tables.size
            var pow = 1
            var log = 0
            while (pow * 2 <= n) {
                pow *= 2
                log++
            }
            val dir = ByteBuffer.allocate(12 + 16 * n).order(ByteOrder.BIG_ENDIAN)
            dir.putInt(0x00010000).putShort(n.toShort()).putShort((pow * 16).toShort())
                .putShort(log.toShort()).putShort((n * 16 - pow * 16).toShort())
            val body = ByteArrayOutputStream()
            var offset = 12 + 16 * n
            for ((tag, bytes) in tables) {
                val padded = bytes.copyOf((bytes.size + 3) / 4 * 4)
                val pb = ByteBuffer.wrap(padded).order(ByteOrder.BIG_ENDIAN)
                var sum = 0
                while (pb.hasRemaining()) sum += pb.int
                dir.put(tag.toByteArray(Charsets.ISO_8859_1)).putInt(sum).putInt(offset).putInt(bytes.size)
                body.write(padded)
                offset += padded.size
            }
            return dir.array() + body.toByteArray()
        }
    }
}
