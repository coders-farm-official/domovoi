package com.domovoi.app.roommode

import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Room mode is no longer a preview (owner decision 2026-11-17): neither
 *  the card that offers it nor its explainer carries the label. */
class RoomModeNoPreviewTest {

    @Test fun theEntryCardHasNoPreviewLabel() {
        val tree = NodeTree()
        var shown = emptyList<String>()
        withHeadlessWindow {
            composeTest(tree) {
                setContent { HeadlessUi { RoomModeEntryCard() } }
                settle(untilQuiet = false, frames = 5)
                shown = tree.texts()
            }
        }
        assertFalse(shown.toString(), "preview" in shown)
        assertTrue(shown.toString(), shown.count { it == "Use this device as a room" } == 2)
    }

    /** The explainer is an Activity; its source has no preview pill under the heading. */
    @Test fun theExplainerHasNoPreviewLabel() {
        val repo = java.io.File(System.getProperty("domovoi.repo.dir") ?: error("domovoi.repo.dir is not set"))
        val src = java.io.File(repo, "android/app/src/main/java/com/domovoi/app/roommode/RoomSetupActivity.kt").readText()
        val explainer = src.substringAfter("private fun Explainer(").substringBefore("Start room mode")
        assertFalse("the explainer shows no preview label", "PreviewLabel" in explainer || "\"preview\"" in explainer)
    }
}
