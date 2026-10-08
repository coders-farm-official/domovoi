package com.domovoi.app.player

import androidx.media3.common.Player
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The exported media session admits the phone's own controllers and the
 * system's, turns every other app away, and never lets anyone choose what
 * the authenticated player fetches (security round 3, A6-01).
 */
class SessionAccessTest {
    private val own = "com.domovoi.app"

    private fun caller(
        pkg: String?,
        notification: Boolean = false,
        automotive: Boolean = false,
        companion: Boolean = false,
        trusted: Boolean = false,
    ) = SessionAccess.Caller(pkg, own, notification, automotive, companion, trusted)

    @Test fun anotherAppOnThePhoneIsTurnedAway() {
        assertFalse(SessionAccess.admits(caller("com.evil.grabber")))
        assertFalse("no package is no package", SessionAccess.admits(caller(null)))
        assertFalse(SessionAccess.admits(caller("")))
    }

    @Test fun theAppItselfAndItsNotificationControllerComeIn() {
        assertTrue(SessionAccess.admits(caller(own)))
        assertTrue(SessionAccess.admits(caller("com.android.systemui", notification = true)))
    }

    @Test fun theSystemsOwnControllersComeIn() {
        // The lock screen and quick settings, Bluetooth AVRCP, a watch: the
        // system vouches for them (MEDIA_CONTENT_CONTROL or an enabled
        // notification listener). Android Auto and Automotive by role.
        assertTrue(SessionAccess.admits(caller("com.android.systemui", trusted = true)))
        assertTrue(SessionAccess.admits(caller("com.android.bluetooth", trusted = true)))
        assertTrue(SessionAccess.admits(caller("com.google.android.projection.gearhead", companion = true)))
        assertTrue(SessionAccess.admits(caller("com.android.car.media", automotive = true)))
    }

    @Test fun nobodyGetsToChooseWhatPlays() {
        // These are the commands media3's default onConnect would grant and
        // whose legacy projection is ACTION_PLAY_FROM_URI / PREPARE_FROM_URI.
        assertEquals(
            setOf(Player.COMMAND_SET_MEDIA_ITEM, Player.COMMAND_CHANGE_MEDIA_ITEMS),
            SessionAccess.MEDIA_ITEM_COMMANDS,
        )
        // Transport stays: the notification needs play/pause and skipping.
        assertFalse(Player.COMMAND_PLAY_PAUSE in SessionAccess.MEDIA_ITEM_COMMANDS)
        assertFalse(Player.COMMAND_SEEK_TO_NEXT in SessionAccess.MEDIA_ITEM_COMMANDS)
        assertFalse(Player.COMMAND_STOP in SessionAccess.MEDIA_ITEM_COMMANDS)
    }

    @Test fun theRefusalNamesThePackageWithoutGuessing() {
        assertEquals(
            "media session: refused a controller from com.evil.grabber",
            SessionAccess.refusalLine(caller("com.evil.grabber")),
        )
        assertEquals(
            "media session: refused a controller from an unknown package",
            SessionAccess.refusalLine(caller(null)),
        )
    }
}
