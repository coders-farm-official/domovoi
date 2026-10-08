package com.domovoi.app.player

import androidx.media3.common.Player

/**
 * Who may connect to the media session, and what they may do once there.
 *
 * PlaybackService is an exported `MediaSessionService` — it has to be, or
 * the system's media controls, Bluetooth and Android Auto could not reach
 * it — and until 2026-10-08 it accepted every controller with media3's
 * default commands. Those include `COMMAND_SET_MEDIA_ITEM`, and the
 * session's player streams through the app's authenticated OkHttpClient:
 * any app on the phone could connect, hand the session a URI of its own
 * and have the phone fetch it (security round 3, A6-01). The token no
 * longer leaves the server's scope (net/TokenScope.kt) and, from here, the
 * session no longer takes media items from anyone:
 *
 *  * a controller is admitted when it is this app, the service's own media
 *    notification controller, Android Auto / Automotive, or a controller
 *    the system itself vouches for (`ControllerInfo.isTrusted`: holds
 *    `MEDIA_CONTENT_CONTROL`, or an enabled notification listener — the
 *    system UI, Bluetooth AVRCP, a watch); anything else is rejected;
 *  * an admitted controller gets the default player commands MINUS
 *    [MEDIA_ITEM_COMMANDS], and the session's `onAddMediaItems` /
 *    `onSetMediaItems` fail outright. The UI drives the ExoPlayer directly
 *    ([PlayerController]), so nothing legitimate ever needed either.
 *
 * Pure, so the rule is unit-tested as written (PlaybackService itself needs
 * a device).
 */
object SessionAccess {

    /** The commands that let a controller choose WHAT plays — and so what
     *  URL the authenticated player opens. Never granted to a controller. */
    val MEDIA_ITEM_COMMANDS: Set<Int> = setOf(
        Player.COMMAND_SET_MEDIA_ITEM,
        Player.COMMAND_CHANGE_MEDIA_ITEMS,
    )

    /** What is known about a controller asking to connect. */
    data class Caller(
        val packageName: String?,
        val ownPackage: String,
        val isMediaNotificationController: Boolean = false,
        val isAutomotiveController: Boolean = false,
        val isAutoCompanionController: Boolean = false,
        /** The system vouches for it (media3 `ControllerInfo.isTrusted`). */
        val isTrustedBySystem: Boolean = false,
    )

    /** Whether [caller] may connect at all. */
    fun admits(caller: Caller): Boolean =
        (caller.packageName != null && caller.packageName == caller.ownPackage) ||
            caller.isMediaNotificationController ||
            caller.isAutomotiveController ||
            caller.isAutoCompanionController ||
            caller.isTrustedBySystem

    /** The player commands an admitted controller receives: [defaults]
     *  without [MEDIA_ITEM_COMMANDS]. */
    fun playerCommandsFor(defaults: Player.Commands): Player.Commands =
        defaults.buildUpon().removeAll(*MEDIA_ITEM_COMMANDS.toIntArray()).build()

    /** One line for the log when a controller is turned away. */
    fun refusalLine(caller: Caller): String =
        "media session: refused a controller from ${caller.packageName ?: "an unknown package"}"
}
