package com.domovoi.app.away

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest

/**
 * Away chat's Tor library, android/native/domovoi-tor (built from the
 * repository's native/domovoi-tor crate by its build-android.sh): the
 * committed files are the ones SHA256SUMS records, each is built for its
 * ABI with the four JNI calls ArtiTor makes, the 64-bit ones load on
 * 16 KB-page phones, and no build machine's path is in them.
 */
class NativeTorLibraryTest {
    private val dir: File = listOf(File("../native/domovoi-tor"), File("android/native/domovoi-tor"), File("native/domovoi-tor"))
        .firstOrNull { File(it, "SHA256SUMS").isFile } ?: error("android/native/domovoi-tor not found")

    private val sums = File(dir, "SHA256SUMS").readLines().filter { it.isNotBlank() }
        .associate { it.substringAfter("  ") to it.substringBefore("  ") }

    private val abis = mapOf("arm64-v8a" to (2 to 183), "x86_64" to (2 to 62), "armeabi-v7a" to (1 to 40), "x86" to (1 to 3))

    @Test fun everyAbiIsCommittedWithItsRecordedHash() {
        abis.keys.forEach { abi ->
            val path = "jniLibs/$abi/libdomovoi_tor.so"
            val f = File(dir, path)
            assertTrue("missing $path", f.isFile)
            assertEquals(path, sums[path], MessageDigest.getInstance("SHA-256").digest(f.readBytes()).joinToString("") { "%02x".format(it) })
        }
    }

    @Test fun eachIsForItsAbiWithTheJniCallsAndNoBuildPaths() {
        abis.forEach { (abi, want) ->
            val bytes = File(dir, "jniLibs/$abi/libdomovoi_tor.so").readBytes()
            val elf = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
            assertEquals(0x464C457F, elf.getInt(0))
            assertEquals(abi, want.first, elf.get(4).toInt())
            assertEquals(abi, want.second, elf.getShort(0x12).toInt())
            if (want.first == 2) {
                val phoff = elf.getLong(0x20).toInt()
                val loads = (0 until elf.getShort(0x38).toInt()).map { phoff + it * elf.getShort(0x36).toInt() }.filter { elf.getInt(it) == 1 }
                loads.forEach { assertTrue("$abi: PT_LOAD align ${elf.getLong(it + 48)}", elf.getLong(it + 48) >= 16_384) }
            }
            val text = String(bytes, Charsets.ISO_8859_1)
            listOf("nativeStart", "nativeOpen", "nativeStop", "nativeLastError").forEach {
                assertTrue("$abi: $it", text.contains("Java_com_domovoi_app_away_ArtiTor_$it"))
            }
            assertTrue(abi, !text.contains("/Users/") && !text.contains("/home/runner") && !text.contains(".cargo/registry"))
        }
    }
}
