package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ModelCatalogTest {

    /** https://huggingface.co/<owner>/<repo>/resolve/<40-hex commit>/<file> */
    private val pinned = Regex("""^https://huggingface\.co/[\w.-]+/[\w.-]+/resolve/[0-9a-f]{40}/[\w./-]+$""")

    @Test fun everyUrlIsPinnedToAnExactCommit() {
        ModelCatalog.all.flatMap { it.files }.forEach { f ->
            assertTrue("not pinned: ${f.url}", pinned.matches(f.url))
            assertFalse("floating revision: ${f.url}", f.url.contains("/resolve/main/"))
        }
    }

    @Test fun everyFileHasASha256AndASize() {
        ModelCatalog.all.flatMap { it.files }.forEach { f ->
            assertTrue("bad sha256 for ${f.path}", Regex("^[0-9a-f]{64}$").matches(f.sha256))
            assertTrue("no size for ${f.path}", f.size > 0)
        }
    }

    @Test fun theUrlNamesTheSameFileAsThePath() {
        ModelCatalog.all.flatMap { it.files }.forEach { f ->
            assertTrue("${f.url} does not end in ${f.path}", f.url.endsWith("/" + f.path))
        }
    }

    @Test fun idsArePlainAndUniqueAndPathsStayInsideTheModelDirectory() {
        val ids = ModelCatalog.all.map { it.id }
        assertEquals(ids.size, ids.toSet().size)
        ids.forEach { assertTrue(it, Regex("^[a-z0-9-]+$").matches(it)) }
        ModelCatalog.all.forEach { m ->
            val paths = m.files.map { it.path }
            assertEquals("duplicate path in ${m.id}", paths.size, paths.toSet().size)
            paths.forEach { p ->
                assertFalse(p, p.startsWith("/") || p.contains("..") || p.contains('\\') || p.startsWith(".installed"))
            }
        }
    }

    @Test fun oneOfEachKindTheBenchmarkNeeds() {
        assertEquals(ModelKind.LLM, ModelCatalog.gemma4E2b.kind)
        assertTrue(ModelCatalog.speechModels.all { it.kind == ModelKind.STT })
        assertEquals(ModelKind.VAD, ModelCatalog.sileroVad.kind)
        assertEquals(ModelKind.TTS, ModelCatalog.piperTimingVoice.kind)
        assertTrue(ModelCatalog.all.containsAll(ModelCatalog.speechModels))
    }

    /** The research-only voice may be used to measure, never shipped: it is
     *  the only timing-only entry, and no other entry is a voice. */
    @Test fun theTimingVoiceIsMarkedAndIsTheOnlyVoice() {
        assertTrue(ModelCatalog.piperTimingVoice.timingOnly)
        assertEquals(listOf(ModelCatalog.piperTimingVoice), ModelCatalog.all.filter { it.timingOnly })
        assertEquals(listOf(ModelCatalog.piperTimingVoice), ModelCatalog.all.filter { it.kind == ModelKind.TTS })
        ModelCatalog.all.filter { it.files.any { f -> f.url.contains("lessac", ignoreCase = true) } }
            .forEach { assertTrue("${it.id} uses the research-only voice", it.timingOnly) }
        assertTrue(ModelCatalog.piperTimingVoice.id.contains("timing-only"))
    }

    /** Permissive licences only, apart from the timing-only voice. */
    @Test fun shippableModelsArePermissivelyLicensed() {
        val permissive = setOf("Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "ISC")
        ModelCatalog.all.filterNot { it.timingOnly }.forEach {
            assertTrue("${it.id}: ${it.license}", it.license in permissive)
            assertTrue(it.licenseUrl.startsWith("https://"))
        }
    }

    @Test fun totalsAddUp() {
        assertEquals(2_588_147_712, ModelCatalog.gemma4E2b.totalBytes)
        assertEquals(13_281_600L + 30_412_256 + 549_350, ModelCatalog.moonshineTinyEn.totalBytes)
    }
}
