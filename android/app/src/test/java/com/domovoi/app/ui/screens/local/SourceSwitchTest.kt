package com.domovoi.app.ui.screens.local

import androidx.activity.OnBackPressedDispatcher
import androidx.activity.compose.BackHandler
import androidx.activity.compose.LocalOnBackPressedDispatcherOwner
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.runtime.setValue
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.FocusManager
import androidx.compose.ui.platform.LocalFocusManager
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import com.domovoi.app.ui.shell.LocalServerChoice
import com.domovoi.app.ui.shell.ServerChoice
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The server / "your device" switch on the connected Files, Music and
 * Videos pages (YourDevice.kt): every page starts on the server, the choice
 * is kept per page for the session (and through a saved-state round trip),
 * and switching back finds the server's side as it was left — the same
 * composition, not a fresh one — with Back belonging to the side on screen.
 */
class SourceSwitchTest {

    // ── the remembered choice ───────────────────────────────────────────

    @Test fun everyPageStartsOnTheServer() {
        val choices = SourceChoices()
        DevicePage.entries.forEach { assertEquals(ContentSource.Server, choices.of(it)) }
    }

    @Test fun theChoiceIsPerPage() {
        val choices = SourceChoices()
        choices.choose(DevicePage.Music, ContentSource.Device)
        assertEquals(ContentSource.Device, choices.of(DevicePage.Music))
        assertEquals(ContentSource.Server, choices.of(DevicePage.Videos))
        assertEquals(ContentSource.Server, choices.of(DevicePage.Files))
        choices.choose(DevicePage.Files, ContentSource.Device)
        choices.choose(DevicePage.Music, ContentSource.Server)
        assertEquals(ContentSource.Server, choices.of(DevicePage.Music))
        assertEquals(ContentSource.Device, choices.of(DevicePage.Files))
    }

    @Test fun theChoiceSurvivesTheSavedStateBundle() {
        val choices = SourceChoices()
        choices.choose(DevicePage.Videos, ContentSource.Device)
        choices.choose(DevicePage.Files, ContentSource.Server)
        val back = SourceChoices.decode(choices.encode())
        DevicePage.entries.forEach { assertEquals(choices.of(it), back.of(it)) }
    }

    @Test fun aSavedEntryItDoesNotKnowIsDropped() {
        val back = SourceChoices.decode(listOf("Music=Device", "Podcasts=Device", "Videos=Somewhere", "garbage"))
        assertEquals(ContentSource.Device, back.of(DevicePage.Music))
        assertEquals(ContentSource.Server, back.of(DevicePage.Videos))
        assertEquals(ContentSource.Server, back.of(DevicePage.Files))
    }

    // ── the page ────────────────────────────────────────────────────────

    /** What each side did: composed (a remembered token), disposed, got Back. */
    private class Side(val name: String) {
        val tokens = mutableListOf<Any>()
        var disposed = 0
        var backs = 0

        @Composable
        fun Content() {
            val token = remember { Any().also { tokens += it } }
            DisposableEffect(token) { onDispose { disposed++ } }
            BackHandler { backs++ }
        }
    }

    private object NoFocus : FocusManager {
        var cleared = 0
        override fun clearFocus(force: Boolean) {
            cleared++
        }
        override fun moveFocus(focusDirection: FocusDirection) = false
    }

