package com.domovoi.app

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Android's regular expressions are ICU's, which reject things the desktop
 * JVM these tests run on accepts: the `(?U)` inline flag and its
 * `UNICODE_CHARACTER_CLASS` option (a PatternSyntaxException or
 * IllegalArgumentException on the phone, a pass here), and `CANON_EQ`.
 * ICU's inline flags are i, m, s, x and w; anything else fails on a device.
 * This scans the app's own sources so none of these can come back.
 * Use explicit classes instead (phonemizer/Py.kt: W, D, S, B).
 */
class IcuRegexSafetyTest {
    private fun mainDir(): File =
        listOf(File("src/main"), File("app/src/main")).firstOrNull { it.isDirectory }
            ?: error("cannot find src/main from ${File(".").absolutePath}")

    private val sources: List<File> by lazy {
        mainDir().walkTopDown().filter { it.isFile && (it.extension == "kt" || it.extension == "java") }.toList()
    }

    private fun offenders(test: (String) -> Boolean): List<String> =
        sources.flatMap { f ->
            f.readLines().mapIndexedNotNull { i, line -> if (test(line)) "${f.path}:${i + 1}: ${line.trim()}" else null }
        }

    @Test
    fun scansTheAppSources() {
        assertTrue("found only ${sources.size} sources under ${mainDir().absolutePath}", sources.size > 50)
    }

    @Test
    fun noUnicodeCharacterClassFlag() {
        val bad = offenders { "(?U)" in it || "UNICODE_CHARACTER_CLASS" in it }
        assertTrue("ICU (Android) rejects (?U) / UNICODE_CHARACTER_CLASS:\n" + bad.joinToString("\n"), bad.isEmpty())
    }

    @Test
    fun onlyInlineFlagsIcuAccepts() {
        // (?flags) or (?flags:...) or (?on-off) — lookarounds and (?:...) don't match.
        val inline = Regex("""\(\?([A-Za-z]+)(?:-([A-Za-z]+))?[:)]""")
        val bad = offenders { line ->
            inline.findAll(line).any { m -> (m.groupValues[1] + m.groupValues[2]).any { it !in "imsxw" } }
        }
        assertTrue("inline regex flags ICU (Android) rejects:\n" + bad.joinToString("\n"), bad.isEmpty())
    }

    @Test
    fun noCanonicalEquivalenceOption() {
        val bad = offenders { "CANON_EQ" in it }
        assertTrue("ICU (Android) has no CANON_EQ:\n" + bad.joinToString("\n"), bad.isEmpty())
    }

    @Test
    fun theScanCatchesTheFlag() {
        val probe = "    private val X = Regex(\"\"\"(?U)\\b\\w+\"\"\")"
        assertTrue("(?U)" in probe)
        val inline = Regex("""\(\?([A-Za-z]+)(?:-([A-Za-z]+))?[:)]""")
        assertTrue(inline.findAll("(?iU)x").any { m -> m.groupValues[1].any { it !in "imsxw" } })
        assertTrue(inline.findAll("(?:x)(?<=y)(?!z)(?i)").none { m -> m.groupValues[1].any { it !in "imsxw" } })
    }
}
