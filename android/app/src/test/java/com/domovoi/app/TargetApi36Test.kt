package com.domovoi.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import javax.xml.parsers.DocumentBuilderFactory

/**
 * The app targets API 36 (Google Play requires it of new apps) and keeps the
 * Android 16 behaviour changes in hand, read from the module's own sources:
 *
 * - compileSdk / targetSdk 36, minSdk still 26 (Android 8);
 * - edge to edge with no opt-out (Android 16 removes it): every activity
 *   calls enableEdgeToEdge, and no theme sets windowOptOutEdgeToEdgeEnforcement;
 * - predictive back: opted in for every version, and back is handled only
 *   through BackHandler / OnBackPressedDispatcher (Android 16 no longer calls
 *   onBackPressed or dispatches KEYCODE_BACK to a target-36 app);
 * - large screens: no orientation, resizability or aspect-ratio locks
 *   (Android 16 ignores them from 600 dp up);
 * - no announceForAccessibility (deprecated in 16: live regions instead);
 * - the native libraries built in this repo (sherpa-onnx) are 16 KB aligned;
 *   the whole APK is checked by the build (checkNativeAlignment).
 */
class TargetApi36Test {
    private val android = "http://schemas.android.com/apk/res/android"

    private fun moduleFile(rel: String): File =
        listOf(File(rel), File("app/$rel")).firstOrNull { it.exists() }
            ?: error("cannot find $rel from ${File(".").absolutePath}")

    private val buildScript by lazy { moduleFile("build.gradle.kts").readText() }

    private val manifest by lazy {
        DocumentBuilderFactory.newInstance().apply { isNamespaceAware = true }
            .newDocumentBuilder().parse(moduleFile("src/main/AndroidManifest.xml"))
    }

    private fun elements(tag: String): List<Element> {
        val nodes = manifest.getElementsByTagName(tag)
        return (0 until nodes.length).map { nodes.item(it) as Element }
    }

    private fun Element.attr(name: String): String? = getAttributeNodeNS(android, name)?.value

    private val sources: List<File> by lazy {
        moduleFile("src/main/java").walkTopDown().filter { it.isFile && it.extension == "kt" }.toList()
    }

    @Test fun targetsAndCompilesAgainstApi36AndStillRunsOnAndroid8() {
        assertTrue(Regex("""(?m)^\s*compileSdk = 36\s*$""").containsMatchIn(buildScript))
        assertTrue(Regex("""(?m)^\s*targetSdk = 36\s*$""").containsMatchIn(buildScript))
        assertTrue(Regex("""(?m)^\s*minSdk = 26\s*$""").containsMatchIn(buildScript))
    }

    @Test fun everyActivityIsEdgeToEdgeAndNoThemeOptsOut() {
        val activities = sources.filter { Regex(""":\s*ComponentActivity\(\)""").containsMatchIn(it.readText()) }
        assertEquals(
            activities.map { it.name }.sorted().toString(),
            setOf("MainActivity.kt", "RoomModeActivity.kt", "RoomSetupActivity.kt"),
            activities.map { it.name }.toSet(),
        )
        activities.forEach { assertTrue("${it.name} calls enableEdgeToEdge", it.readText().contains("enableEdgeToEdge(")) }
        val res = moduleFile("src/main/res").walkTopDown().filter { it.isFile && it.extension == "xml" }.toList()
        assertTrue(res.isNotEmpty())
        res.forEach { f ->
            val text = f.readText()
            assertTrue("${f.name} opts out of edge to edge", !text.contains("windowOptOutEdgeToEdgeEnforcement"))
            assertTrue("${f.name} sets elegantTextHeight (ignored on 16)", !text.contains("elegantTextHeight"))
        }
    }

    @Test fun backIsPredictiveAndOnlyThroughTheDispatcher() {
        val app = elements("application").single()
        assertEquals("true", app.attr("enableOnBackInvokedCallback"))
        elements("activity").forEach { a ->
            assertTrue("${a.attr("name")} opts out of predictive back", a.attr("enableOnBackInvokedCallback") != "false")
        }
        val offenders = sources.filter { f ->
            val text = f.readText()
            Regex("""override\s+fun\s+onBackPressed\s*\(""").containsMatchIn(text) || text.contains("KEYCODE_BACK")
        }
        assertEquals(emptyList<String>(), offenders.map { it.name })
    }

    @Test fun noOrientationOrResizeLocks() {
        val locked = (elements("activity") + elements("activity-alias") + elements("application")).filter { e ->
            e.attr("screenOrientation") != null || e.attr("resizeableActivity") == "false" ||
                e.attr("maxAspectRatio") != null || e.attr("minAspectRatio") != null
        }
        assertEquals(emptyList<String?>(), locked.map { it.attr("name") })
        val code = sources.filter { f -> f.readText().let { it.contains("requestedOrientation") || it.contains("setRequestedOrientation") } }
        assertEquals(emptyList<String>(), code.map { it.name })
    }

    @Test fun noDeprecatedAccessibilityAnnouncements() {
        assertEquals(emptyList<String>(), sources.filter { it.readText().contains("announceForAccessibility") }.map { it.name })
    }

    /** The libraries this repo builds itself (android/native/sherpa-onnx):
     *  every LOAD segment of the 64-bit ones aligned to 16 KB. */
    @Test fun theLibrariesBuiltHereAre16KbAligned() {
        val jni = listOf(File("../native/sherpa-onnx/jniLibs"), File("native/sherpa-onnx/jniLibs")).first { it.isDirectory }
        val libs = listOf("arm64-v8a", "x86_64").flatMap { abi -> File(jni, abi).listFiles().orEmpty().filter { it.name.endsWith(".so") } }
        assertEquals(2, libs.size)
        libs.forEach { so ->
            val aligns = loadAligns(so.readBytes())
            assertTrue("${so.path}: $aligns", aligns.isNotEmpty() && aligns.all { it >= 16_384 })
        }
    }

    private fun loadAligns(b: ByteArray): List<Long> {
        require(b[0] == 0x7f.toByte() && b[1] == 'E'.code.toByte() && b[4].toInt() == 2) { "not a 64-bit ELF file" }
        val bb = ByteBuffer.wrap(b).order(if (b[5].toInt() == 1) ByteOrder.LITTLE_ENDIAN else ByteOrder.BIG_ENDIAN)
        val phoff = bb.getLong(0x20)
        val size = bb.getShort(0x36).toInt() and 0xffff
        val count = bb.getShort(0x38).toInt() and 0xffff
        return (0 until count).mapNotNull { i ->
            val at = (phoff + i.toLong() * size).toInt()
            if (bb.getInt(at) == 1) bb.getLong(at + 0x30) else null
        }
    }
}
