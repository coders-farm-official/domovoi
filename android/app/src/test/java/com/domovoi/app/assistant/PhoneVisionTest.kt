package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pictures on the phone (N3), the pure part: the GPU-vision probe's memory
 * (per model file, app build and backend; a try that never finished counts
 * as a failure), when the camera and gallery buttons show, and how a photo
 * question reads to the model later (the marker, never the photo).
 */
class PhoneVisionTest {
    private val model = ModelCatalog.gemma4E2b
    private val key = visionKey(model, "1.0.0 (42)", LlmBackend.CPU, LlmBackend.GPU)

    @Test fun theKeyNamesTheModelFileTheBuildAndBothBackends() {
        assertTrue(key, key.startsWith("gemma-4-e2b-it@18193810"))
        assertNotEquals(key, visionKey(model, "1.0.0 (43)", LlmBackend.CPU, LlmBackend.GPU))
        assertNotEquals(key, visionKey(model, "1.0.0 (42)", LlmBackend.GPU, LlmBackend.GPU))
        assertNotEquals(key, visionKey(model, "1.0.0 (42)", LlmBackend.CPU, LlmBackend.CPU))
        val other = model.copy(files = listOf(model.files.single().copy(sha256 = "ab".repeat(32))))
        assertNotEquals(key, visionKey(other, "1.0.0 (42)", LlmBackend.CPU, LlmBackend.GPU))
    }

    @Test fun nothingKnownUntilTried() {
        val mem = MemoryVisionMemory()
        assertEquals(VisionProbe.UNKNOWN, VisionCheck(mem, key).state())
        VisionCheck(mem, key).passed()
        assertEquals(VisionProbe.PASSED, VisionCheck(mem, key).state())
        VisionCheck(mem, key).failed()
        assertEquals(VisionProbe.FAILED, VisionCheck(mem, key).state())
    }

    @Test fun anAnswerForAnotherBuildIsNoAnswer() {
        val mem = MemoryVisionMemory()
        VisionCheck(mem, key).failed()
        val next = VisionCheck(mem, visionKey(model, "1.0.1 (43)", LlmBackend.CPU, LlmBackend.GPU))
        assertEquals("a new build tries again", VisionProbe.UNKNOWN, next.state())
    }

    @Test fun aRealCrashWhileTheEncoderRanTurnsPhotosOff() {
        val mem = MemoryVisionMemory()
        val check = VisionCheck(mem, key)
        check.begin(now = 1_000)
        assertTrue(check.running())
        // The next start: Android says the run ended in a native crash.
        var askedSince = 0L
        val v = VisionCheck(mem, key).resolveAtStart { since -> askedSince = since; ExitKind.CRASH }
        assertEquals(1_000L, askedSince)
        assertEquals(VisionProbe.CRASHED, v)
        assertEquals("crashed#$key", mem.value)
        assertNull("the guard is settled", mem.guard)
        assertFalse(photoButtonsShown(true, VisionProbe.CRASHED, true, false, true, false))
    }

    @Test fun aCrashAfterPhotosPassedStillTurnsThemOff() {
        val mem = MemoryVisionMemory()
        VisionCheck(mem, key).passed()
        VisionCheck(mem, key).begin(now = 5)          // a photo turn
        assertEquals(VisionProbe.CRASHED, VisionCheck(mem, key).resolveAtStart { ExitKind.CRASH })
    }

    @Test fun aMemoryKillOrASwipeAwayIsNoVerdict() {
        val mem = MemoryVisionMemory()
        VisionCheck(mem, key).begin(now = 7)
        assertEquals(VisionProbe.UNKNOWN, VisionCheck(mem, key).resolveAtStart { ExitKind.NOT_A_CRASH })
        assertFalse("tried again later", VisionCheck(mem, key).running())
        assertNull(mem.value)
        // And a passed answer stays passed.
        VisionCheck(mem, key).passed()
        VisionCheck(mem, key).begin(now = 8)
        assertEquals(VisionProbe.PASSED, VisionCheck(mem, key).resolveAtStart { ExitKind.NOT_A_CRASH })
    }

