package com.domovoi.app.update

import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.textButton
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.unlabelledControls
import com.domovoi.app.testing.withHeadlessWindow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Home's update card and Settings → About & updates's line, composed for real on the
 * JVM ([HeadlessUi]): "get the update" only when the phone can act on it,
 * "dismiss" always, and every control labelled.
 */
class AppUpdateCardTest {
    private fun card(state: UpdateState, taps: NodeTree.() -> Unit = {}): Pair<List<String>, List<String>> {
        val heard = mutableListOf<String>()
        val tree = NodeTree()
        var shown = emptyList<String>()
        withHeadlessWindow {
            composeTest(tree) {
                val notice = noticeFor(state, onOpen = { heard += "open $it" }, onDismiss = { heard += "dismiss" })!!
                setContent { HeadlessUi { AppUpdateCard(notice) } }
                settle()
                shown = tree.texts()
                assertEquals(emptyList<String>(), tree.unlabelledControls())
                tree.taps()
            }
        }
        return shown to heard
    }

    private val link = "http://192.168.1.20:6370/join?abi=arm64-v8a&update=1"

    @Test fun anUpdateOnTheServerHasOneButtonToItsPage() {
        val (shown, heard) = card(UpdateState.Available("1.0.1", 26110101, "arm64-v8a", link)) {
            textButton("get the update").click()
            textButton("dismiss").click()
        }
        assertEquals(listOf("Domovoi 1.0.1 is available from your server.", "get the update", "dismiss"), shown)
        assertEquals(listOf("open $link", "dismiss"), heard)
    }

    @Test fun anUpdateNotOnTheServerHasNoButtonButDismiss() {
        val (shown, heard) = card(UpdateState.NotOnServer("1.0.1", 26110101)) { textButton("dismiss").click() }
        assertFalse(shown.toString(), "get the update" in shown)
        assertTrue(shown.first().contains("“Download the app to this server”"))
        assertEquals(listOf("dismiss"), heard)
    }

    @Test fun aboutShowsTheButtonOnlyWithALink() {
        fun about(line: String, link: String?, taps: NodeTree.() -> Unit = {}): Pair<List<String>, List<String>> {
            val heard = mutableListOf<String>()
            val tree = NodeTree()
            var shown = emptyList<String>()
            withHeadlessWindow {
                composeTest(tree) {
                    setContent { HeadlessUi { AboutAppUpdate(line, link) { heard += it } } }
                    settle()
                    shown = tree.texts()
                    tree.taps()
                }
            }
            return shown to heard
        }
        val debug = about(AppUpdate.aboutText(UpdateState.DebugBuild), null).first
        assertTrue(debug.toString(), debug.any { it.startsWith("Debug build") })
        assertFalse("Get the update" in debug)
        val (shown, heard) = about("Domovoi 1.0.1 is available from your server.", link) {
            textButton("Get the update").click()
        }
        assertTrue("Get the update" in shown)
        assertEquals(listOf(link), heard)
    }
}
