package com.domovoi.app.ui.screens.settings

import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import com.domovoi.app.ui.components.webLinkOrNull
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Settings → About & updates's open-source licences: the link to docs/LICENSES.md,
 * the licences of what a Domovoi server downloads (owner decision 7,
 * 2026-10-16: hey_jarvis and the Llama model ship, stated in that table).
 */
class ModelLicencesLinkTest {

    /** An https page of the repo, as GitHub shows it on main: the file must
     *  be in the repo it points at, and say what the card says. */
    @Test fun theLinkIsTheRepoSLicencesPage() {
        assertEquals(MODEL_LICENCES_URL, webLinkOrNull(MODEL_LICENCES_URL))
        val prefix = "https://github.com/coders-farm-official/domovoi/blob/main/"
        assertTrue(MODEL_LICENCES_URL, MODEL_LICENCES_URL.startsWith(prefix))
        val repo = java.io.File(System.getProperty("domovoi.repo.dir") ?: error("domovoi.repo.dir is not set"))
        val file = java.io.File(repo, MODEL_LICENCES_URL.removePrefix(prefix))
        assertTrue("$file is missing", file.isFile)
        val text = file.readText()
        for (needle in listOf("hey_jarvis", "CC BY-NC-SA 4.0", "llama3.2:3b", "Llama 3.2 Community License", "Gemma 4")) {
            assertTrue("$file says nothing of $needle", needle in text)
        }
    }

    @Test fun theCardSaysWhyAndOffersThePage() {
        val tree = NodeTree()
        var shown = emptyList<String>()
        withHeadlessWindow {
            composeTest(tree) {
                setContent { HeadlessUi { ModelLicencesLink() } }
                settle(untilQuiet = false, frames = 5)
                shown = tree.texts()
            }
        }
        assertTrue(shown.toString(), "licences of what Domovoi downloads" in shown)
        assertTrue(shown.toString(), shown.any { "hey_jarvis" in it && "Llama 3.2 Community License" in it })
    }
}