    @Test fun whereAndroidCantSayTheSecondUnexplainedDeathCounts() {
        val mem = MemoryVisionMemory()
        VisionCheck(mem, key).begin(now = 1)
        assertEquals("first: forgiven", VisionProbe.UNKNOWN, VisionCheck(mem, key).resolveAtStart { ExitKind.UNKNOWN })
        VisionCheck(mem, key).begin(now = 2)
        assertEquals("second: a crash", VisionProbe.CRASHED, VisionCheck(mem, key).resolveAtStart { ExitKind.UNKNOWN })
    }

    @Test fun aRunThatEndsWellClearsTheStrikes() {
        val mem = MemoryVisionMemory()
        VisionCheck(mem, key).begin(now = 1)
        VisionCheck(mem, key).resolveAtStart { ExitKind.UNKNOWN }
        VisionCheck(mem, key).begin(now = 2)
        VisionCheck(mem, key).end()
        VisionCheck(mem, key).begin(now = 3)
        assertEquals(VisionProbe.UNKNOWN, VisionCheck(mem, key).resolveAtStart { ExitKind.UNKNOWN })
    }

    @Test fun noMarkNoQuestion() {
        val mem = MemoryVisionMemory()
        VisionCheck(mem, key).passed()
        assertEquals(VisionProbe.PASSED, VisionCheck(mem, key).resolveAtStart { error("not asked") })
        // A mark from another build is dropped, not judged.
        mem.guard = "other-key#9#0"
        assertEquals(VisionProbe.PASSED, VisionCheck(mem, key).resolveAtStart { error("not asked") })
        assertNull(mem.guard)
    }

    @Test fun androidsExitReasonsAreReadAsTheGuardNeeds() {
        assertEquals(ExitKind.CRASH, exitKind(5))    // REASON_CRASH_NATIVE
        assertEquals(ExitKind.CRASH, exitKind(4))    // REASON_CRASH
        assertEquals(ExitKind.CRASH, exitKind(6))    // REASON_ANR
        assertEquals(ExitKind.NOT_A_CRASH, exitKind(3))   // REASON_LOW_MEMORY
        assertEquals(ExitKind.NOT_A_CRASH, exitKind(10))  // REASON_USER_REQUESTED
        assertEquals(ExitKind.NOT_A_CRASH, exitKind(11))  // REASON_USER_STOPPED
        assertEquals(ExitKind.UNKNOWN, exitKind(0))       // REASON_UNKNOWN
    }


    @Test fun theButtonsShowOnlyWithAPassedProbeAndNothingElseGoingOn() {
        fun shown(
            takes: Boolean = true, vision: VisionProbe = VisionProbe.PASSED, installed: Boolean = true,
            replying: Boolean = false, idle: Boolean = true, waiting: Boolean = false,
        ) = photoButtonsShown(takes, vision, installed, replying, idle, waiting)
        assertTrue(shown())
        assertFalse("a text-only model", shown(takes = false))
        assertFalse("not probed yet", shown(vision = VisionProbe.UNKNOWN))
        assertFalse("no GPU vision (the emulator; CPU fails the gate)", shown(vision = VisionProbe.FAILED))
        assertFalse(shown(installed = false))
        assertFalse(shown(replying = true))
        assertFalse("during a voice turn", shown(idle = false))
        assertFalse("one photo per question", shown(waiting = true))
    }

    @Test fun theBenchFixesTheNumbers() {
        assertEquals(280, PhonePhotos.IMAGE_TOKENS)
        assertEquals(1024, PhonePhotos.LONG_EDGE)
        assertEquals(90, PhonePhotos.JPEG_QUALITY)
        assertEquals(1, PhonePhotos.MAX_PER_QUESTION)
        assertTrue(ModelCatalog.gemma4E2b.takesImages)
        assertFalse(ModelCatalog.moonshineTinyEn.takesImages)
    }

