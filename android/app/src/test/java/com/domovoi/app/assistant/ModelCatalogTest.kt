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
        // Lower case, except a voice's id, which is the server's (en_US-...).
        ModelCatalog.assistant.forEach { assertTrue(it.id, Regex("^[a-z0-9-]+$").matches(it.id)) }
        ModelCatalog.voices.forEach { assertTrue(it.id, Regex("^en_US-[a-z]+-medium$").matches(it.id)) }
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
        assertTrue(ModelCatalog.all.containsAll(ModelCatalog.speechModels))
    }

    /** The phone's voices are the server's three public-domain Piper voices
     *  (2026-10-12), pinned to the server's revision; nothing espeak-ng would
     *  read (its data is GPL-3.0), no lessac/amy/ryan, nothing timing-only. */
    @Test fun theVoicesAreTheServersThreePublicDomainPiperVoices() {
        val voices = ModelCatalog.all.filter { it.kind == ModelKind.TTS }
        assertEquals(listOf("en_US-norman-medium", "en_US-kristin-medium", "en_US-ljspeech-medium"), voices.map { it.id })
        assertEquals(ModelCatalog.voices, voices)
        assertEquals(ModelCatalog.kristin, ModelCatalog.defaultVoice)   // the house voice since 2026-10-12
        assertTrue(ModelCatalog.assistant.none { it.kind == ModelKind.TTS })
        voices.forEach { v ->
            assertEquals("public domain", v.license)
            assertEquals(listOf("${v.id}.onnx", "${v.id}.onnx.json"), v.files.map { it.path })
            v.files.forEach { f ->
                assertTrue(f.url, f.url.startsWith(
                    "https://huggingface.co/rhasspy/piper-voices/resolve/c10ece1aade47bb51c153c893d14e5bf8e5b7117/en/en_US/"))
            }
            assertEquals(63_531_379L, v.files[0].size)
        }
        assertEquals("b9739443232a80a59c7d18810dd856899bf16a7964725f5ab81ea49b1351cb71", ModelCatalog.norman.files[0].sha256)
        assertEquals(emptyList<CatalogModel>(), ModelCatalog.all.filter { it.timingOnly })
        ModelCatalog.all.flatMap { it.files }.forEach { f ->
            assertFalse(f.url, Regex("espeak|lessac|phontab|amy|ryan", RegexOption.IGNORE_CASE).containsMatchIn(f.url))
        }
        assertEquals(ModelCatalog.kristin, ModelCatalog.voice("en_US-kristin-medium"))
        assertEquals(null, ModelCatalog.voice("en_US-lessac-medium"))
    }

    /** Permissive licences only (public domain counts), apart from a timing-only entry. */
    @Test fun shippableModelsArePermissivelyLicensed() {
        val permissive = setOf("Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "ISC", "public domain")
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
