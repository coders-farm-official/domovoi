package com.domovoi.app.ui.screens.settings

import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.buttons
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Trusted servers card lets the ACTIVE server be forgotten too
 * (security round 3 review, P2-at-01): its row carries a forget button
 * that asks for confirmation ([onForgetActive]) instead of forgetting
 * outright, and every other row's forget names its own server. Composed
 * for real on the JVM ([HeadlessUi]); the buttons are read off their
 * nodes and tapped.
 */
class TrustedServersCardTest {

    private fun card(taps: NodeTree.() -> Unit): Pair<List<String>, Int> {
        val forgot = mutableListOf<String>()
        var forgetActiveAsked = 0
        val tree = NodeTree()
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        TrustedServersCard(
                            trusted = setOf("http://10.0.2.2:6390", "http://10.0.2.2:6397"),
                            known = mapOf("http://10.0.2.2:6390" to "Domovoi"),
                            active = "http://10.0.2.2:6390",
                            fingerprintOf = { if (it.endsWith("6390")) "SHA256:home" else null },
                            paired = { it.endsWith("6390") },
                            onForget = { forgot += it },
                            onForgetActive = { forgetActiveAsked++ },
                        )
                    }
                }
                // The active row's "connected" pill pulses for as long as it shows.
                settle(untilQuiet = false, frames = 20)
                tree.taps()
            }
        }
        return forgot to forgetActiveAsked
    }

    @Test fun theActiveRowHasAForgetButtonThatAsksFirst() {
        val (forgot, asked) = card {
            val forgets = buttons().filter { it.label == "forget" }
            assertEquals("one per row, the active row first", 2, forgets.size)
            assertTrue(forgets.all { it.enabled })
            forgets[0].click()
        }
        assertEquals(1, asked)
        assertTrue("nothing forgotten without confirmation", forgot.isEmpty())
    }

    @Test fun anotherRowsForgetNamesItsOwnServer() {
        val (forgot, asked) = card {
            buttons().filter { it.label == "forget" }[1].click()
        }
        assertEquals(listOf("http://10.0.2.2:6397"), forgot)
        assertEquals(0, asked)
    }

    @Test fun theCardSaysWhatForgettingTheConnectedServerDoes() {
        withHeadlessWindow {
            val tree = NodeTree()
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        TrustedServersCard(
                            trusted = setOf("http://10.0.2.2:6390"), known = emptyMap(), active = "http://10.0.2.2:6390",
                            fingerprintOf = { "SHA256:home" }, paired = { true }, onForget = {}, onForgetActive = {},
                        )
                    }
                }
                settle(untilQuiet = false, frames = 20)
                val all = tree.texts().joinToString("\n")
                assertTrue(all, all.contains("returns you to the server list"))
                assertTrue(all, all.contains("SHA256:home"))
            }
        }
    }
}