    @Test fun aPhotoSentWithNothingTypedIsAskedWhatsInIt() {
        assertEquals(PhonePhotos.DEFAULT_QUESTION, PhonePhotos.question("  "))
        assertEquals("Is this plant healthy?", PhonePhotos.question(" Is this plant healthy? "))
        assertTrue(PhonePhotos.systemPrompt("BASE").startsWith("BASE\n\n"))
    }

    @Test fun laterTurnsSeeTheMarkerNotThePhoto() {
        val messages = listOf(
            StoredMessage(Speaker.USER, "What plant is this?", photo = true),
            StoredMessage(Speaker.DOMOVOI, "A snake plant."),
            StoredMessage(Speaker.USER, "", photo = true),
            StoredMessage(Speaker.DOMOVOI, "A stop sign."),
            StoredMessage(Speaker.USER, "How often do I water it?"),
        )
        val turns = modelHistory(messages)
        assertEquals(
            listOf(
                ChatTurn(true, "${PhonePhotos.HISTORY_MARKER} What plant is this?"),
                ChatTurn(false, "A snake plant."),
                ChatTurn(true, PhonePhotos.HISTORY_MARKER),
                ChatTurn(false, "A stop sign."),
                ChatTurn(true, "How often do I water it?"),
            ),
            turns,
        )
    }

    @Test fun aPhotoQuestionCantBeTriedAgain() {
        val photoTurn = StoredConversation(
            "c", messages = listOf(StoredMessage(Speaker.USER, "What is it?", photo = true), StoredMessage(Speaker.DOMOVOI, "A cat.")),
        )
        assertNull("its photo is gone", retryBase(photoTurn))
        val textTurn = photoTurn.copy(messages = listOf(StoredMessage(Speaker.USER, "What is it?"), StoredMessage(Speaker.DOMOVOI, "A cat.")))
        assertEquals(1, retryBase(textTurn)?.messages?.size)
    }

    @Test fun theExportKeepsTheMarkOnly() {
        val c = StoredConversation(
            "c", created = 1, updated = 1,
            messages = listOf(StoredMessage(Speaker.USER, "What is it?", photo = true), StoredMessage(Speaker.DOMOVOI, "A cat.")),
        )
        val text = ChatExport.text(c, java.time.ZoneOffset.UTC)
        assertTrue(text, text.contains("You: [photo] What is it?"))
    }

    @Test fun aReportKeepsThePhotoMark() {
        val c = StoredConversation(
            "c", messages = listOf(StoredMessage(Speaker.USER, "What is it?", photo = true), StoredMessage(Speaker.DOMOVOI, "A cat.")),
        )
        assertEquals("(a photo, not kept) What is it?", ReplyReport.draft(c, 1, model, "1.0", 1)!!.question)
        val blank = c.copy(messages = listOf(StoredMessage(Speaker.USER, "", photo = true), StoredMessage(Speaker.DOMOVOI, "A cat.")))
        assertEquals("(a photo, not kept)", ReplyReport.draft(blank, 1, model, "1.0", 1)!!.question)
        val text = c.copy(messages = listOf(StoredMessage(Speaker.USER, "What is it?"), StoredMessage(Speaker.DOMOVOI, "A cat.")))
        assertEquals("What is it?", ReplyReport.draft(text, 1, model, "1.0", 1)!!.question)
    }

    @Test fun twoPhotosAreNeverEqualByTheirBytes() {
        val a = PendingPhoto(byteArrayOf(1, 2), 10, 10, "gallery", 5)
        val b = PendingPhoto(byteArrayOf(1, 2), 10, 10, "gallery", 5)
        assertNotEquals(a, b)
        assertEquals(a, a)
        assertFalse("its bytes stay out of logs", a.toString().contains("[1, 2]"))
    }
}
