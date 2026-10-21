package com.domovoi.app.ui.screens.assistant

import androidx.activity.compose.LocalActivityResultRegistryOwner
import androidx.activity.result.ActivityResultRegistry
import androidx.activity.result.ActivityResultRegistryOwner
import androidx.activity.result.contract.ActivityResultContract
import androidx.core.app.ActivityOptionsCompat
import androidx.compose.material3.Text
import androidx.compose.runtime.CompositionLocalProvider
import com.domovoi.app.assistant.AssistantUi
import com.domovoi.app.assistant.DeviceTiers
import com.domovoi.app.assistant.EngineState
import com.domovoi.app.assistant.LlmBackend
import com.domovoi.app.assistant.ModelSetup
import com.domovoi.app.assistant.PendingPhoto
import com.domovoi.app.assistant.PhoneMemory
import com.domovoi.app.assistant.StoredConversation
import com.domovoi.app.assistant.VisionProbe
import com.domovoi.app.assistant.VoicePhase
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.buttons
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import com.domovoi.app.ui.screens.chat.CAMERA_LABEL
import com.domovoi.app.ui.screens.chat.ADD_LABEL
import com.domovoi.app.ui.screens.chat.DOCUMENT_LABEL
import com.domovoi.app.ui.screens.chat.CAMERA_REFUSED
import com.domovoi.app.ui.screens.chat.CameraAsk
import com.domovoi.app.ui.screens.chat.addMenuItems
import com.domovoi.app.ui.screens.chat.cameraAsk
import com.domovoi.app.ui.screens.chat.sourceTooBig
import org.junit.Assert.assertNull
import com.domovoi.app.ui.screens.chat.CAMERA_REASON
import com.domovoi.app.ui.screens.chat.GALLERY_LABEL
import com.domovoi.app.ui.screens.chat.phonePhotoSize
import com.domovoi.app.ui.screens.chat.pickedTooBig
import com.domovoi.app.ui.screens.chat.pendingPhotoLine
import com.domovoi.app.ui.screens.chat.PhotoSize
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The phone chat's camera and gallery buttons (N3), composed on the JVM:
 * beside the mic only where the GPU probe passed (never on a phone without
 * GPU vision, such as the emulator), gone while a photo waits (one per
 * question, which sends with nothing typed), and the resize's sizes.
 */
class PhonePhotoUiTest {
    private val gib = 1L shl 30
    private val calls = mutableListOf<String>()

    private val actions = AssistantActions(
        talk = { calls += "talk" },
        send = { calls += "send:$it" },
        attachPhoto = { calls += "photo" },
        dropPhoto = { calls += "drop" },
    )

    private fun ui(
        vision: VisionProbe = VisionProbe.PASSED,
        takes: Boolean = true,
        pending: PendingPhoto? = null,
        replying: Boolean = false,
        voicePhase: VoicePhase = VoicePhase.Idle,
    ): AssistantUi {
        val memory = PhoneMemory((15.8 * gib).toLong(), lowRamDevice = false)
        return AssistantUi(
            memory = memory, tier = DeviceTiers.of(memory), setup = ModelSetup.Installed, freeBytes = 50L * gib,
            engine = EngineState.Ready(LlmBackend.CPU, 2_400), conversation = StoredConversation(id = "c-1-a"),
            voiceSetup = ModelSetup.Installed, modelTakesImages = takes, vision = vision, pendingPhoto = pending,
            replying = replying, voicePhase = voicePhase,
        )
    }

    /** Launches nothing: the tests only look. */
    private val registry = object : ActivityResultRegistryOwner {
        override val activityResultRegistry = object : ActivityResultRegistry() {
            override fun <I, O> onLaunch(requestCode: Int, contract: ActivityResultContract<I, O>, input: I, options: ActivityOptionsCompat?) {}
        }
    }

