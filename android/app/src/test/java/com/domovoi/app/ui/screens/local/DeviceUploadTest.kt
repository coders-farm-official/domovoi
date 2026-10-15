package com.domovoi.app.ui.screens.local

import com.domovoi.app.net.ApiException
import com.domovoi.app.net.UploadResult
import com.domovoi.app.ui.screens.files.FileLibrary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.FileNotFoundException
import java.io.IOException

/**
 * Where a "Your device" Upload goes, and when it is refused (DeviceUpload.kt):
 * music to the server's Music library, a video to Videos (Documents on a
 * server without one, as send-home does), anything else through the
 * send-home sheet — and a blocked device or a read-only library refused up
 * front, in the server's own words where it gave any.
 */
class DeviceUploadTest {

    private fun lib(id: String, label: String = id.substringAfter(':').replaceFirstChar { it.uppercase() }, editable: Boolean = true) =
        FileLibrary(id = id, label = label, editable = editable)

    private val full = listOf(
        lib("core:music"), lib("core:audiobooks"), lib("core:documents"),
        lib("core:pictures"), lib("core:videos"), lib("removable:sdb1", "USB stick", editable = false),
    )

    private fun ok(libs: List<FileLibrary>?) = ServerWriteStatus(libs)

    // ── where it goes ───────────────────────────────────────────────────

    @Test fun musicGoesToTheMusicLibraryThroughItsOwnDoor() {
        assertEquals(UploadTarget.Direct(UploadDest.MusicLibrary), uploadTarget(UploadKind.Music, ok(full)))
    }

    @Test fun aVideoGoesToVideos() {
        assertEquals(
            UploadTarget.Direct(UploadDest.Library("core:videos", "Videos")),
            uploadTarget(UploadKind.Video, ok(full)),
        )
    }

    @Test fun aVideoGoesToDocumentsOnAServerWithoutVideos() {
        val older = full.filter { it.id != "core:videos" }
        assertEquals(
            UploadTarget.Direct(UploadDest.Library("core:documents", "Documents")),
            uploadTarget(UploadKind.Video, ok(older)),
        )
        val videosReadOnly = full.map { if (it.id == "core:videos") it.copy(editable = false) else it }
        assertEquals(
            "a read-only Videos library is passed over too",
            UploadTarget.Direct(UploadDest.Library("core:documents", "Documents")),
            uploadTarget(UploadKind.Video, ok(videosReadOnly)),
        )
    }

    @Test fun anyOtherFileAsksWhereThroughTheSendHomeSheet() {
        assertEquals(UploadTarget.Pick, uploadTarget(UploadKind.File, ok(full)))
        assertEquals("the sheet reads the libraries itself", UploadTarget.Pick, uploadTarget(UploadKind.File, ok(null)))
    }

    @Test fun nothingIsDecidedBeforeTheServerAnswers() {
        UploadKind.entries.forEach { assertNull(uploadTarget(it, null)) }
    }

    // ── refused ─────────────────────────────────────────────────────────

    @Test fun aBlockedDeviceIsRefusedForEveryKindInTheServersWords() {
        val blocked = ServerWriteStatus(full, writable = false, blockedReason = "Pixel 7 isn't allowed to change files (kids' tablet)")
        UploadKind.entries.forEach { kind ->
            val t = uploadTarget(kind, blocked)
            assertEquals(UploadTarget.Refused("Pixel 7 isn't allowed to change files (kids' tablet)", blocked = true), t)
        }
        val unnamed = uploadTarget(UploadKind.Music, ServerWriteStatus(full, writable = false))
        assertEquals(UploadTarget.Refused("this device isn't allowed to change files", blocked = true), unnamed)
    }

    @Test fun aReadOnlyMusicLibraryIsRefused() {
        val ro = full.map { if (it.id == "core:music") it.copy(editable = false) else it }
        val t = uploadTarget(UploadKind.Music, ok(ro)) as UploadTarget.Refused
        assertTrue(t.reason, t.reason.contains("read-only"))
        assertEquals(false, t.blocked)
        val none = uploadTarget(UploadKind.Music, ok(full.filter { it.id != "core:music" })) as UploadTarget.Refused
        assertTrue(none.reason, none.reason.contains("no Music library"))
    }

    @Test fun everyLibraryReadOnlyRefusesVideosAndFiles() {
        val ro = full.map { it.copy(editable = false) }
        listOf(UploadKind.Video, UploadKind.File).forEach { kind ->
            val t = uploadTarget(kind, ok(ro)) as UploadTarget.Refused
            assertTrue(t.reason, t.reason.contains("read-only"))
        }
    }

    @Test fun unreadableLibrariesLetMusicThroughButNotAVideo() {
        // The music door applies the server's rules itself; a video needs
        // the list to know where Videos is.
        assertEquals(UploadTarget.Direct(UploadDest.MusicLibrary), uploadTarget(UploadKind.Music, ok(null)))
        val v = uploadTarget(UploadKind.Video, ServerWriteStatus(null, error = "404 Not Found")) as UploadTarget.Refused
        assertTrue(v.reason, v.reason.contains("couldn't read the server's libraries") && v.reason.contains("404"))
    }

