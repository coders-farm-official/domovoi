package com.domovoi.app.ui.shell

import androidx.compose.runtime.compositionLocalOf
import kotlinx.coroutines.delay

/**
 * Which shell [AppShell] renders: the on-device shell (music + videos from
 * MediaStore, and the phone's own files; no domovoi needed), the full
 * server workspace, or (only until the saved server first answers) a
 * plain "Connecting to <name>…" screen ([Connecting]).
 */
internal enum class ShellMode { Local, Workspace, Connecting }

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
 *
 * Before the saved server has answered even once ([answeredOnce]: opening
 * the app, or switching servers) the workspace is not shown at all: its
 * pages could only spin, and away from home they spun for the whole grace
 * period before the switch, which is where people got lost. Instead a plain
 * "Connecting" screen for up to [LOOKING_AFTER_MS] (at home the server
 * answers well inside it), then, still unanswered ([looking]), the phone's
 * own media with a "Looking for <name>" line until it answers or the grace
 * runs out.
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
    /** The server has answered at least once since the app opened (or since
     *  this server was picked): the live socket, or any HTTP answer. */
    answeredOnce: Boolean = true,
    /** Asked for [LOOKING_AFTER_MS] on screen without an answer. */
    looking: Boolean = false,
): ShellMode =
    when {
        serverUrl.isBlank() -> ShellMode.Local
        preferLocal -> ShellMode.Local
        identityChanged -> ShellMode.Local
        pairingRequired || tokenTrouble -> ShellMode.Workspace
        unreachable -> ShellMode.Local
        !answeredOnce -> if (looking) ShellMode.Local else ShellMode.Connecting
        else -> ShellMode.Workspace
    }

/**
 * Whether the workspace is covered by a "Looking for <name>…" card: the
 * server answered earlier, but has now gone [LOOKING_AFTER_MS] on screen
 * without answering (the phone left the house with the app open, or came
 * back to the app away from home). The pages underneath could only spin, so
 * they are covered rather than shown. They stay composed under the cover,
 * so a screen waiting on the system picker still gets its answer. The grace
 * period then drops the app to the phone's own media as before.
 */
internal fun coversWorkspace(mode: ShellMode, answeredOnce: Boolean, looking: Boolean, unreachable: Boolean): Boolean =
    mode == ShellMode.Workspace && answeredOnce && looking && !unreachable

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

    /** Saved, not answered yet, and still inside the grace period: being
     *  looked for ([shellMode]'s `looking`). */
    data class Looking(val label: String) : ServerChoice

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
    looking: Boolean = false,
): ServerChoice = when {
    serverUrl.isBlank() -> ServerChoice.NoServer
    notOurs -> ServerChoice.NotOurs(label)
    // With the token held, every request fails and the server reads as
    // unreachable; the identity verdict says why, so it is named first.
    coreDown -> ServerChoice.CoreDown(label)
    unreachable -> ServerChoice.Unreachable(label)
    looking -> ServerChoice.Looking(label)
    else -> ServerChoice.Available(label, pairingRequired)
}

/** The one-line status under the server side of the switch. */
internal fun ServerChoice.reason(): String = when (this) {
    ServerChoice.NoServer -> "no server yet · pick one below"
    is ServerChoice.Looking -> "looking for it…"
    is ServerChoice.Unreachable -> "can't reach it right now"
    is ServerChoice.NotOurs -> "looks like a different Domovoi, or it was reinstalled"
    is ServerChoice.CoreDown -> "up, but its core isn't answering yet"
    is ServerChoice.Available -> if (needsPairing) "available · needs the household token" else "available"
}

/** Name for the server side of the switch. */
internal fun ServerChoice.title(): String = when (this) {
    ServerChoice.NoServer -> "your Domovoi"
    is ServerChoice.Looking -> label
    is ServerChoice.Unreachable -> label
    is ServerChoice.NotOurs -> label
    is ServerChoice.CoreDown -> label
    is ServerChoice.Available -> label
}

/**
 * The phone's-own-media layout's top-bar title. It says plainly where the
 * app is, since that layout's bar has no Home (home pages need the server).
 */
internal fun ServerChoice.localTitle(): String = when (this) {
    is ServerChoice.Looking -> LOOKING_TITLE
    is ServerChoice.Unreachable -> AWAY_TITLE
    else -> ON_THIS_PHONE_TITLE
}

internal const val AWAY_TITLE = "Away from home"
internal const val LOOKING_TITLE = "Looking for home…"
internal const val ON_THIS_PHONE_TITLE = "On this phone"

/**
 * The line under the title when the saved server can't be used right now
 * (null when there is nothing to explain). A changed identity has its own
 * words and button (IdentityChanged.kt), so it is not here.
 */
internal fun ServerChoice.awayLine(): String? = when (this) {
    is ServerChoice.Looking ->
        "Looking for $label. Until it answers, here's what's on this phone."
    is ServerChoice.Unreachable ->
        "You're away from home, or $label is off. Here's what's on this phone. " +
            "Your home pages come back by themselves when the phone reaches $label again."
    is ServerChoice.CoreDown ->
        "$label is up but its core is not answering (starting, updating, or busy), so " +
            "nothing was sent to it yet. Here's what's on this phone. Your home pages " +
            "come back when the core does."
    else -> null
}

/** The cover over the workspace while the server is being looked for ([coversWorkspace]). */
internal fun lookingCoverTitle(label: String): String = "Looking for $label…"
internal const val LOOKING_COVER_LINE =
    "If you're away from home, what's on this phone opens in a few seconds."

/** The plain screen shown before the saved server first answers ([ShellMode.Connecting]). */
internal fun connectingLine(label: String): String = "Connecting to $label…"

/**
 * How long the app asks, on screen, before it stops showing the server's
 * pages (or the Connecting screen) and says it is looking. At home the live
 * socket is up well inside this.
 */
internal const val LOOKING_AFTER_MS = 2_000L

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
    /** Called on every answer, before [publish]. */
    onAnswer: () -> Unit = {},
    publish: (unreachable: Boolean) -> Unit,
) {
    reach.resume(clock())
    try {
        while (true) {
            val answered = probe()
            val now = clock()
            if (answered) {
                reach.answered(now)
                onAnswer()
            } else {
                reach.missed(now)
            }
            publish(reach.unreachable)
            wait(REACH_PROBE_EVERY_MS)
        }
    } finally {
        reach.pause(clock())
    }
}

/** Opens the connection dialog, which AppShell owns above both shells. */
internal val LocalOpenConnection = compositionLocalOf<() -> Unit> { {} }