    private fun show(state: AssistantUi, a: AssistantActions = actions): Pair<List<String>, List<String?>> {
        val tree = NodeTree()
        var shown = emptyList<String>()
        var labels = emptyList<String?>()
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        CompositionLocalProvider(
                            LocalComposerField provides { value, _, _ -> Text("field: $value") },
                            LocalActivityResultRegistryOwner provides registry,
                        ) {
                            AssistantContent(state, a, PhoneChatChrome())
                        }
                    }
                }
                settle(untilQuiet = false, frames = 10)
                shown = tree.texts()
                labels = tree.buttons().map { it.label }
            }
        }
        return shown to labels
    }

    @Test fun theAddButtonSitsLeftAndTheMicAloneOnTheRight() {
        val (_, labels) = show(ui())
        val add = labels.indexOf(ADD_LABEL)
        assertTrue(labels.toString(), add >= 0 && labels.last() == "talk" && add < labels.indexOf("talk"))
        assertFalse("no separate photo buttons", CAMERA_LABEL in labels || GALLERY_LABEL in labels)
    }

    @Test fun theMenuListsPhotoItemsOnlyWhereTheProbePassed() {
        assertEquals(listOf(DOCUMENT_LABEL, CAMERA_LABEL, GALLERY_LABEL), addMenuItems(documents = true, photos = true, backCamera = true))
        assertEquals("no greyed-out photo items", listOf(DOCUMENT_LABEL), addMenuItems(documents = true, photos = false, backCamera = true))
        assertEquals("no back camera, no camera item", listOf(DOCUMENT_LABEL, GALLERY_LABEL), addMenuItems(true, true, false))
        assertEquals(listOf(CAMERA_LABEL, GALLERY_LABEL), addMenuItems(documents = false, photos = true, backCamera = true))
        assertEquals(emptyList<String>(), addMenuItems(false, false, true))
    }

    @Test fun withoutGpuVisionAndNoDocumentsThereIsNoAddButton() {
        for (v in listOf(VisionProbe.FAILED, VisionProbe.UNKNOWN, VisionProbe.CRASHED)) {
            val (_, labels) = show(ui(vision = v))
            assertFalse("$v: $labels", ADD_LABEL in labels)
            assertTrue("the mic stays", "talk" in labels)
        }
        val (_, none) = show(ui(), AssistantActions())
        assertFalse(none.toString(), ADD_LABEL in none)
    }

    @Test fun notWhileAReplyIsWrittenOrAVoiceTurnRuns() {
        assertFalse(ADD_LABEL in show(ui(replying = true)).second)
        assertFalse(ADD_LABEL in show(ui(voicePhase = VoicePhase.Listening)).second)
    }

    @Test fun aWaitingPhotoShowsItselfAndSendsWithNothingTyped() {
        val p = PendingPhoto(ByteArray(10), 1024, 768, "camera", 40)
        val (shown, labels) = show(ui(pending = p))
        assertTrue(shown.toString(), pendingPhotoLine(p) in shown)
        assertEquals("photo · 1024 px · stays on this phone", pendingPhotoLine(p))
        assertFalse("one per question: no photo items, so no +", ADD_LABEL in labels)
        assertTrue(labels.toString(), "send" in labels && "remove the photo" in labels)
        assertFalse("send, not the mic", "talk" in labels)
    }

    @Test fun afterDontAskAgainTheCameraGoesStraightToTheSettingsNote() {
        assertNull("granted: the camera opens", cameraAsk(granted = true, askedBefore = true, canShowRationale = false))
        assertEquals(CameraAsk.Reason, cameraAsk(granted = false, askedBefore = false, canShowRationale = false))
        assertEquals("refused once: Android asks again", CameraAsk.Reason, cameraAsk(false, true, true))
        assertEquals(CameraAsk.Refused, cameraAsk(granted = false, askedBefore = true, canShowRationale = false))
        assertTrue(CAMERA_REFUSED.contains("open the app's settings"))
    }

    @Test fun aHugeHeaderIsRefusedBeforeDecoding() {
        assertFalse(sourceTooBig(16_320, 12_240))   // 200 MP
        assertTrue(sourceTooBig(50_000, 50_000))
        assertFalse(sourceTooBig(0, 0))
    }

    @Test fun theCameraReasonSaysWhereThePhotoGoes() {
        assertTrue(CAMERA_REASON.contains("stays on this phone"))
        assertTrue(CAMERA_REASON.contains("isn't saved to your gallery") && CAMERA_REASON.contains("discarded from memory"))
    }

    @Test fun photosAreResizedTo1024OnTheLongEdge() {
        assertEquals(PhotoSize(1024, 768), phonePhotoSize(4032, 3024))
        assertEquals(PhotoSize(768, 1024), phonePhotoSize(3024, 4032))
        // A camera capture turned a quarter: width and height swap first.
        assertEquals(PhotoSize(768, 1024), phonePhotoSize(4032, 3024, rotation = 90))
        assertEquals(PhotoSize(1024, 768), phonePhotoSize(4032, 3024, rotation = 180))
        assertEquals(PhotoSize(1024, 768), phonePhotoSize(3024, 4032, rotation = 270))
        // Never enlarged.
        assertEquals(PhotoSize(800, 600), phonePhotoSize(800, 600))
        // A 50 MP panorama.
        assertEquals(PhotoSize(1024, 205), phonePhotoSize(16000, 3200))
    }

    @Test fun aPhotoThatCantBeUsedIsSaidWithoutTalkOfAnUpload() {
        val heic = com.domovoi.app.ui.screens.chat.phonePhotoProblem(
            com.domovoi.app.ui.screens.chat.PhotoUnreadable(com.domovoi.app.ui.screens.chat.PictureKind.HEIC), 27)
        assertTrue(heic, heic.contains("HEIC") && heic.contains("Android 9"))
        val tooBig = com.domovoi.app.ui.screens.chat.phonePhotoProblem(com.domovoi.app.ui.screens.chat.PhotoTooBig(), 35)
        assertEquals(com.domovoi.app.ui.screens.chat.PHOTO_TOO_BIG_TEXT, tooBig)
        val other = com.domovoi.app.ui.screens.chat.phonePhotoProblem(java.io.IOException("boom"), 35)
        assertFalse(other, other.contains("upload"))
    }

    @Test fun aHugePickedFileIsRefusedBeforeItIsRead() {
        assertFalse(pickedTooBig(null))
        assertFalse(pickedTooBig(12L * 1024 * 1024))
        assertTrue(pickedTooBig(65L * 1024 * 1024))
    }
}
