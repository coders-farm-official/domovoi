package com.domovoi.app.data

import androidx.compose.runtime.compositionLocalOf
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * Developer options: the tools for measuring the app on this phone, kept out
 * of ordinary households' way (owner, 2026-10-16: design for ordinary
 * households, no confusing controls). Under them, in Settings → About and
 * the no-server settings (ui/screens/settings/DeveloperOptionsSection.kt):
 * the on-device assistant benchmark; in the assistant, a reply's timings in
 * its long-press menu and the last stop's timing in the model settings
 * ([LocalDeveloperOptionsOn]).
 *
 * Turned on as Android's own are: tap the app version seven times
 * ([VersionTaps]). Turned off with the switch at the top of the section.
 * The choice is kept on the phone ([Store], the `domovoi` DataStore via
 * [Prefs]); a phone that never chose starts with them on in a debug build
 * (a developer's) and off in a release build (a household's).
 *
 * [available] is false where there is nothing to put under them: an install
 * without the on-device assistant (a 32-bit phone or APK,
 * assistant/AssistantSupport.kt). There they are never on, and the version
 * line is plain text, so no tap turns on an empty section.
 */
class DeveloperOptions(
    private val store: Store,
    /** A debuggable build (ApplicationInfo.FLAG_DEBUGGABLE). */
    val debugBuild: Boolean,
    val available: Boolean,
) {
    /** Where the choice is kept. */
    interface Store {
        /** The saved choice; null when this phone never made one. */
        fun loadDeveloperOptions(): Boolean?
        fun saveDeveloperOptions(on: Boolean)
    }

    private val _on = MutableStateFlow(available && (store.loadDeveloperOptions() ?: debugBuild))

    /** Whether the developer tools show. */
    val on: StateFlow<Boolean> = _on

    fun turnOn() = set(true)

    fun turnOff() = set(false)

    private fun set(value: Boolean) {
        if (!available) return
        _on.value = value
        store.saveDeveloperOptions(value)
    }

    companion object {
        const val TAPS = 7

        /** Taps left from which each tap says how many remain (Android's:
         *  the countdown starts on the third tap). */
        const val COUNTDOWN_FROM = 4

        const val ON_TEXT = "Developer options on"
        const val ALREADY_ON_TEXT = "Developer options are already on"
        const val OFF_TEXT = "Developer options off"

        fun stepsAwayText(left: Int): String =
            "You are now $left ${if (left == 1) "step" else "steps"} away from developer options"
    }
}

/** What one tap on the app version line did. */
sealed interface VersionTap {
    /** Nothing to say: an early tap, or developer options aren't available. */
    data object Quiet : VersionTap
    data class StepsAway(val left: Int) : VersionTap
    data object TurnedOn : VersionTap
    data object AlreadyOn : VersionTap

    /** The toast for this tap, or null for none. */
    val message: String?
        get() = when (this) {
            Quiet -> null
            is StepsAway -> DeveloperOptions.stepsAwayText(left)
            TurnedOn -> DeveloperOptions.ON_TEXT
            AlreadyOn -> DeveloperOptions.ALREADY_ON_TEXT
        }
}

/**
 * The count of taps on the app version line, as Android counts taps on its
 * build number: the seventh turns developer options on, and from the third
 * each says how many are left. One per showing of the line (it is
 * `remember`ed there), so leaving the screen starts the count again, as
 * Android's does when its About screen comes back.
 */
class VersionTaps(private val options: DeveloperOptions) {
    private var left = DeveloperOptions.TAPS

    fun tap(): VersionTap {
        if (!options.available) return VersionTap.Quiet
        if (options.on.value) {
            left = DeveloperOptions.TAPS
            return VersionTap.AlreadyOn
        }
        left--
        return when {
            left <= 0 -> {
                left = DeveloperOptions.TAPS
                options.turnOn()
                VersionTap.TurnedOn
            }
            left <= DeveloperOptions.COUNTDOWN_FROM -> VersionTap.StepsAway(left)
            else -> VersionTap.Quiet
        }
    }
}

/** The app version line's words: the versionName, and whether this is a
 *  debug build (which starts with developer options on). */
fun appVersionText(versionName: String?, debugBuild: Boolean): String =
    "app version ${versionName?.takeIf { it.isNotBlank() } ?: "unknown"}" + if (debugBuild) " (debug build)" else ""

/**
 * Whether developer options are on, for the parts of the app that only show
 * with them (the assistant's reply and stop timings). Provided at the root
 * (MainActivity) from AppContainer.developerOptions; false wherever nothing
 * provides it (a test's composition, room mode).
 */
val LocalDeveloperOptionsOn = compositionLocalOf { false }
