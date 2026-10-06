package com.domovoi.app.testing

import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.InspectableValue
import androidx.compose.ui.semantics.SemanticsModifier
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull

/**
 * The text a headless composition ([NodeTree], [HeadlessUi]) shows, read
 * the way [buttons] reads buttons: off the modifiers of its nodes. A
 * `Text` is a node whose modifier holds its string (Compose foundation
 * 1.7's TextStringSimpleElement, or TextAnnotatedStringElement for an
 * AnnotatedString). Rows of a lazy list are never built here (nothing is
 * measured), so their text is not in the tree.
 */
internal fun NodeTree.texts(): List<String> = nodes().flatMap { node -> elementsOf(node).mapNotNull(::textOf) }

/** Every content description the tree carries (an Icon's, a button's): what
 *  a screen reader says for the parts that are not text. */
internal fun NodeTree.descriptions(): List<String> = nodes().flatMap { node ->
    elementsOf(node)
        .filterIsInstance<SemanticsModifier>()
        .mapNotNull { it.semanticsConfiguration.getOrNull(SemanticsProperties.ContentDescription)?.firstOrNull() }
}

/** The innermost clickable that shows [text]: a TextButton's label is its
 *  text, not a content description, so [button] cannot find it. */
internal fun NodeTree.textButton(text: String): HeadlessButton {
    val found = nodes().mapNotNull { node ->
        val clickable = elementsOf(node)
            .filterIsInstance<InspectableValue>()
            .firstOrNull { it.nameFallback == "clickable" } ?: return@mapNotNull null
        if (subtree(node).none { n -> elementsOf(n).any { textOf(it) == text } }) return@mapNotNull null
        val props = clickable.inspectableElements.associate { it.name to it.value }
        @Suppress("UNCHECKED_CAST")
        HeadlessButton(text, props["enabled"] as Boolean, props["onClick"] as? () -> Unit)
    }
    return found.lastOrNull() ?: error("no clickable shows \"$text\"; the tree shows ${texts()}")
}

private fun textOf(element: Modifier.Element): String? {
    val name = element.javaClass.simpleName
    if (name != "TextStringSimpleElement" && name != "TextAnnotatedStringElement") return null
    return runCatching {
        element.javaClass.getDeclaredField("text").apply { isAccessible = true }.get(element)?.toString()
    }.getOrNull()
}
