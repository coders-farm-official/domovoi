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
 * none of espeak-ng (GPL-3.0) or piper-phonemize is in them, that each is
 * built for its ABI, and that the 64-bit ones load on phones with 16 KB
 * memory pages. All four ABIs the app builds for (app/build.gradle.kts):
 * the 32-bit APKs have no on-device assistant but keep this library.
 */
class NativeSpeechLibraryTest {

    private val dir: File = listOf(File("../native/sherpa-onnx"), File("android/native/sherpa-onnx"), File("native/sherpa-onnx"))
        .firstOrNull { File(it, "SHA256SUMS").isFile }
        ?: error("android/native/sherpa-onnx not found from ${File(".").absolutePath}")

    private val sums: Map<String, String> = File(dir, "SHA256SUMS").readLines().filter { it.isNotBlank() }
        .associate { line -> line.substringAfter("  ") to line.substringBefore("  ") }

    /** ABI -> ELF class (1 = 32-bit, 2 = 64-bit) and machine. */
    private val abis = mapOf(
        "arm64-v8a" to (2 to 183),    // EM_AARCH64
        "x86_64" to (2 to 62),        // EM_X86_64
        "armeabi-v7a" to (1 to 40),   // EM_ARM
        "x86" to (1 to 3),            // EM_386
    )

    private val libraries = abis.keys.map { File(dir, "jniLibs/$it/libsherpa-onnx-jni.so") }

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

    /** Each library is built for its ABI; the 64-bit ones are 16 KB aligned
     *  (Android 15+ phones with 16 KB pages load nothing less; a 32-bit
     *  process always has 4 KB pages); all need only ONNX Runtime. */
    @Test fun eachLibraryIsForItsAbiAnd64BitOnesAre16KbAligned() {
        abis.forEach { (abi, want) ->
            val (elfClass, machine) = want
            val so = File(dir, "jniLibs/$abi/libsherpa-onnx-jni.so")
            val elf = ByteBuffer.wrap(so.readBytes()).order(ByteOrder.LITTLE_ENDIAN)
            assertEquals(0x464C457F, elf.getInt(0))   // \x7fELF
            assertEquals(abi, elfClass, elf.get(4).toInt())
            assertEquals(abi, machine, elf.getShort(0x12).toInt())
            if (elfClass == 2) {
                val phoff = elf.getLong(0x20).toInt()
                val phentsize = elf.getShort(0x36).toInt()
                val phnum = elf.getShort(0x38).toInt()
                val loads = (0 until phnum).map { phoff + it * phentsize }.filter { elf.getInt(it) == 1 }   // PT_LOAD
                assertTrue(loads.isNotEmpty())
                loads.forEach { assertTrue("${so.path}: PT_LOAD align ${elf.getLong(it + 48)}", elf.getLong(it + 48) >= 16_384) }
            }
            val text = String(so.readBytes(), Charsets.ISO_8859_1)
            assertTrue(abi, text.contains("libonnxruntime.so"))
        }
    }

    private fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
}
