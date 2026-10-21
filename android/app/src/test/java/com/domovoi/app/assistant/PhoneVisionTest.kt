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

    @Test fun aTryTheLastRunNeverFinishedIsAFailureAndIsWrittenSo() {
        val mem = MemoryVisionMemory()
        VisionCheck(mem, key).trying()
        // During the run, the try is under way: not known yet, nothing written.
        assertEquals(VisionProbe.UNKNOWN, VisionCheck(mem, key).stateNow())
        assertTrue(VisionCheck(mem, key).unfinished())
        // The next start: the process died in the driver.
        assertEquals(VisionProbe.FAILED, VisionCheck(mem, key).state())
        assertEquals("failed#$key", mem.value)
        assertFalse(VisionCheck(mem, key).unfinished())
    }

    @Test fun forgettingLeavesNothing() {
        val mem = MemoryVisionMemory()
        VisionCheck(mem, key).trying()
        VisionCheck(mem, key).forget()
        assertNull(mem.value)
        assertEquals(VisionProbe.UNKNOWN, VisionCheck(mem, key).state())
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

    @Test fun twoPhotosAreNeverEqualByTheirBytes() {
        val a = PendingPhoto(byteArrayOf(1, 2), 10, 10, "gallery", 5)
        val b = PendingPhoto(byteArrayOf(1, 2), 10, 10, "gallery", 5)
        assertNotEquals(a, b)
        assertEquals(a, a)
        assertFalse("its bytes stay out of logs", a.toString().contains("[1, 2]"))
    }
}
