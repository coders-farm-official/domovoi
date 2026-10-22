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

    /** Where phone chats are not in the install (a 32-bit phone or APK),
     *  the card says nothing about them. */
    @Test fun thePhoneChatsPointsOnlyWhereTheyAre() {
        val with = privacyPoints(assistant = true)
        val without = privacyPoints(assistant = false)
        assertEquals(6, with.size)
        assertEquals(4, without.size)
        assertTrue(without.none { it.contains("phone chat", ignoreCase = true) || it.contains("home server") || it.contains("lookup") })
        assertTrue(without.any { it.startsWith("Room mode, which you turn on yourself") })
        assertTrue(with.any { it.startsWith("Phone chats open the microphone only after you tap it") })
        assertTrue(with.any { it.startsWith("Phone chats, reply reports and problem reports stay on this phone") })
    }

    /** The card must not promise what the app no longer does: phone chats go
     *  online for a lookup only after its allow card, and hand a document, a
     *  letter or a draft to the home server only when the person sends it
     *  (docs/PRIVACY_POLICY.md, "Lookups you allow" and "Documents, letters
     *  and drafts"). The old "never leave it" / "only network use" wording
     *  stays gone, and the One Chat names replace "on-device assistant". */
    @Test fun theCardSaysLookupsAskFirstAndTheHomeServerHandOffIsAChoice() {
        val points = privacyPoints(assistant = true)
        val lookup = points.single { it.contains("lookup you allow") }
        assertTrue(lookup, lookup.startsWith("Domovoi on this phone answers phone chats on this phone"))
        assertTrue(lookup, "card shows the exact words" in lookup && "first" in lookup)
        assertTrue(lookup, "model" in lookup)
        val handOff = points.single { it.contains("home server") }
        assertTrue(handOff, "document" in handOff && "letter" in handOff && "draft" in handOff)
        assertTrue(handOff, "only when you choose" in handOff)
        assertTrue(handOff, "kept nowhere" in handOff)
        val all = points.joinToString(" ")
        listOf("never leave", "only network use", "on-device assistant", "The assistant").forEach {
            assertTrue("the card still says '$it'", it !in all)
        }
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
