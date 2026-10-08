package com.domovoi.app.ui.shell

import androidx.compose.runtime.compositionLocalOf

/**
 * Which shell [AppShell] renders: the on-device shell (music + videos from
 * MediaStore, and the phone's own files; no domovoi needed) or the full
 * server workspace.
 */
internal enum class ShellMode { Local, Workspace }

/**
 * A saved server is not the same as a reachable one. Take the phone out of
 * the house and the workspace would spin on every panel until each request
 * timed out; instead, once the server has not answered for
 * [UNREACHABLE_GRACE_MS], the app drops back to local media, and comes back
 * to the workspace the moment it answers again.
 *
 * A pairing refusal is the exception: the server answered, it just wants the
 * household token, so the workspace stays up to show the pairing screen.
 *
 * [preferLocal] is the person's own choice in the connection dialog ("this
 * phone"): it holds even at home with the server answering, until they pick
 * the server again. Choosing the server keeps the automatic fallback above.
 */
internal fun shellMode(
    serverUrl: String,
    unreachable: Boolean,
    pairingRequired: Boolean,
    preferLocal: Boolean = false,
): ShellMode =
    when {
        serverUrl.isBlank() -> ShellMode.Local
        preferLocal -> ShellMode.Local
        pairingRequired -> ShellMode.Workspace
        unreachable -> ShellMode.Local
        else -> ShellMode.Workspace
    }

/**
 * Whether the connection dialog's server side can be picked right now, and
 * if not, why: it is greyed out with the reason rather than hidden.
 */
internal sealed interface ServerChoice {
    /** No server saved yet: the picker below the switch is how to add one. */
    data object NoServer : ServerChoice

    /** Saved, but nothing has answered for the grace period. */
    data class Unreachable(val label: String) : ServerChoice

    /** Something answers at the address but failed the identity proof (A6-03). */
    data class NotOurs(val label: String) : ServerChoice

    /** Answering; [needsPairing] when it wants the household token first. */
    data class Available(val label: String, val needsPairing: Boolean = false) : ServerChoice
}

internal val ServerChoice.enabled: Boolean get() = this is ServerChoice.Available

internal fun serverChoice(
    serverUrl: String,
    label: String,
    unreachable: Boolean,
    notOurs: Boolean,
    pairingRequired: Boolean,
): ServerChoice = when {
    serverUrl.isBlank() -> ServerChoice.NoServer
    notOurs -> ServerChoice.NotOurs(label)
    unreachable -> ServerChoice.Unreachable(label)
    else -> ServerChoice.Available(label, pairingRequired)
}

/** The one-line status under the server side of the switch. */
internal fun ServerChoice.reason(): String = when (this) {
    ServerChoice.NoServer -> "no server yet · pick one below"
    is ServerChoice.Unreachable -> "can't reach it right now"
    is ServerChoice.NotOurs -> "didn't prove it's your Domovoi"
    is ServerChoice.Available -> if (needsPairing) "available · needs the household token" else "available"
}

/** Name for the server side of the switch. */
internal fun ServerChoice.title(): String = when (this) {
    ServerChoice.NoServer -> "your Domovoi"
    is ServerChoice.Unreachable -> label
    is ServerChoice.NotOurs -> label
    is ServerChoice.Available -> label
}

/** The current [ServerChoice], provided by AppShell to both shells' connection dialogs. */
internal val LocalServerChoice = compositionLocalOf<ServerChoice> { ServerChoice.NoServer }

/**
 * How long the server may go without answering before it counts as
 * unreachable. Long enough for a couple of failed probes, so a blip at home
 * does not bounce the user out of the screen they are on.
 */
internal const val UNREACHABLE_GRACE_MS = 10_000L

/** How often the server is asked while the live socket is down. */
internal const val REACH_PROBE_EVERY_MS = 4_000L

/** Opens the connection dialog, which AppShell owns above both shells. */
internal val LocalOpenConnection = compositionLocalOf<() -> Unit> { {} }
