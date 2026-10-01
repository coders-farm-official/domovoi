package com.domovoi.app.testing

import android.content.ContextWrapper
import android.content.res.Configuration
import android.graphics.Rect
import android.os.Build
import android.view.View
import androidx.activity.OnBackPressedDispatcher
import androidx.activity.OnBackPressedDispatcherOwner
import androidx.activity.compose.LocalOnBackPressedDispatcherOwner
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.LocalOverscrollConfiguration
import androidx.compose.foundation.gestures.BringIntoViewSpec
import androidx.compose.foundation.gestures.LocalBringIntoViewSpec
import androidx.compose.runtime.AbstractApplier
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.ui.graphics.GraphicsContext
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalGraphicsContext
import androidx.compose.ui.platform.LocalFontFamilyResolver
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.LocalViewConfiguration
import androidx.compose.ui.platform.ViewConfiguration
import androidx.compose.ui.text.font.createFontFamilyResolver
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.LayoutDirection
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.window.layout.WindowInfoTracker
import androidx.window.layout.WindowInfoTrackerDecorator
import androidx.window.layout.WindowLayoutInfo
import androidx.window.layout.WindowMetrics
import androidx.window.layout.WindowMetricsCalculator
import androidx.window.layout.WindowMetricsCalculatorDecorator
import kotlinx.coroutines.flow.flowOf
import java.lang.reflect.Proxy

/**
 * What an Activity's ComposeView provides its content, faked for a
 * composition with no window: enough for the app's screens and the
 * Material / foundation components they use to COMPOSE on the JVM. Nothing
 * is measured or drawn — a lazy list's rows, which only exist once it is
 * measured, are never built, which is exactly what a test of laziness
 * needs. Pair with [composeTest] and a [NodeTree] applier.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun HeadlessUi(content: @Composable () -> Unit) {
    val context = remember { HeadlessContext() }
    val owner = remember { HeadlessOwner() }
    CompositionLocalProvider(
        LocalContext provides context,
        LocalConfiguration provides remember { Configuration() },
        LocalView provides remember { View(context) },
        LocalDensity provides Density(1f),
        LocalGraphicsContext provides remember { proxyOf(GraphicsContext::class.java) { null } },
        LocalLayoutDirection provides LayoutDirection.Ltr,
        LocalViewConfiguration provides HeadlessViewConfiguration,
        LocalFontFamilyResolver provides remember { createFontFamilyResolver(context) },
        LocalLifecycleOwner provides owner,
        LocalOnBackPressedDispatcherOwner provides owner,
        LocalOverscrollConfiguration provides null,
        LocalBringIntoViewSpec provides object : BringIntoViewSpec {},
        content = content,
    )
}

/** Run [block] with the window library answering for a compact window with
 *  no folds (material3-adaptive's `currentWindowAdaptiveInfo`), and with
 *  foundation's lazy layouts able to compose ([actLikeAJvmTestRunner]). */
internal inline fun <T> withHeadlessWindow(block: () -> T): T {
    actLikeAJvmTestRunner()
    WindowMetricsCalculator.overrideDecorator(object : WindowMetricsCalculatorDecorator {
        override fun decorate(calculator: WindowMetricsCalculator): WindowMetricsCalculator =
            proxyOf(WindowMetricsCalculator::class.java) { WindowMetrics(Rect()) }
    })
    WindowInfoTracker.overrideDecorator(object : WindowInfoTrackerDecorator {
        override fun decorate(tracker: WindowInfoTracker): WindowInfoTracker =
            proxyOf(WindowInfoTracker::class.java) { flowOf(WindowLayoutInfo(emptyList())) }
    })
    try {
        return block()
    } finally {
        WindowMetricsCalculator.reset()
        WindowInfoTracker.reset()
    }
}

/**
 * Foundation's lazy layouts read `Build.FINGERPRINT` once, to pick the
 * prefetch scheduler a test runner without a real Choreographer needs
 * (Robolectric's): in the unit-test android.jar it is null, and that read
 * throws. Give it Robolectric's value, so a lazy list composes here too.
 */
@PublishedApi
internal fun actLikeAJvmTestRunner() {
    if (Build.FINGERPRINT != null) return
    val field = Build::class.java.getField("FINGERPRINT")
    val unsafeClass = Class.forName("sun.misc.Unsafe")
    val unsafe = unsafeClass.getDeclaredField("theUnsafe").apply { isAccessible = true }.get(null)
    val base = unsafeClass.getMethod("staticFieldBase", java.lang.reflect.Field::class.java).invoke(unsafe, field)
    val offset = unsafeClass.getMethod("staticFieldOffset", java.lang.reflect.Field::class.java).invoke(unsafe, field) as Long
    unsafeClass.getMethod("putObject", Any::class.java, java.lang.Long.TYPE, Any::class.java)
        .invoke(unsafe, base, offset, "robolectric")
}

/** Every method of [type] answers [answer]. */
@Suppress("UNCHECKED_CAST")
internal fun <T> proxyOf(type: Class<T>, answer: () -> Any?): T =
    Proxy.newProxyInstance(type.classLoader, arrayOf(type)) { proxy, method, args ->
        when (method.name) {
            "equals" -> proxy === args?.get(0)
            "hashCode" -> System.identityHashCode(proxy)
            "toString" -> "headless ${type.simpleName}"
            else -> answer()
        }
    } as T

/**
 * An applier that keeps the tree of nodes a composition emits (Compose UI's
 * LayoutNodes, never attached to anything), so a test can count what a
 * screen built.
 */
internal class NodeTree : AbstractApplier<Any>(ROOT) {
    private val children = HashMap<Any, MutableList<Any>>()

    private fun kids(of: Any) = children.getOrPut(of) { mutableListOf() }

    override fun insertTopDown(index: Int, instance: Any) = kids(current).add(index, instance)
    override fun insertBottomUp(index: Int, instance: Any) = Unit
    override fun remove(index: Int, count: Int) = kids(current).subList(index, index + count).clear()
    override fun move(from: Int, to: Int, count: Int) = kids(current).move(from, to, count)
    override fun onClear() = children.clear()

    /** Nodes in the tree right now. */
    fun size(): Int {
        fun count(node: Any): Int = children[node].orEmpty().sumOf { 1 + count(it) }
        return count(ROOT)
    }

    /** Every node in the tree, parents before their children. */
    fun nodes(): List<Any> {
        val out = mutableListOf<Any>()
        fun walk(node: Any): Unit = children[node].orEmpty().forEach { out += it; walk(it) }
        walk(ROOT)
        return out
    }

    /** [node] and every node under it. */
    fun subtree(node: Any): List<Any> {
        val out = mutableListOf(node)
        fun walk(n: Any): Unit = children[n].orEmpty().forEach { out += it; walk(it) }
        walk(node)
        return out
    }

    private companion object {
        val ROOT = Any()
    }
}

private class HeadlessOwner : OnBackPressedDispatcherOwner {
    override val lifecycle: Lifecycle = LifecycleRegistry.createUnsafe(this)
    override val onBackPressedDispatcher = OnBackPressedDispatcher()
}

private object HeadlessViewConfiguration : ViewConfiguration {
    override val longPressTimeoutMillis = 400L
    override val doubleTapTimeoutMillis = 300L
    override val doubleTapMinTimeMillis = 40L
    override val touchSlop = 8f
}

/** A Context that is its own application context (the font resolver keeps
 *  that one); every other call answers the unit-test android.jar's default. */
private class HeadlessContext : ContextWrapper(null) {
    override fun getApplicationContext() = this
}
