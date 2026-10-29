package com.domovoi.app.net

import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * A server address as a person typed it, made into one the app can save:
 * `http://` when no scheme was given, `:6369` when no port, and the
 * cleartext rule applied up front — a plain-http address outside the home
 * network is refused with the reason, before anything is probed or stored.
 *
 * One rule for both places an address can be typed: the picker's "add
 * manually" and Settings → Phones & browsers. Until 2026-10-08 the Connection
 * panel saved the raw string with neither the normalisation nor the check
 * (security round 3, A6-05 / P2-at-01).
 */
object ServerAddress {
    sealed class Result {
        /** The address to save, normalised. */
        data class Ok(val url: String) : Result()

        /** Why it was not accepted, in the user's words. */
        data class Refused(val message: String) : Result()
    }

    /**
     * Null for a blank input (nothing to do). A path, query or fragment is
     * dropped, so an address copied from the browser's bar while the
     * dashboard is open ("http://192.168.1.30:6369/files?x") saves as that
     * server. Until 2026-10-28 the port check ran over the path too and the
     * default port was appended after it (found by the iOS port).
     */
    fun fromTyped(input: String, defaultPort: Int = Discovery.DEFAULT_PORT): Result? {
        val typed = input.trim()
        if (typed.isBlank()) return null
        val scheme = if (typed.contains("://")) typed.substringBefore("://") else "http"
        val authority = typed.substringAfter("://").takeWhile { it != '/' && it != '?' && it != '#' }
        if (authority.isBlank()) return Result.Refused("that is not a server address")
        var url = "$scheme://$authority"
        if (!Regex(":\\d+$").containsMatchIn(authority)) url = "$url:$defaultPort"
        val parsed = url.toHttpUrlOrNull() ?: return Result.Refused("that is not a server address")
        if (!CleartextPolicy.permits(parsed)) return Result.Refused(CleartextPolicy.refusalMessage(parsed.host))
        return Result.Ok(url)
    }
}
