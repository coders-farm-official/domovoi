package com.domovoi.app.data

import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json

/**
 * The two things this phone remembers ABOUT a Domovoi server, kept as pure
 * functions so they can be reasoned about (and tested) without a Context:
 *
 *  * which servers the user has **trusted** — picking a server means loading
 *    its capability manifest and its plugin-backed screens, so the picker
 *    asks first and nothing is written until the answer is yes;
 *  * the **household device token** each trusted server issued, which this
 *    app presents as `X-Device-Token` on every request to it.
 *
 * Both are stored per server URL: two Domovois are two households with two
 * tokens, and trusting one says nothing about the other.
 *
 * [Prefs] owns the DataStore side. Tokens live in the app's own DataStore
 * file, which is private to the app's uid; keeping the file out of Android
 * cloud backups is a manifest change that ships separately.
 */
object ServerCredentials {

    /** Trailing slashes and stray whitespace are not part of a server's identity. */
    fun normalize(url: String): String = url.trim().trimEnd('/')

    /** `10.0.0.42:6369` — the address a person can check against the box. */
    fun address(url: String): String =
        normalize(url).removePrefix("http://").removePrefix("https://")

    fun isTrusted(trusted: Set<String>, url: String): Boolean {
        val clean = normalize(url)
        return clean.isNotBlank() && clean in trusted
    }

    fun withTrusted(trusted: Set<String>, url: String): Set<String> {
        val clean = normalize(url)
        return if (clean.isBlank()) trusted else trusted + clean
    }

    fun withoutTrusted(trusted: Set<String>, url: String): Set<String> =
        trusted - normalize(url)

    // ── Device tokens, one per server ──────────────────────────────────

    fun tokenFor(tokens: Map<String, String>, url: String): String? =
        tokens[normalize(url)]?.takeIf { it.isNotBlank() }

    /** A blank token REMOVES the entry: that is what unpairing means. */
    fun withToken(tokens: Map<String, String>, url: String, token: String?): Map<String, String> {
        val clean = normalize(url)
        if (clean.isBlank()) return tokens
        val value = token?.trim().orEmpty()
        return if (value.isEmpty()) tokens - clean else tokens + (clean to value)
    }

    fun encodeTokens(tokens: Map<String, String>): String =
        Json.encodeToString(MapSerializer(String.serializer(), String.serializer()), tokens)

    /** Never throws: a corrupt or absent blob just means "nothing paired yet". */
    fun decodeTokens(raw: String?): Map<String, String> = runCatching {
        Json.decodeFromString(MapSerializer(String.serializer(), String.serializer()), raw ?: "{}")
    }.getOrDefault(emptyMap())

    fun encodeTrusted(trusted: Set<String>): String =
        Json.encodeToString(kotlinx.serialization.builtins.SetSerializer(String.serializer()), trusted)

    fun decodeTrusted(raw: String?): Set<String> = runCatching {
        Json.decodeFromString(kotlinx.serialization.builtins.SetSerializer(String.serializer()), raw ?: "[]")
    }.getOrDefault(emptySet())
}