    @Test fun theWriteAccessProbeAsksAboutTheLibraryTheUploadWouldUse() {
        assertEquals("core:music", probeLibrary(UploadKind.Music, full))
        assertEquals("core:videos", probeLibrary(UploadKind.Video, full))
        assertEquals("core:documents", probeLibrary(UploadKind.File, full))
        assertNull(probeLibrary(UploadKind.Music, null))
        assertNull(probeLibrary(UploadKind.Music, full.filter { it.id != "core:music" }))
    }

    // ── the button ──────────────────────────────────────────────────────

    @Test fun theButtonFollowsTheTargetAndItsOwnUpload() {
        val music = UploadTarget.Direct(UploadDest.MusicLibrary)
        assertEquals(UploadButtonState.Ready, uploadButtonState(music, inFlight = false, percent = null))
        assertEquals(UploadButtonState.Ready, uploadButtonState(UploadTarget.Pick, inFlight = false, percent = null))
        assertEquals(UploadButtonState.Checking, uploadButtonState(null, inFlight = false, percent = null))
        assertEquals(
            UploadButtonState.Refused("nope"),
            uploadButtonState(UploadTarget.Refused("nope"), inFlight = false, percent = null),
        )
        assertEquals(UploadButtonState.Busy(37), uploadButtonState(music, inFlight = true, percent = 37))
        assertEquals(UploadButtonState.Busy(null), uploadButtonState(music, inFlight = true, percent = null))
        assertEquals(
            "a running upload keeps its progress even if a block lands meanwhile",
            UploadButtonState.Busy(80),
            uploadButtonState(UploadTarget.Refused("blocked", true), inFlight = true, percent = 80),
        )
    }

    @Test fun eachRowHasItsOwnKey() {
        assertEquals("track:7", trackUploadKey(7))
        assertEquals("video:7", videoUploadKey(7))
        assertEquals("file:phone:folder:x/a/b.pdf", fileUploadKey("phone:folder:x", "a/b.pdf"))
        assertTrue(trackUploadKey(7) != videoUploadKey(7))
    }

    // ── the toasts ──────────────────────────────────────────────────────

    @Test fun aRefusalIsSaidPlainlyInTheServersWords() {
        val blocked = ApiException(
            403, "403 Forbidden: {\"detail\":\"Pixel 7 isn't allowed to change files\"}",
            body = "{\"detail\":\"Pixel 7 isn't allowed to change files\"}",
        )
        assertEquals("upload refused: Pixel 7 isn't allowed to change files", uploadFailureText("song", blocked))
        val readOnly = ApiException(403, "403 Forbidden", body = "{\"detail\":\"library is not editable\"}")
        assertEquals("upload refused: library is not editable", uploadFailureText("a.pdf", readOnly))
        assertEquals("upload refused: this device may not upload here", uploadFailureText("x", ApiException(403, "403 Forbidden")))
    }

    @Test fun otherFailuresSayWhatHappened() {
        val bad = ApiException(400, "400 Bad Request", body = "{\"detail\":\"no supported audio files in upload; a.xyz: unsupported type\"}")
        assertEquals("upload failed: no supported audio files in upload; a.xyz: unsupported type", uploadFailureText("a", bad))
        assertEquals("upload failed (offline?)", uploadFailureText("a", IOException("timeout")))
        assertEquals(
            "can't read \"clip.mp4\" on this phone",
            uploadFailureText("clip.mp4", FileNotFoundException("can't read \"clip.mp4\" on this phone")),
        )
        val validation = ApiException(422, "422 Unprocessable Entity: [..]", body = "{\"detail\":[{\"loc\":[\"body\"]}]}")
        assertTrue(uploadFailureText("a", validation).startsWith("upload failed: 422"))
    }

    @Test fun aFinishedUploadSaysWhereItWent() {
        assertEquals(
            "uploaded \"Hearth\" to Music · indexing…",
            uploadDoneText("Hearth", UploadDest.MusicLibrary, UploadResult(1, 0, true)),
        )
        assertEquals(
            "uploaded \"clip.mp4\" to Videos",
            uploadDoneText("clip.mp4", UploadDest.Library("core:videos", "Videos"), UploadResult(1, 0, false)),
        )
        assertEquals(
            "uploaded \"a.pdf\" to Documents / letters/2026 · 1 skipped",
            uploadDoneText("a.pdf", UploadDest.Library("core:documents", "Documents", "letters/2026"), UploadResult(1, 1, false)),
        )
        assertEquals("nothing was uploaded", uploadDoneText("a", UploadDest.MusicLibrary, UploadResult(0, 0, false)))
    }
}
