package com.domovoi.app.net

import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * A server address as a person typed it, made into one the app can save:
 * `http://` when no scheme was given, `:6369` when no port, and the
 * cleartext rule applied up front — a plain-http address outside the home
 * network is refused with the reason, before anything is probed or stored.
 *
 * One rule for both places an address can be typed: the picker's "add
 * manually" and Settings → Connection. Until 2026-10-08 the Connection
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

    /** Null for a blank input (nothing to do). */
    fun fromTyped(input: String, defaultPort: Int = Discovery.DEFAULT_PORT): Result? {
        var url = input.trim().trimEnd('/')
        if (url.isBlank()) return null
        if (!url.contains("://")) url = "http://$url"
        if (!Regex(":\\d+$").containsMatchIn(url.substringAfter("://"))) url = "$url:$defaultPort"
        val parsed = url.toHttpUrlOrNull() ?: return Result.Refused("that is not a server address")
        if (!CleartextPolicy.permits(parsed)) return Result.Refused(CleartextPolicy.refusalMessage(parsed.host))
        return Result.Ok(url)
    }
}
