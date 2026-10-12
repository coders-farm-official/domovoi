package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest

/**
 * The speech library the app ships, android/native/sherpa-onnx: built from
 * source by build.sh in a pinned container, with text to speech left out.
 * Guards that the committed files are the ones SHA256SUMS records (a
 * replaced library fails here until build.sh --install records it), that
 * none of espeak-ng (GPL-3.0) or piper-phonemize is in them, and that they
 * load on phones with 16 KB memory pages.
 */
class NativeSpeechLibraryTest {

    private val dir: File = listOf(File("../native/sherpa-onnx"), File("android/native/sherpa-onnx"), File("native/sherpa-onnx"))
        .firstOrNull { File(it, "SHA256SUMS").isFile }
        ?: error("android/native/sherpa-onnx not found from ${File(".").absolutePath}")

    private val sums: Map<String, String> = File(dir, "SHA256SUMS").readLines().filter { it.isNotBlank() }
        .associate { line -> line.substringAfter("  ") to line.substringBefore("  ") }

    private val libraries = listOf("arm64-v8a", "x86_64").map { File(dir, "jniLibs/$it/libsherpa-onnx-jni.so") }

    @Test fun everyCommittedFileMatchesItsRecordedHash() {
        assertTrue(sums.keys.containsAll(libraries.map { it.relativeTo(dir).invariantSeparatorsPath }))
        assertTrue(sums.keys.any { it.startsWith("kotlin/") })
        sums.forEach { (path, sha) ->
            val file = File(dir, path)
            assertTrue("missing $path", file.isFile)
            assertEquals(path, sha, sha256(file.readBytes()))
        }
    }

    /** Strings from espeak-ng's own code and data files, piper-phonemize and
     *  sherpa-onnx's TTS JNI; the release AAR's library holds 95 of them. */
    @Test fun noTextToSpeechCodeIsInTheLibraries() {
        val markers = listOf(
            "espeak-ng", "eSpeak-ng", "ESPEAK_DATA_PATH", "espeakXXXXXX", "phontab", "phonindex", "phondata",
            "piper-phonemize", "PiperPhonemes", "OfflineTts",
        )
        libraries.forEach { so ->
            val text = String(so.readBytes(), Charsets.ISO_8859_1)
            assertEquals(so.path, emptyList<String>(), markers.filter { text.contains(it) })
        }
    }

    @Test fun theLibrariesAre16KbAlignedAndNeedOnlyOnnxRuntime() {
        libraries.forEach { so ->
            val elf = ByteBuffer.wrap(so.readBytes()).order(ByteOrder.LITTLE_ENDIAN)
            assertEquals(0x464C457F, elf.getInt(0))   // \x7fELF
            assertEquals(2, elf.get(4).toInt())        // 64-bit
            val phoff = elf.getLong(0x20).toInt()
            val phentsize = elf.getShort(0x36).toInt()
            val phnum = elf.getShort(0x38).toInt()
            val loads = (0 until phnum).map { phoff + it * phentsize }.filter { elf.getInt(it) == 1 }   // PT_LOAD
            assertTrue(loads.isNotEmpty())
            loads.forEach { assertTrue("${so.path}: PT_LOAD align ${elf.getLong(it + 48)}", elf.getLong(it + 48) >= 16_384) }
            val text = String(so.readBytes(), Charsets.ISO_8859_1)
            assertTrue(text.contains("libonnxruntime.so"))
        }
    }

    private fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
}
