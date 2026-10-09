package com.domovoi.app.ui.screens.documents

/**
 * A very small XML tag scanner for the spreadsheet code (XlsxSheet.kt).
 *
 * Not a parser: it walks a document and reports each tag with its exact
 * character offsets, which is what lets a save PATCH a few cells inside a
 * worksheet and copy every other byte through untouched. A DOM round trip
 * would re-serialize the whole part (namespace declarations, attribute order,
 * mc:Ignorable prefixes) and is exactly what this exists to avoid.
 *
 * Comments, processing instructions, DOCTYPE and CDATA sections are skipped
 * as opaque spans. Attribute values are returned unescaped.
 */
internal object MiniXml {

    enum class Kind { Open, Close, Empty }

    class Tag(
        /** Qualified name as written, e.g. "c", "x:c", "r:id". */
        val qname: String,
        val kind: Kind,
        /** Attributes by qualified name, unescaped, in document order. */
        val attrs: LinkedHashMap<String, String>,
        /** Offset of '<'. */
        val start: Int,
        /** Offset just past '>'. */
        val end: Int,
    ) {
        /** Local part of the name ("c" for "x:c"). */
        val name: String get() = qname.substringAfter(':')

        /** Attribute by exact qualified name, else by local name. */
        fun attr(n: String): String? =
            attrs[n] ?: attrs.entries.firstOrNull { it.key.substringAfter(':') == n && it.key.contains(':') }?.value
    }

    /** Every tag in [xml] between [from] and [to], in order. */
    fun tags(xml: String, from: Int = 0, to: Int = xml.length): Sequence<Tag> = sequence {
        var i = from
        while (i < to) {
            val lt = xml.indexOf('<', i)
            if (lt < 0 || lt >= to) break
            when {
                xml.startsWith("<!--", lt) -> {
                    val e = xml.indexOf("-->", lt + 4)
                    i = if (e < 0) to else e + 3
                }
                xml.startsWith("<![CDATA[", lt) -> {
                    val e = xml.indexOf("]]>", lt + 9)
                    i = if (e < 0) to else e + 3
                }
                xml.startsWith("<?", lt) -> {
                    val e = xml.indexOf("?>", lt + 2)
                    i = if (e < 0) to else e + 2
                }
                xml.startsWith("<!", lt) -> {
                    val e = xml.indexOf('>', lt + 2)
                    i = if (e < 0) to else e + 1
                }
                else -> {
                    val tag = readTag(xml, lt) ?: break
                    yield(tag)
                    i = tag.end
                }
            }
        }
    }

    private fun readTag(xml: String, lt: Int): Tag? {
        var p = lt + 1
        val closing = p < xml.length && xml[p] == '/'
        if (closing) p++
        val nameStart = p
        while (p < xml.length && !xml[p].isWhitespace() && xml[p] != '>' && xml[p] != '/') p++
        val qname = xml.substring(nameStart, p)
        val attrs = LinkedHashMap<String, String>()
        while (p < xml.length) {
            while (p < xml.length && xml[p].isWhitespace()) p++
            if (p >= xml.length) return null
            val ch = xml[p]
            if (ch == '>') {
                return Tag(qname, if (closing) Kind.Close else Kind.Open, attrs, lt, p + 1)
            }
            if (ch == '/' && p + 1 < xml.length && xml[p + 1] == '>') {
                return Tag(qname, Kind.Empty, attrs, lt, p + 2)
            }
            // attribute name
            val an = p
            while (p < xml.length && xml[p] != '=' && !xml[p].isWhitespace() && xml[p] != '>' && xml[p] != '/') p++
            val aname = xml.substring(an, p)
            while (p < xml.length && xml[p].isWhitespace()) p++
            if (p < xml.length && xml[p] == '=') {
                p++
                while (p < xml.length && xml[p].isWhitespace()) p++
                if (p >= xml.length) return null
                val q = xml[p]
                if (q == '"' || q == '\'') {
                    val e = xml.indexOf(q, p + 1)
                    if (e < 0) return null
                    attrs[aname] = unescape(xml.substring(p + 1, e))
                    p = e + 1
                } else {
                    val vs = p
                    while (p < xml.length && !xml[p].isWhitespace() && xml[p] != '>') p++
                    attrs[aname] = unescape(xml.substring(vs, p))
                }
            } else if (aname.isEmpty()) {
                p++ // stray character; step over it
            } else {
                attrs[aname] = ""
            }
        }
        return null
    }