    @Test fun switchingKeepsTheServersSideAndBackGoesToTheSideShown() {
        val choices = SourceChoices()
        val server = Side("server")
        val device = Side("device")
        var dispatcher: OnBackPressedDispatcher? = null
        withHeadlessWindow {
            val tree = NodeTree()
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        // Back handlers register only on a started lifecycle.
                        (LocalLifecycleOwner.current.lifecycle as LifecycleRegistry).currentState = Lifecycle.State.RESUMED
                        dispatcher = LocalOnBackPressedDispatcherOwner.current!!.onBackPressedDispatcher
                        CompositionLocalProvider(
                            LocalSourceChoices provides choices,
                            LocalFocusManager provides NoFocus,
                            LocalServerChoice provides ServerChoice.Available("Hearth"),
                        ) {
                            SourceSwitchPage(DevicePage.Music, server = { server.Content() }, device = { device.Content() })
                        }
                    }
                }
                settle()
                val shown = tree.texts()
                assertTrue("the switch names the server: $shown", "Hearth" in shown)
                assertTrue("and the phone: $shown", "your device" in shown)
                assertEquals("the server's side first", 1, server.tokens.size)
                assertEquals("the phone's side waits until asked for", 0, device.tokens.size)
                dispatcher!!.onBackPressed()
                assertEquals(1, server.backs)

                choices.choose(DevicePage.Music, ContentSource.Device)
                settle()
                assertEquals(1, device.tokens.size)
                assertEquals("the server's side is kept, not rebuilt", 1, server.tokens.size)
                assertEquals(0, server.disposed)
                dispatcher!!.onBackPressed()
                assertEquals("Back is the phone side's now", 1, device.backs)
                assertEquals("not the hidden server side's", 1, server.backs)

                choices.choose(DevicePage.Music, ContentSource.Server)
                settle()
                assertEquals("back to the very same server side", 1, server.tokens.size)
                assertEquals(1, device.tokens.size)
                assertEquals("the phone's side is kept too", 0, device.disposed)
                dispatcher!!.onBackPressed()
                assertEquals(2, server.backs)
                assertEquals(1, device.backs)
            }
        }
    }

    @Test fun bothSidesComeBackAsLeftAfterTheWorkspaceGoesAwayAndReturns() {
        // A spell in the background (the system folder picker included)
        // drops the app to local media and back; the workspace is a saveable
        // slot (AppShell), so what a side keeps saveable comes back with it.
        val choices = SourceChoices()
        var workspace by mutableStateOf(true)
        var folder: MutableState<String>? = null
        val server = Side("server")
        val device = Side("device")
        withHeadlessWindow {
            composeTest(NodeTree()) {
                setContent {
                    HeadlessUi {
                        val holder = rememberSaveableStateHolder()
                        CompositionLocalProvider(LocalSourceChoices provides choices, LocalFocusManager provides NoFocus) {
                            if (workspace) {
                                holder.SaveableStateProvider("workspace") {
                                    SourceSwitchPage(
                                        DevicePage.Files,
                                        server = {
                                            folder = rememberSaveable { mutableStateOf("") }
                                            server.Content()
                                        },
                                        device = { device.Content() },
                                    )
                                }
                            }
                        }
                    }
                }
                settle()
                folder!!.value = "Documents/Videos"
                choices.choose(DevicePage.Files, ContentSource.Device)
                settle()
                workspace = false
                settle()
                assertEquals("both sides went with the workspace", 1, server.disposed)
                assertEquals(1, device.disposed)
                workspace = true
                settle()
                assertEquals("the phone's side is on screen again", 2, device.tokens.size)
                assertEquals("the hidden server side is back too, before it is asked for", 2, server.tokens.size)
                assertEquals("where it was left", "Documents/Videos", folder!!.value)
            }
        }
    }

    @Test fun aPageRememberedOnTheDeviceSideOpensThere() {
        val choices = SourceChoices().apply { choose(DevicePage.Videos, ContentSource.Device) }
        val server = Side("server")
        val device = Side("device")
        withHeadlessWindow {
            composeTest(NodeTree()) {
                setContent {
                    HeadlessUi {
                        CompositionLocalProvider(LocalSourceChoices provides choices, LocalFocusManager provides NoFocus) {
                            SourceSwitchPage(DevicePage.Videos, server = { server.Content() }, device = { device.Content() })
                        }
                    }
                }
                settle()
                assertEquals(1, device.tokens.size)
                assertEquals("the server isn't asked for a page nobody is looking at", 0, server.tokens.size)
            }
        }
    }
}
