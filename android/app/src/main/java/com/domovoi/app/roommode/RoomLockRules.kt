package com.domovoi.app.roommode

/**
 * The owner's rule, "exit behind the phone's unlock", made decisions — pure,
 * so they are tested on the JVM (RoomLockRulesTest).
 *
 * A phone that holds a household token (it is, or was, paired with the
 * house) must have a screen lock to be a room: the lock is what keeps the
 * household app, and that token, out of reach of whoever is in the room.
 * Room mode does not start without one, and stops listening if the lock is
 * removed while it runs. A room-only phone (no household token) may be a
 * room without a lock; leaving then asks for a confirmation and says why.
 */
object RoomLockRules {
    /** Whether room mode may run (start, resume, keep listening). */
    fun mayRun(holdsHouseholdToken: Boolean, deviceSecure: Boolean): Boolean =
        !holdsHouseholdToken || deviceSecure

    /**
     * Whether the room screen holds the screen on (FLAG_KEEP_SCREEN_ON,
     * RoomModeActivity is the only place that sets it, and only through
     * this). Only on the charger, and only while the phone is locked: then
     * the room shows over the lock screen and Home or Recents meets the
     * lock. While someone has it unlocked the screen times out as the phone
     * says, the phone locks itself, and when it wakes the room is back over
     * the lock screen with the screen held on. A phone with no secure lock
     * (a room-only phone may have none) has nothing to lock behind, so on
     * the charger it stays on as before.
     */
    fun keepScreenOn(keyguardLocked: Boolean, plugged: Boolean, deviceSecure: Boolean): Boolean =
        plugged && (keyguardLocked || !deviceSecure)

    /** Whether the room screen shows [RoomStatusText.LOCK_HINT]: a phone
     *  with a lock that is unlocked right now. */
    fun showLockHint(keyguardLocked: Boolean, deviceSecure: Boolean): Boolean =
        deviceSecure && !keyguardLocked

    /** How leaving room mode is guarded. */
    enum class Exit { DEVICE_CREDENTIAL, CONFIRM_NO_LOCK, SET_A_LOCK_FIRST }

    fun exit(holdsHouseholdToken: Boolean, deviceSecure: Boolean): Exit = when {
        deviceSecure -> Exit.DEVICE_CREDENTIAL
        holdsHouseholdToken -> Exit.SET_A_LOCK_FIRST
        else -> Exit.CONFIRM_NO_LOCK
    }

    /** What the room's service does with a start (RoomService.onStartCommand). */
    enum class Start { RUN, ASK_FOR_TAP, NEEDS_LOCK, NOTHING }

    /**
     * [stickyRestart]: Android restarted the service by itself (a null
     * intent): never take the microphone from the background — ask for a
     * tap if the room was listening. No room, or room mode off: nothing.
     * No microphone permission: the room screen has to ask. A household
     * phone without a screen lock: not until it has one.
     */
    fun start(
        stickyRestart: Boolean,
        active: Boolean,
        enrolled: Boolean,
        running: Boolean,
        micGranted: Boolean,
        holdsHouseholdToken: Boolean,
        deviceSecure: Boolean,
    ): Start = when {
        !active || !enrolled -> Start.NOTHING
        stickyRestart -> if (running) Start.ASK_FOR_TAP else Start.NOTHING
        !micGranted -> Start.ASK_FOR_TAP
        !mayRun(holdsHouseholdToken, deviceSecure) -> Start.NEEDS_LOCK
        else -> Start.RUN
    }
}