    /** Decode the five predefined entities and numeric character references. */
    fun unescape(s: String): String {
        if (s.indexOf('&') < 0) return s
        val out = StringBuilder(s.length)
        var i = 0
        while (i < s.length) {
            val c = s[i]
            if (c == '&') {
                val semi = s.indexOf(';', i + 1)
                if (semi > i) {
                    val ent = s.substring(i + 1, semi)
                    val rep: String? = when {
                        ent == "lt" -> "<"
                        ent == "gt" -> ">"
                        ent == "amp" -> "&"
                        ent == "quot" -> "\""
                        ent == "apos" -> "'"
                        ent.startsWith("#x") || ent.startsWith("#X") -> codePoint(ent.substring(2).toIntOrNull(16))
                        ent.startsWith("#") -> codePoint(ent.substring(1).toIntOrNull())
                        else -> null
                    }
                    if (rep != null) {
                        out.append(rep)
                        i = semi + 1
                        continue
                    }
                }
            }
            out.append(c)
            i++
        }
        return out.toString()
    }

    /**
     * The text for a numeric character reference, or null when [n] is not a
     * code point (unparsable, negative, past U+10FFFF, or a lone surrogate):
     * the reference is then left as written. `Character.toChars` throws for
     * those, and a crafted .xlsx must not throw out of the reader.
     */
    private fun codePoint(n: Int?): String? =
        if (n == null || !Character.isValidCodePoint(n) || n in 0xD800..0xDFFF) null
        else String(Character.toChars(n))

    /** Escape text content (and attribute values: quotes too). */
    fun escape(s: String): String {
        val out = StringBuilder(s.length + 8)
        for (c in s) {
            when (c) {
                '<' -> out.append("&lt;")
                '>' -> out.append("&gt;")
                '&' -> out.append("&amp;")
                '"' -> out.append("&quot;")
                else -> if (c < ' ' && c != '\t' && c != '\n' && c != '\r') {
                    // Control characters are not legal XML 1.0; drop them.
                } else {
                    out.append(c)
                }
            }
        }
        return out.toString()
    }

    /**
     * The offset just past the closing tag of the element [open] starts
     * (nested elements of the same name are counted). For an empty element,
     * its own end; for an unclosed one, the end of the document.
     */
    fun elementEnd(xml: String, open: Tag): Int {
        if (open.kind == Kind.Empty) return open.end
        var depth = 0
        for (t in tags(xml, open.end)) {
            if (t.qname == open.qname) {
                when (t.kind) {
                    Kind.Open -> depth++
                    Kind.Close -> if (depth == 0) return t.end else depth--
                    Kind.Empty -> {}
                }
            }
        }
        return xml.length
    }

    /** Raw character data between [from] and [to] with tags removed, CDATA kept, entities decoded. */
    fun textBetween(xml: String, from: Int, to: Int): String {
        val out = StringBuilder()
        var i = from
        while (i < to) {
            val lt = xml.indexOf('<', i)
            if (lt < 0 || lt >= to) {
                out.append(unescape(xml.substring(i, to)))
                break
            }
            out.append(unescape(xml.substring(i, lt)))
            if (xml.startsWith("<![CDATA[", lt)) {
                val e = xml.indexOf("]]>", lt + 9).let { if (it < 0) to else it }
                out.append(xml, lt + 9, minOf(e, to))
                i = e + 3
            } else if (xml.startsWith("<!--", lt)) {
                val e = xml.indexOf("-->", lt + 4)
                i = if (e < 0) to else e + 3
            } else {
                val gt = xml.indexOf('>', lt)
                i = if (gt < 0) to else gt + 1
            }
        }
        return out.toString()
    }
}
