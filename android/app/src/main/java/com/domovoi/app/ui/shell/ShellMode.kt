package com.domovoi.app.ui.shell

/**
 * Which shell [AppShell] renders: the on-device media shell (music + videos
 * from MediaStore, no domovoi needed) or the full server workspace.
 */
internal enum class ShellMode { Local, Workspace }

/**
 * A saved server is not the same as a reachable one. Take the phone out of
 * the house and the workspace would spin on every panel until each request
 * timed out; instead, once the live connection has been down for
 * [UNREACHABLE_GRACE_MS], the app drops back to local media, and comes back
 * to the workspace the moment the connection does.
 *
 * A pairing refusal is the exception: the server answered, it just wants the
 * household token, so the workspace stays up to show the pairing screen.
 */
internal fun shellMode(serverUrl: String, unreachable: Boolean, pairingRequired: Boolean): ShellMode =
    when {
        serverUrl.isBlank() -> ShellMode.Local
        pairingRequired -> ShellMode.Workspace
        unreachable -> ShellMode.Local
        else -> ShellMode.Workspace
    }

/**
 * How long the live connection may be down before the server counts as
 * unreachable. Long enough to cover a cold start (one 6s connect timeout)
 * and a server restart's first few reconnect attempts, so a blip at home
 * does not bounce the user out of the screen they are on.
 */
internal const val UNREACHABLE_GRACE_MS = 10_000L
