package com.domovoi.app.ui.screens.settings

import com.domovoi.app.testing.Bytecode
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Settings → About & updates's privacy card: its summary always, the privacy policy's
 * link only once the owner has filled in its https address
 * (PRIVACY_POLICY_URL; owner rule: no control that cannot do what it says).
 */
class PrivacyCardTest {

    @Test fun thePolicyLinkIsAPublishedHttpsPageOrNothing() {
        assertNull(privacyPolicyLink(""))
        assertNull(privacyPolicyLink("   "))
        assertNull("not https", privacyPolicyLink("http://codersfarm.org/privacy"))
        assertNull("not a web link", privacyPolicyLink("codersfarm.org/privacy"))
        assertEquals("https://codersfarm.org/domovoi/privacy", privacyPolicyLink(" https://codersfarm.org/domovoi/privacy "))
        // The constant itself: blank until the owner fills it, then https.
        assertTrue("PRIVACY_POLICY_URL must be blank or https: '$PRIVACY_POLICY_URL'",
            PRIVACY_POLICY_URL.isBlank() || privacyPolicyLink(PRIVACY_POLICY_URL) != null)
    }

    /** The link is the repo's own policy as GitHub shows it on main: the
     *  file must be in the repo it points at (it goes live when pushed). */
    @Test fun aGithubPolicyLinkPointsAtAFileInTheRepo() {
        val prefix = "https://github.com/coders-farm-official/domovoi/blob/main/"
        if (!PRIVACY_POLICY_URL.startsWith(prefix)) return
        val repo = java.io.File(System.getProperty("domovoi.repo.dir") ?: error("domovoi.repo.dir is not set"))
        val file = java.io.File(repo, PRIVACY_POLICY_URL.removePrefix(prefix))
        assertTrue("$file is missing", file.isFile)
        assertTrue("the policy names the app", file.readText().contains("Domovoi"))
    }

    @Test fun theCardSaysWhatTheAppKeepsAndLinksThePolicyOnlyWhenThereIsOne() {
        val tree = NodeTree()
        var shown = emptyList<String>()
        withHeadlessWindow {
            composeTest(tree) {
                setContent { HeadlessUi { PrivacyCard() } }
                settle(untilQuiet = false, frames = 5)
                shown = tree.texts()
            }
        }
        assertTrue(shown.toString(), "Privacy" in shown)
        PRIVACY_POINTS.forEach { assertTrue(it, it in shown) }
        // On the JVM the assistant's engine is not loadable: the 32-bit wording.
        assertEquals(privacyPoints(assistant = false), PRIVACY_POINTS)
        assertEquals(PRIVACY_POLICY_URL.isNotBlank(), "read the privacy policy" in shown)
    }

    /** Where the assistant is not in the install (a 32-bit phone or APK),
     *  the card says nothing about it. */
    @Test fun theAssistantsPointsOnlyWhereItIs() {
        val with = privacyPoints(assistant = true)
        val without = privacyPoints(assistant = false)
        assertEquals(5, with.size)
        assertEquals(4, without.size)
        assertTrue(without.none { it.contains("assistant") || it.contains("Conversations") })
        assertTrue(without.any { it.startsWith("Room mode, which you turn on yourself") })
        assertTrue(with.any { it.startsWith("The assistant opens the microphone only after you tap it") })
        assertTrue(with.any { it.startsWith("Conversations, reply reports and problem reports stay on this phone") })
    }

    @Test fun aboutAndTheLocalSettingsBothShowIt() {
        val card = "com/domovoi/app/ui/screens/settings/PrivacyCardKt"
        fun calledFrom(vararg facades: String) = facades.any { f ->
            runCatching { Bytecode.callers(f, card, setOf("PrivacyCard")).isNotEmpty() }.getOrDefault(false)
        }
        assertTrue("About", calledFrom(
            "com.domovoi.app.ui.screens.settings.SettingsScreenKt",
            "com.domovoi.app.ui.screens.settings.ComposableSingletons\$SettingsScreenKt",
        ))
        assertTrue("the local shell's settings", calledFrom(
            "com.domovoi.app.ui.screens.settings.LocalSettingsScreenKt",
            "com.domovoi.app.ui.screens.settings.ComposableSingletons\$LocalSettingsScreenKt",
        ))
    }
}
