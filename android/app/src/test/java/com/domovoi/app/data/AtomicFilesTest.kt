package com.domovoi.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.FileInputStream
import java.nio.file.Files
import java.nio.file.NoSuchFileException

/** The stores' one-step replace survives a reader holding the file open
 *  (Windows refuses the rename until the read ends: the JVM tests' lost
 *  "saved" flake); other errors still throw. */
class AtomicFilesTest {
    private val dir = Files.createTempDirectory("atomic").toFile()

    @Test fun aReaderHoldingTheFileOnlyDelaysTheReplace() {
        val target = dir.resolve("c.json").apply { writeText("old") }
        val tmp = dir.resolve("c.json.tmp").apply { writeText("new") }
        val reader = FileInputStream(target)
        val release = Thread {
            Thread.sleep(150)
            reader.close()
        }.apply { start() }
        AtomicFiles.replace(tmp, target)
        release.join()
        assertEquals("new", target.readText())
        dir.deleteRecursively()
    }

    @Test fun otherErrorsStillThrow() {
        assertThrows(NoSuchFileException::class.java) {
            AtomicFiles.replace(dir.resolve("missing.tmp"), dir.resolve("x.json"))
        }
        dir.deleteRecursively()
    }
}
