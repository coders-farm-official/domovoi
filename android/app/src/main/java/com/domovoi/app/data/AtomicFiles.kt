package com.domovoi.app.data

import java.io.File
import java.nio.file.AccessDeniedException
import java.nio.file.FileSystemException
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/**
 * Puts [tmp] in place of [target] in one step (a reader sees the old file or
 * the new one, never half of one).
 *
 * On Android (POSIX) the rename always succeeds, readers or not. On Windows,
 * where the JVM unit tests run, replacing a file another thread has open for
 * reading is refused (AccessDeniedException) until that read ends, and the
 * stores' callers only logged it, so the save was lost: the JVM tests'
 * "timed out waiting for: saved" flake (2026-10-21; the test polls the same
 * file while the controller saves it). A refused move is tried again
 * briefly; anything else, or a refusal that outlasts [attempts], is thrown
 * as before.
 */
object AtomicFiles {
    fun replace(tmp: File, target: File, attempts: Int = 50, pauseMs: Long = 10) {
        var tries = 0
        while (true) {
            try {
                Files.move(tmp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
                return
            } catch (e: FileSystemException) {
                // AccessDeniedException, or Windows' "being used by another
                // process": both are a reader holding the target open.
                val busy = e is AccessDeniedException || e.reason?.contains("used by another process", ignoreCase = true) == true
                if (!busy || ++tries >= attempts) throw e
                Thread.sleep(pauseMs)
            }
        }
    }
}
