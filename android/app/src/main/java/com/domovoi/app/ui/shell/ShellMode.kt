package com.domovoi.app.ui.shell

import androidx.compose.runtime.compositionLocalOf
import kotlinx.coroutines.delay

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
    /** The server proved a different key than the one pinned (a reinstall,
     *  a new machine, or someone else's server at this address): nothing is
     *  sent to it, and the local shell's banner asks first (IdentityChanged.kt). */
    identityChanged: Boolean = false,
    /** The server refused this phone's token, or asked it to wait
     *  ([tokenTrouble]): the workspace stays up to show the one card. */
    tokenTrouble: Boolean = false,
): ShellMode =
    when {
        serverUrl.isBlank() -> ShellMode.Local
        preferLocal -> ShellMode.Local
        identityChanged -> ShellMode.Local
        pairingRequired || tokenTrouble -> ShellMode.Workspace
        unreachable -> ShellMode.Local
        else -> ShellMode.Workspace
    }

/**
 * What stands between this phone and its server when the server answered
 * but will not take this phone's household token (net/TokenHealth.kt):
 * shown as ONE card in place of the workspace, never as "offline" and a
 * page of errors.
 */
internal sealed interface TokenTrouble {
    /** How long the server still makes the phone wait, in ms (0: no wait). */
    val waitMs: Long

    /** The server refused the token this phone holds: it is no longer sent,
     *  and the card offers to pair again. */
    data class NotRecognised(override val waitMs: Long = 0) : TokenTrouble

    /** The server asked the phone to wait before it looks at a token from it
     *  again (after wrong ones): nothing carrying the token is sent until then. */
    data class Waiting(override val waitMs: Long) : TokenTrouble
}

/**
 * The card to show for the active server, if any. A changed identity comes
 * first and is not this card's business: the token is not sent to a server
 * that proved a different key, and the person is asked about the key before
 * anything about pairing ([shellMode], IdentityChanged.kt). A wait only
 * matters while there is a token to wait with.
 */
internal fun tokenTrouble(
    serverUrl: String,
    paired: Boolean,
    rejected: Boolean,
    waitMs: Long,
    identityChanged: Boolean = false,
): TokenTrouble? = when {
    serverUrl.isBlank() || !paired -> null
    identityChanged -> null
    rejected -> TokenTrouble.NotRecognised(waitMs.coerceAtLeast(0))
    waitMs > 0 -> TokenTrouble.Waiting(waitMs)
    else -> null
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

    /** The web backend answers but its core does not, so the proof could not
     *  be taken and the token is held until it can (A6-03 review): starting,
     *  updating or busy — not an impostor, never a reason to forget it. */
    data class CoreDown(val label: String) : ServerChoice

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
    coreDown: Boolean = false,
): ServerChoice = when {
    serverUrl.isBlank() -> ServerChoice.NoServer
    notOurs -> ServerChoice.NotOurs(label)
    // With the token held, every request fails and the server reads as
    // unreachable; the identity verdict says why, so it is named first.
    coreDown -> ServerChoice.CoreDown(label)
    unreachable -> ServerChoice.Unreachable(label)
    else -> ServerChoice.Available(label, pairingRequired)
}

/** The one-line status under the server side of the switch. */
internal fun ServerChoice.reason(): String = when (this) {
    ServerChoice.NoServer -> "no server yet · pick one below"
    is ServerChoice.Unreachable -> "can't reach it right now"
    is ServerChoice.NotOurs -> "looks like a different Domovoi, or it was reinstalled"
    is ServerChoice.CoreDown -> "up, but its core isn't answering yet"
    is ServerChoice.Available -> if (needsPairing) "available · needs the household token" else "available"
}

/** Name for the server side of the switch. */
internal fun ServerChoice.title(): String = when (this) {
    ServerChoice.NoServer -> "your Domovoi"
    is ServerChoice.Unreachable -> label
    is ServerChoice.NotOurs -> label
    is ServerChoice.CoreDown -> label
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

/**
 * Whether the saved server counts as out of reach: it has gone unanswered
 * for [graceMs] of time the app spent ON SCREEN asking it.
 *
 * The time in the background does not count. Android freezes a cached app
 * (the system folder picker in front of it is enough): its live socket
 * misses a ping, and the first request after the thaw can fail on a stale
 * connection. Measured by the wall clock, the grace was long over by then,
 * and one failed probe dropped a phone sitting next to a healthy server to
 * local media. So the asking pauses when the app leaves the screen
 * ([pause]) and starts again with a fresh probe when it comes back
 * ([resume]); only foreground stretches add up, and a result that lands
 * while the app is away is no evidence either way.
 *
 * What it does not change: out of the house, on screen, the server not
 * answering, the app still falls back after [graceMs]; once out of reach it
 * stays so until an answer — a spell in the background never brings the
 * workspace back by itself.
 */
internal class Reachability(private val graceMs: Long = UNREACHABLE_GRACE_MS) {
    var unreachable = false
        private set

    /** Foreground time spent unanswered before the current stretch. */
    private var spentMs = 0L

    /** When the current foreground stretch of asking began (or last got an answer); null while not asking. */
    private var askingSince: Long? = null

    /** The app is on screen with the live socket down: asking starts (or starts again). */
    fun resume(now: Long) {
        if (askingSince == null) askingSince = now
    }

    /** The app left the screen (or the socket came back): asking stops, the stretch so far is kept. */
    fun pause(now: Long) {
        askingSince?.let { spentMs += (now - it).coerceAtLeast(0) }
        askingSince = null
    }

    /** The server answered: in reach, and the grace starts over. */
    fun answered(now: Long) {
        spentMs = 0
        if (askingSince != null) askingSince = now
        unreachable = false
    }

    /** The live socket is up: nothing to ask, nothing owed. */
    fun connected() {
        spentMs = 0
        askingSince = null
        unreachable = false
    }

    /** A probe went unanswered. Ignored unless the app is on screen asking. */
    fun missed(now: Long) {
        val since = askingSince ?: return
        if (spentMs + (now - since).coerceAtLeast(0) >= graceMs) unreachable = true
    }
}

/**
 * Ask the server every [REACH_PROBE_EVERY_MS] while the app is on screen,
 * [publish]ing the verdict after each answer. AppShell runs it under
 * `repeatOnLifecycle(STARTED)`: leaving the screen cancels it (and [reach]
 * stops counting, in `finally`), coming back starts it again with a fresh
 * probe first; a probe still out when the app left is dropped with it.
 */
internal suspend fun askWhileOnScreen(
    reach: Reachability,
    probe: suspend () -> Boolean,
    clock: () -> Long,
    wait: suspend (Long) -> Unit = { delay(it) },
    publish: (unreachable: Boolean) -> Unit,
) {
    reach.resume(clock())
    try {
        while (true) {
            val answered = probe()
            val now = clock()
            if (answered) reach.answered(now) else reach.missed(now)
            publish(reach.unreachable)
            wait(REACH_PROBE_EVERY_MS)
        }
    } finally {
        reach.pause(clock())
    }
}

/** Opens the connection dialog, which AppShell owns above both shells. */
internal val LocalOpenConnection = compositionLocalOf<() -> Unit> { {} }
