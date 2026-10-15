package com.domovoi.app.ui.screens.settings

import com.domovoi.app.assistant.ModelCatalog
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The licences screen kept current with the build: the list the APK
 * carries (`licences/maven.json`, made by app/build.gradle.kts from this
 * variant's dependency graph) is read here as the build wrote it, and
 *
 * - every library in it is under a licence decided by hand (a [LicenceRule]),
 *   and that licence is permissive (docs/CONTRIBUTING.md);
 * - what each library's POM declares agrees with that decision, so an
 *   upgrade that changes a licence fails here until someone looks;
 * - every rule still matches a library (no stale entries);
 * - no analytics, advertising or crash-reporting library is in the app
 *   (the privacy card says so);
 * - the parts the plan names are all listed, with the texts they need.
 *
 * Adding a dependency means adding its rule (or seeing that one covers it)
 * after checking its licence at the source.
 */
class OpenSourceLicencesTest {
    private val dir: File by lazy {
        File(System.getProperty("domovoi.licences.dir") ?: error("run through Gradle: domovoi.licences.dir is not set"))
    }
    private val libraries: List<MavenLibrary> by lazy { OpenSourceLicences.parse(File(dir, OpenSourceLicences.MAVEN_LIST).readText()) }

    @Test fun theBuildListsEveryLibraryInTheApk() {
        assertTrue("only ${libraries.size} libraries", libraries.size > 50)
        listOf(
            "com.google.ai.edge.litertlm:litertlm-android", "com.microsoft.onnxruntime:onnxruntime-android",
            "androidx.camera:camera-core", "com.google.zxing:core", "androidx.compose.ui:ui-android",
            "org.jetbrains.kotlin:kotlin-stdlib", "com.squareup.okhttp3:okhttp", "androidx.work:work-runtime",
        ).forEach { c -> assertTrue("$c is not listed", libraries.any { "${it.group}:${it.name}" == c }) }
        assertEquals("each library once", libraries.size, libraries.map { "${it.group}:${it.name}" }.distinct().size)
    }

    @Test fun everyLibraryIsUnderAPermissiveLicenceDecidedByHand() {
        val unruled = libraries.filter { OpenSourceLicences.ruleFor(it) == null }.map { it.coordinate }
        assertEquals("libraries with no licence rule (check the licence at its source, then add one)", emptyList<String>(), unruled)
        OpenSourceLicences.RULES.forEach { r ->
            r.ids.forEach { id -> assertTrue("${r.title}: $id is not permissive", id in OpenSourceLicences.PERMISSIVE) }
        }
    }

    @Test fun whatEachPomDeclaresAgreesWithTheRule() {
        val disagree = libraries.mapNotNull { lib ->
            val rule = OpenSourceLicences.ruleFor(lib) ?: return@mapNotNull null
            val declared = lib.licences.map { OpenSourceLicences.spdx(it) }
            val problems = buildList {
                if (declared.isEmpty() && rule.name == null) add("its POM declares no licence; give it a rule of its own, checked at the source")
                declared.forEachIndexed { i, id ->
                    val covered = when (id) {
                        null -> false
                        "BSD" -> rule.ids.any { it.startsWith("BSD-") }
                        else -> id in rule.ids
                    }
                    if (!covered) add("its POM says ${lib.licences[i]}, the rule says ${rule.licence}")
                }
            }
            problems.takeIf { it.isNotEmpty() }?.let { "${lib.coordinate}: ${it.joinToString("; ")}" }
        }
        assertEquals(emptyList<String>(), disagree)
    }

    @Test fun everyRuleStillMatchesALibrary() {
        val stale = OpenSourceLicences.RULES.filter { r -> libraries.none { r.matches(it) } }.map { it.title }
        assertEquals(emptyList<String>(), stale)
    }

    @Test fun noAnalyticsAdvertisingOrCrashReporting() {
        val found = libraries.filter { lib -> OpenSourceLicences.NEVER.any { lib.group == it || lib.group.startsWith("$it.") } }
        assertEquals(emptyList<String>(), found.map { it.coordinate })
    }

    @Test fun everyTextAndNoticeTheScreenOffersIsInTheAssets() {
        val texts = OpenSourceLicences.RULES.flatMap { it.texts } + libraries.flatMap { it.notices } +
            OPEN_SOURCE_PARTS.flatMap { it.notices }.filter { it.startsWith("licences/texts/") }
        assertTrue(texts.isNotEmpty())
        texts.distinct().forEach { path -> assertTrue("$path is not in the assets", File(dir, path).isFile) }
        // The libraries' own notices: LiteRT-LM's third-party notice, OkHttp's
        // Public Suffix List notice.
        val litert = libraries.single { it.group == "com.google.ai.edge.litertlm" }
        assertTrue(litert.notices.toString(), litert.notices.any { it.endsWith("THIRD_PARTY_NOTICE.txt") })
        val okhttp = libraries.single { it.group == "com.squareup.okhttp3" && it.name == "okhttp" }
        assertTrue(okhttp.notices.toString(), okhttp.notices.any { File(dir, it).readText().contains("Mozilla Public License") })
        assertTrue(File(dir, OpenSourceLicences.APACHE_TEXT).readText().contains("Apache License"))
        assertTrue(File(dir, OpenSourceLicences.ORT_LICENCE).readText().contains("Copyright (c) Microsoft Corporation"))
        assertTrue(File(dir, OpenSourceLicences.ORT_NOTICES).readText().contains("Eigen"))
        assertTrue(File(dir, OpenSourceLicences.LIBYUV_LICENCE).readText().contains("The LibYuv Project Authors"))
    }

    /** The parts the assistant plan names, each with its licence. */
    @Test fun thePlansPartsAreAllListed() {
        val names = OPEN_SOURCE_PARTS.joinToString("\n") { "${it.name} ${it.note.orEmpty()}" }
        listOf("LiteRT-LM", "sherpa-onnx", "ONNX Runtime", "Eigen (MPL-2.0, unmodified)", "Piper voices", "CameraX and ZXing").forEach {
            assertTrue("missing $it", names.contains(it))
        }
        listOf(ModelCatalog.gemma4E2b, ModelCatalog.moonshineTinyEn, ModelCatalog.sileroVad).forEach { m ->
            assertTrue(m.name, OPEN_SOURCE_PARTS.any { it.name == m.name && it.licence == m.license })
        }
        assertEquals("Gemma 4 is Apache-2.0", "Apache-2.0", ModelCatalog.gemma4E2b.license)
        assertEquals("public domain", OPEN_SOURCE_PARTS.single { it.name.startsWith("Piper voices") }.licence)
    }

    @Test fun licencesFromPomsAreReadAsSpdx() {
        assertEquals("Apache-2.0", OpenSourceLicences.spdx(PomLicence("The Apache Software License, Version 2.0", null)))
        assertEquals("Apache-2.0", OpenSourceLicences.spdx(PomLicence(null, "https://www.apache.org/licenses/LICENSE-2.0.txt")))
        assertEquals("MIT", OpenSourceLicences.spdx(PomLicence("MIT License", "https://opensource.org/licenses/MIT")))
        assertEquals("BSD-3-Clause", OpenSourceLicences.spdx(PomLicence("New BSD License", null)))
        assertEquals("BSD", OpenSourceLicences.spdx(PomLicence("BSD License", null)))
        assertEquals(null, OpenSourceLicences.spdx(PomLicence("GNU General Public License, version 2", null)))
        assertEquals("a GPL library would fail the agreement check", null,
            OpenSourceLicences.spdx(PomLicence("GNU Lesser General Public License", "https://www.gnu.org/licenses/lgpl.html")))
    }

    @Test fun theListShowsByWhoMakesIt() {
        val groups = OpenSourceLicences.grouped(libraries)
        assertEquals(libraries.size, groups.sumOf { it.second.size })
        assertEquals("CameraX camera-core (room mode's QR scanner)", groups.first().first.title)
        assertTrue(OpenSourceLicences.summary(libraries).startsWith("${libraries.size} libraries, from the app's build: "))
        val tree = NodeTree()
        var shown = emptyList<String>()
        withHeadlessWindow {
            composeTest(tree) {
                setContent { HeadlessUi { LibraryGroups(groups, open = null) {} } }
                settle(untilQuiet = false, frames = 5)
                shown = tree.texts()
            }
        }
        assertTrue(shown.toString(), "LiteRT-LM" in shown && "ONNX Runtime" in shown && "AndroidX and Jetpack Compose" in shown)
        assertTrue(shown.any { it.startsWith("MIT · github.com/microsoft/onnxruntime") })
        assertTrue(shown.contains("show onnxruntime/ThirdPartyNotices.txt"))
        assertTrue(shown.contains("show Apache-2.0.txt"))
    }

    @Test fun longNoticesAreCutIntoPieces() {
        assertEquals(listOf(""), noticeChunks(""))
        val text = (1..95).joinToString("\n") { "line $it" }
        val chunks = noticeChunks(text, lines = 40)
        assertEquals(3, chunks.size)
        assertEquals(text, chunks.joinToString("\n"))
        assertEquals("Apache-2.0.txt", noticeLabel("licences/texts/Apache-2.0.txt"))
        assertEquals("sherpa-onnx/eigen/COPYING.MPL2", noticeLabel("licences/sherpa-onnx/eigen/COPYING.MPL2"))
        assertEquals("cmudict/LICENSE", noticeLabel("phonemizer/cmudict/LICENSE"))
        assertEquals("core: LICENSE.txt", noticeLabel("licences/maven/androidx.core/core/META-INF_androidx_core_core_LICENSE.txt"))
        assertEquals("litertlm-android: THIRD_PARTY_NOTICE.txt", noticeLabel("licences/maven/com.google.ai.edge.litertlm/litertlm-android/THIRD_PARTY_NOTICE.txt"))
    }
}
