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

    @Test fun withGpuVisionTheCameraAndGallerySitBesideTheMic() {
        val (_, labels) = show(ui())
        val camera = labels.indexOf(CAMERA_LABEL)
        val gallery = labels.indexOf(GALLERY_LABEL)
        val mic = labels.indexOf("talk")
        assertTrue(labels.toString(), camera >= 0 && gallery == camera + 1 && mic == gallery + 1)
    }

    @Test fun withoutGpuVisionThereAreNoPhotoButtons() {
        for (v in listOf(VisionProbe.FAILED, VisionProbe.UNKNOWN)) {
            val (_, labels) = show(ui(vision = v))
            assertFalse("$v: $labels", CAMERA_LABEL in labels || GALLERY_LABEL in labels)
            assertTrue("the mic stays", "talk" in labels)
        }
        val (_, plain) = show(ui(takes = false))
        assertFalse(plain.toString(), CAMERA_LABEL in plain)
        // A place that takes no photos at all (no attachPhoto): none either.
        val (_, none) = show(ui(), AssistantActions())
        assertFalse(none.toString(), CAMERA_LABEL in none)
    }

    @Test fun notWhileAReplyIsWrittenOrAVoiceTurnRuns() {
        assertFalse(CAMERA_LABEL in show(ui(replying = true)).second)
        assertFalse(CAMERA_LABEL in show(ui(voicePhase = VoicePhase.Listening)).second)
    }

    @Test fun aWaitingPhotoShowsItselfAndSendsWithNothingTyped() {
        val p = PendingPhoto(ByteArray(10), 1024, 768, "camera", 40)
        val (shown, labels) = show(ui(pending = p))
        assertTrue(shown.toString(), pendingPhotoLine(p) in shown)
        assertEquals("photo · 1024 px · stays on this phone", pendingPhotoLine(p))
        assertFalse("one per question", CAMERA_LABEL in labels || GALLERY_LABEL in labels)
        assertTrue(labels.toString(), "send" in labels && "remove the photo" in labels)
        assertFalse("send, not the mic", "talk" in labels)
    }

    @Test fun theCameraReasonSaysWhereThePhotoGoes() {
        assertTrue(CAMERA_REASON.contains("stays on this phone"))
        assertTrue(CAMERA_REASON.contains("isn't saved to your gallery"))
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
