package com.domovoi.app.assistant

import com.domovoi.app.phonemizer.PhonemizerGoldenTest
import com.domovoi.app.voice.PhoneVoiceUi
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.security.MessageDigest

/**
 * "Play a sample" plays the voice it names or is not shown (owner,
 * 2026-10-12): the voice once downloaded, its built-in clip until then.
 * The clips are the repo's `domovoi/voice_clips/`, which the build copies
 * into the assets after checking their SHA256SUMS (the same files the
 * server's play buttons play).
 */
class VoiceClipsTest {
    private val dir: File by lazy {
        System.getProperty("domovoi.voice_clips.dir")?.let(::File)
            ?: File(PhonemizerGoldenTest.phonemizerDir().parentFile, "voice_clips")
    }

    @Test fun everyVoiceThePhoneOffersHasASmallClip() {
        val sums = File(dir, "SHA256SUMS").readLines().filter { it.isNotBlank() && !it.startsWith("#") }
            .associate { line -> line.split(Regex("""\s+"""), limit = 2).let { it[1] to it[0] } }
        assertEquals(ModelCatalog.voices.map { VoiceClips.assetPath(it.id).removePrefix("${VoiceClips.DIR}/") }.toSet(), sums.keys)
        for (voice in ModelCatalog.voices) {
            val clip = File(dir, "${voice.id}.ogg")
            val bytes = clip.readBytes()
            assertTrue("${voice.id}: ${bytes.size} bytes", bytes.size in 4_000 until 100_000)
            assertEquals("OggS", String(bytes, 0, 4, Charsets.ISO_8859_1))
            assertTrue(String(bytes, 0, 64, Charsets.ISO_8859_1).contains("OpusHead"))
            val sha = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
            assertEquals(voice.id, sums.getValue(clip.name), sha)
        }
        assertEquals(ModelCatalog.voices.map { it.id }.toSet(), VoiceClips.voicesIn(dir.list()))
    }

    @Test fun whatTheButtonPlays() {
        assertEquals(SampleSource.VOICE, SampleSource.of(installed = true, hasClip = true))
        assertEquals(SampleSource.VOICE, SampleSource.of(installed = true, hasClip = false))
        assertEquals(SampleSource.CLIP, SampleSource.of(installed = false, hasClip = true))
        assertNull(SampleSource.of(installed = false, hasClip = false))
        assertEquals(setOf("a"), VoiceClips.voicesIn(arrayOf("a.ogg", "SHA256SUMS")))
        assertEquals(emptySet<String>(), VoiceClips.voicesIn(null))
    }

    @Test fun theCardsButtonFollowsTheVoiceInUse() {
        val kristin = ModelCatalog.kristin.id
        val clips = ModelCatalog.voices.map { it.id }.toSet()
        // Not downloaded: its clip (never Android's own voice).
        assertEquals(SampleSource.CLIP, PhoneVoiceUi(effective = kristin, clips = clips).effectiveSample)
        // Downloaded: the voice itself.
        assertEquals(SampleSource.VOICE, PhoneVoiceUi(effective = kristin, installed = setOf(kristin), clips = clips).effectiveSample)
        // Neither (an app built without the clips): no button.
        assertNull(PhoneVoiceUi(effective = kristin).effectiveSample)
    }
}
