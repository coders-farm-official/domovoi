package com.domovoi.app.assistant.docs

import com.domovoi.app.ui.screens.documents.MiniXml

/**
 * A small element tree over [MiniXml]'s tag scanner, for reading a Word or
 * OpenDocument part. The document is somebody else's file, so a DOCTYPE or
 * an entity declaration is REFUSED before anything is read (the server's
 * expat refusal, `domovoi/doc_extract/safe.py`); without one there is
 * nothing to expand and nothing to fetch, and [MiniXml.unescape] only knows
 * the five predefined entities and character references anyway. Nesting
 * past [MAX_DEPTH] is refused too.
 */
class XmlEl(val qname: String, val attrs: Map<String, String>) {
    /** [XmlEl] or [String] (text, entities decoded), in document order. */
    val children = ArrayList<Any>()

    /** The local name ("p" for "w:p"). */
    val name: String get() = qname.substringAfter(':')

    fun attr(n: String): String? = attrs[n] ?: attrs.entries.firstOrNull { it.key.substringAfter(':') == n }?.value

    fun elements(): List<XmlEl> = children.filterIsInstance<XmlEl>()

    fun child(local: String): XmlEl? = elements().firstOrNull { it.name == local }

    fun all(local: String): List<XmlEl> = elements().filter { it.name == local }

    /** Every element under this one (itself first), in document order. */
    fun descendants(): Sequence<XmlEl> = sequence {
        yield(this@XmlEl)
        for (c in elements()) yieldAll(c.descendants())
    }

    fun find(local: String): Sequence<XmlEl> = descendants().drop(1).filter { it.name == local }
}

object DocXml {
    const val MAX_DEPTH = 2000
    private val REFUSED = Regex("(?i)<!DOCTYPE|<!ENTITY")

    fun parse(xml: String, what: String): XmlEl {
        if (REFUSED.containsMatchIn(xml)) throw DocRefused("That $what has XML entity declarations, which Domovoi doesn't read.")
        val root = XmlEl("#document", emptyMap())
        val stack = ArrayList<XmlEl>().apply { add(root) }
        var pos = 0
        for (tag in MiniXml.tags(xml)) {
            if (tag.start > pos) {
                val t = MiniXml.textBetween(xml, pos, tag.start)
                if (t.isNotEmpty()) stack.last().children += t
            }
            pos = tag.end
            when (tag.kind) {
                MiniXml.Kind.Open -> {
                    val el = XmlEl(tag.qname, tag.attrs)
                    stack.last().children += el
                    stack += el
                    if (stack.size > MAX_DEPTH) throw DocRefused("That $what is nested too deeply to read safely.")
                }
                MiniXml.Kind.Empty -> stack.last().children += XmlEl(tag.qname, tag.attrs)
                MiniXml.Kind.Close -> {
                    val at = stack.indexOfLast { it.qname == tag.qname }
                    if (at > 0) while (stack.size > at) stack.removeAt(stack.lastIndex)
                }
            }
        }
        return root.elements().firstOrNull() ?: throw DocRefused("That $what is damaged and couldn't be read.")
    }
}
