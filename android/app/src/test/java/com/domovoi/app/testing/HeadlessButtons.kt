package com.domovoi.app.testing

import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.InspectableValue
import androidx.compose.ui.semantics.SemanticsModifier
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull

/**
 * A button a headless composition built ([NodeTree], [HeadlessUi]), read off
 * the modifiers its node was given: the content description of the icon in
 * it, whether it takes a tap, and the tap itself. Nothing is attached, laid
 * out or drawn on the JVM, so there is no semantics tree to ask; a node's
 * modifier chain is what the button is. An IconButton is a Box whose
 * modifier ends in `clickable(enabled, onClick)` around its Icon, whose own
 * node carries the `semantics { contentDescription }`.
 */
internal class HeadlessButton(val label: String?, val enabled: Boolean, private val onClick: (() -> Unit)?) {
    fun click() = (onClick ?: error("\"$label\" has no tap")).invoke()
    override fun toString() = "\"$label\"" + if (enabled) "" else " (disabled)"
}

/** Every clickable in the tree, in composition order. */
internal fun NodeTree.buttons(): List<HeadlessButton> = nodes().mapNotNull { node ->
    val clickable = elementsOf(node)
        .filterIsInstance<InspectableValue>()
        .firstOrNull { it.nameFallback == "clickable" } ?: return@mapNotNull null
    val props = clickable.inspectableElements.associate { it.name to it.value }
    val label = subtree(node).asSequence().flatMap { elementsOf(it).asSequence() }
        .filterIsInstance<SemanticsModifier>()
        .firstNotNullOfOrNull { it.semanticsConfiguration.getOrNull(SemanticsProperties.ContentDescription)?.firstOrNull() }
    @Suppress("UNCHECKED_CAST")
    HeadlessButton(label, props["enabled"] as Boolean, props["onClick"] as? () -> Unit)
}

/** The one button labelled [label]. */
internal fun NodeTree.button(label: String): HeadlessButton {
    val all = buttons()
    return all.singleOrNull { it.label == label } ?: error("no single \"$label\" button in $all")
}

/** The modifier elements a LayoutNode was given. A node that was never
 *  attached keeps its modifier pending (Compose UI 1.7) instead of applying
 *  it, so that is read first. */
internal fun elementsOf(node: Any): List<Modifier.Element> {
    val pending = runCatching {
        node.javaClass.getDeclaredField("pendingModifier").apply { isAccessible = true }.get(node) as Modifier?
    }.getOrNull()
    val modifier = pending
        ?: runCatching { node.javaClass.getMethod("getModifier").invoke(node) as Modifier }.getOrNull()
        ?: return emptyList()
    return modifier.foldIn(mutableListOf()) { acc, e -> acc.also { it += e } }
}
