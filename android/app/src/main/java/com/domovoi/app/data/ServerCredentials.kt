package com.domovoi.app.data

import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json

/**
 * The things this phone remembers ABOUT a Domovoi server, kept as pure
 * functions so they can be reasoned about (and tested) without a Context:
 *
 *  * which servers the user has **trusted** — picking a server means loading
 *    its capability manifest and its plugin-backed screens, so the picker
 *    asks first and nothing is written until the answer is yes;
 *  * the **household device token** each trusted server issued, which this
 *    app presents as `X-Device-Token` on every request to it — and to
 *    nothing else (net/TokenScope.kt);
 *  * (in net/ServerIdentity.kt) the server's pinned **identity**.
 *
 * All are stored per server URL: two Domovois are two households with two
 * tokens, and trusting one says nothing about the other.
 *
 * [Prefs] owns the storage side: the trust list and the pins in the app's
 * DataStore, the tokens sealed in the [TokenVault] under an Android
 * Keystore key. Every file is excluded from cloud backup and from
 * device-to-device transfer (res/xml/data_extraction_rules.xml).
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

    /**
     * Trust that belongs to nothing: entries that are neither a known
     * server (with a row and a forget button in the picker and in
     * Settings) nor the [active] one. Forgotten on every server switch, so
     * an address trusted once can never be reused silently (P2-at-01).
     */
    fun orphanTrust(trusted: Set<String>, known: Collection<String>, active: String): Set<String> {
        val keep = known.map(::normalize).toSet() + normalize(active)
        return trusted.filterNot { normalize(it) in keep }.toSet()
    }

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

    // ── Shared-screen answers, one per server ──────────────────────────

    /** [url]'s last answer (null: it has never answered), looked up the way
     *  [Prefs.setSharedScreen] stores it — by the normalised address. */
    fun sharedAnswerFor(answers: Map<String, Boolean>, url: String): Boolean? =
        answers[normalize(url)]

    fun encodeSharedAnswers(answers: Map<String, Boolean>): String =
        Json.encodeToString(MapSerializer(String.serializer(), Boolean.serializer()), answers)

    /** Never throws: a corrupt or absent blob means "no server has answered yet". */
    fun decodeSharedAnswers(raw: String?): Map<String, Boolean> = runCatching {
        Json.decodeFromString(MapSerializer(String.serializer(), Boolean.serializer()), raw ?: "{}")
    }.getOrDefault(emptyMap())
}
