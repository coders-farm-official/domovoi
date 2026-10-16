package com.domovoi.app.roommode

import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import com.domovoi.app.ui.components.PREVIEW_MEANS
import org.junit.Assert.assertTrue
import org.junit.Test

/** Room mode is a preview (owner decision 2, 2026-10-16): the card that
 *  offers it and its explainer say so, as a label beside the choice. */
class RoomModePreviewTest {

    @Test fun theEntryCardSaysPreviewBesideTheChoice() {
        val tree = NodeTree()
        var shown = emptyList<String>()
        withHeadlessWindow {
            composeTest(tree) {
                setContent { HeadlessUi { RoomModeEntryCard() } }
                settle(untilQuiet = false, frames = 5)
                shown = tree.texts()
            }
        }
        assertTrue(shown.toString(), "preview" in shown && PREVIEW_MEANS in shown)
        assertTrue(shown.toString(), shown.count { it == "Use this device as a room" } == 2)
    }

    /** The explainer is an Activity; its source holds the label under the heading. */
    @Test fun theExplainerSaysPreviewToo() {
        val repo = java.io.File(System.getProperty("domovoi.repo.dir") ?: error("domovoi.repo.dir is not set"))
        val src = java.io.File(repo, "android/app/src/main/java/com/domovoi/app/roommode/RoomSetupActivity.kt").readText()
        val explainer = src.substringAfter("private fun Explainer(").substringBefore("Start room mode")
        assertTrue("the explainer shows PreviewLabel()", "PreviewLabel()" in explainer)
    }
}
