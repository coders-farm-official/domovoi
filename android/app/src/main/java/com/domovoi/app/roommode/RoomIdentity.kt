package com.domovoi.app.roommode

import com.domovoi.app.net.Ed25519
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.longOrNull
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64

/**
 * "Is this the server this room belongs to?" — the room phone's port of the
 * Pi satellite's checks (satellite/server_identity.py), byte for byte where
 * bytes are signed:
 *
 * - [verifyServer] / [verifyHealthDocument]: `GET <core>/v1/health?challenge=
 *   <nonce>&addr=<host:port>` must come back signed by the PINNED key over
 *   our nonce AND the address we dialed. The core refuses to sign for an
 *   address that is not its own, so a LAN host that relays the question to
 *   the real core cannot pass the answer off as its own. Run before every
 *   connect of the room's socket (RoomEngine). Not the app's IdentityGate:
 *   that proves the WEB backend on 6369, unbound, cached per network; the
 *   room talks to the core on 6370 and holds no household token.
 * - [verifyManifestEnvelope] / [acceptManifestEnvelope]: a file list (wake
 *   models, sounds) is taken only inside an envelope our server signed
 *   (`signature_v2`, over the list, its issue time and serial), and never
 *   one older than the last this phone accepted on that channel.
 *
 * A room phone is always pinned (the enrolment carries the fingerprint), so
 * the unpinned trust-on-first-use branches of the Python have no twin here.
 */
object RoomIdentity {
    const val ALGORITHM = "ed25519"
    const val FINGERPRINT_PREFIX = "SHA256:"
    const val HEALTH_CONTEXT = "domovoi-health-v1"
    const val MANIFEST_CONTEXT_V2 = "domovoi-manifest-v2"

    const val SOUNDS_CHANNEL = "satellite-sounds"
    const val WAKE_MODELS_CHANNEL = "satellite-wake-models"
    /** domovoi/wake_base_models.py WAKE_BASE_CHANNEL. */
    const val WAKE_BASE_CHANNEL = "satellite-wake-base"

    private const val CHALLENGE_BYTES = 16

    /** A server that could not prove it is ours; [message] says why. */
    class IdentityException(message: String) : Exception(message)

    fun newChallenge(random: SecureRandom = SecureRandom()): String {
        val bytes = ByteArray(CHALLENGE_BYTES).also { random.nextBytes(it) }
        return bytes.joinToString("") { "%02x".format(it) }
    }

    fun fingerprintOf(publicKey: ByteArray): String =
        FINGERPRINT_PREFIX + Base64.getEncoder().withoutPadding()
            .encodeToString(MessageDigest.getInstance("SHA-256").digest(publicKey))

    /** `domovoi-health-v1\n<challenge>[\n<addr>]` (server_identity.health_message). */
    fun healthMessage(challenge: String, addr: String?): ByteArray {
        val text = HEALTH_CONTEXT + "\n" + challenge + (if (addr != null) "\n" + addr else "")
        return text.toByteArray(Charsets.UTF_8)
    }

    /**
     * The `host:port` a URL dials, spelled the way the core is asked to sign
     * it: host lower-cased, an IPv6 literal in brackets, the port always
     * present (`dialed_address`).
     */
    fun dialedAddress(httpBase: String): String {
        val text = httpBase.trim().let { if ("://" in it) it else "http://$it" }
            .replaceFirst(Regex("^ws://", RegexOption.IGNORE_CASE), "http://")
            .replaceFirst(Regex("^wss://", RegexOption.IGNORE_CASE), "https://")
        val url = text.toHttpUrlOrNull() ?: throw IdentityException("$httpBase is not an address")
        val host = if (url.host.contains(':')) "[${url.host}]" else url.host
        return "$host:${url.port}"
    }

    /** The health URL with both query values (`fetch_health`). */
    fun healthUrl(httpBase: String, challenge: String, addr: String): String =
        httpBase.trimEnd('/') + "/v1/health?challenge=" + challenge + "&addr=" +
            java.net.URLEncoder.encode(addr, "UTF-8").replace("+", "%20")

    /**
     * Prove the core at [httpBase] is the one pinned as [fingerprint]: ask
     * [fetch] for the health answer (a GET returning the body, throwing on
     * any transport failure or non-200) and judge it. Returns the proved
     * fingerprint; throws [IdentityException] otherwise.
     */
    fun verifyServer(httpBase: String, fingerprint: String, fetch: (String) -> String): String {
        val challenge = newChallenge()
        val addr = dialedAddress(httpBase)
        val body = try {
            fetch(healthUrl(httpBase, challenge, addr))
        } catch (e: Exception) {
            throw IdentityException("could not reach ${httpBase.trimEnd('/')}: ${e.message ?: e.javaClass.simpleName}")
        }
        val doc = runCatching { Json.parseToJsonElement(body) }.getOrNull()
        return verifyHealthDocument(doc, challenge, fingerprint, addr)
    }

    /** `verify_health_document`, pinned and address-bound. */
    fun verifyHealthDocument(doc: JsonElement?, challenge: String, expected: String, addr: String): String {
        if (doc !is JsonObject) throw IdentityException("the server's answer was not a document")
        val identity = doc["identity"] as? JsonObject
            ?: throw IdentityException("the server offered no identity to check (an older Domovoi server?)")
        if (str(identity, "algorithm") != ALGORITHM) {
            throw IdentityException("unsupported identity algorithm ${str(identity, "algorithm")}")
        }
        val public = unb64(str(identity, "public_key"))
        val signature = unb64(str(identity, "signature"))
        if (public == null || signature == null) {
            if (str(identity, "addr_refused") == addr) {
                throw IdentityException(
                    "the server would not sign for $addr: that address is not the server's own " +
                        "(something is relaying its answers, or the server needs it in TRUSTED_HOSTS)",
                )
            }
            throw IdentityException("the server's identity is malformed")
        }
        if (str(identity, "challenge") != challenge) throw IdentityException("the server answered a different challenge")
        val signedAddr = str(identity, "addr")
            ?: throw IdentityException("the proof is not bound to the address dialed ($addr): an older Domovoi server, or something relaying one")
        if (signedAddr != addr) {
            throw IdentityException("the signed address $signedAddr is not the one dialed ($addr)")
        }
        if (!Ed25519.verify(public, healthMessage(challenge, addr), signature)) {
            throw IdentityException("the server's signature did not verify")
        }
        val actual = fingerprintOf(public)
        val claimed = str(identity, "fingerprint")
        if (claimed != null && claimed.startsWith(FINGERPRINT_PREFIX) && claimed != actual) {
            throw IdentityException("the server's fingerprint does not match its key")
        }
        if (actual != expected) {
            throw IdentityException("this is a different server: $actual is not the $expected this room was set up for")
        }
        return actual
    }

    // ── signed file lists ───────────────────────────────────────────────

    /** An accepted envelope: the list, and what freshness records. */
    data class Accepted(val manifest: Map<String, String>, val serial: Long, val issuedAt: Long, val digest: String)

    /** What the phone last accepted on a channel (or record). */
    data class Seen(val serial: Long, val digest: String?)

    /**
     * The `{path: sha256}` list inside a signed envelope (`manifest.sig`),
     * or throw: our server's key ([expected]), the channel it was signed
     * for, an integer `issued_at` and `serial`, and `signature_v2` over all
     * of it (`verify_manifest_envelope`).
     */
    fun verifyManifestEnvelope(doc: JsonElement?, channel: String, expected: String): Accepted {
        if (doc !is JsonObject) throw IdentityException("the signed list was not a document")
        if (str(doc, "algorithm") != ALGORITHM) throw IdentityException("unsupported signature algorithm")
        if (str(doc, "channel") != channel) throw IdentityException("this signature is for ${str(doc, "channel")}, not $channel")
        val manifest = doc["manifest"] ?: throw IdentityException("the signed list carried no list")
        val public = unb64(str(doc, "public_key")) ?: throw IdentityException("the signed list is malformed")
        val actual = fingerprintOf(public)
        if (actual != expected) throw IdentityException("the list was signed by $actual, not $expected")
        val issuedAt = wholeNumber(doc["issued_at"])
        val serial = wholeNumber(doc["serial"])
        if (issuedAt == null || serial == null || issuedAt < 0 || serial < 0) {
            throw IdentityException("the signed list carries no issue time or serial: upgrade the Domovoi server first")
        }
        val signature = unb64(str(doc, "signature_v2"))
            ?: throw IdentityException("the signed list carries no signature over its serial: upgrade the Domovoi server first")
        if (!Ed25519.verify(public, manifestMessageV2(channel, manifest, issuedAt, serial), signature)) {
            throw IdentityException("the list's signature did not verify")
        }
        val files = (manifest as? JsonObject)?.mapValues { (_, v) ->
            (v as? JsonPrimitive)?.takeIf { it.isString }?.content
                ?: throw IdentityException("the list is not a path -> sha256 map")
        } ?: throw IdentityException("the list is not a path -> sha256 map")
        return Accepted(files, serial, issuedAt, manifestDigest(manifest))
    }

    /**
     * Refuse an envelope older than [last] (`check_manifest_freshness`): a
     * smaller serial, or the same serial for a different list. A newer
     * serial is always taken.
     */
    fun checkFreshness(accepted: Accepted, last: Seen?) {
        if (last == null) return
        if (accepted.serial < last.serial) {
            throw IdentityException(
                "the signed list (serial ${accepted.serial}) is older than the one this phone already accepted (serial ${last.serial})",
            )
        }
        if (accepted.serial == last.serial && last.digest != null && last.digest != accepted.digest) {
            throw IdentityException("the signed list reuses serial ${accepted.serial} for a different list")
        }
    }

    /** `MANIFEST_CONTEXT_V2 \n canonical({channel, issued_at, manifest, serial})`. */
    fun manifestMessageV2(channel: String, manifest: JsonElement, issuedAt: Long, serial: Long): ByteArray {
        val signed = JsonObject(
            mapOf(
                "channel" to JsonPrimitive(channel),
                "issued_at" to JsonPrimitive(issuedAt),
                "manifest" to manifest,
                "serial" to JsonPrimitive(serial),
            ),
        )
        return (MANIFEST_CONTEXT_V2 + "\n").toByteArray(Charsets.UTF_8) + canonicalJson(signed)
    }

    fun manifestDigest(manifest: JsonElement): String =
        MessageDigest.getInstance("SHA-256").digest(canonicalJson(manifest)).joinToString("") { "%02x".format(it) }

    /**
     * Python's `json.dumps(doc, sort_keys=True, separators=(",", ":"),
     * ensure_ascii=False).encode()`: keys in code-point order, no spaces,
     * non-ASCII as itself, only `"` `\` and control characters escaped.
     * Whole numbers only (a signed list never carries a float, and Python's
     * float spelling is not worth matching).
     */
    fun canonicalJson(element: JsonElement): ByteArray =
        StringBuilder().also { write(it, element) }.toString().toByteArray(Charsets.UTF_8)

    private fun write(out: StringBuilder, e: JsonElement) {
        when (e) {
            is JsonNull -> out.append("null")
            is JsonObject -> {
                out.append('{')
                e.keys.sortedWith(CODE_POINT_ORDER).forEachIndexed { i, k ->
                    if (i > 0) out.append(',')
                    writeString(out, k)
                    out.append(':')
                    write(out, e.getValue(k))
                }
                out.append('}')
            }
            is JsonArray -> {
                out.append('[')
                e.forEachIndexed { i, v ->
                    if (i > 0) out.append(',')
                    write(out, v)
                }
                out.append(']')
            }
            is JsonPrimitive -> when {
                e.isString -> writeString(out, e.content)
                e.booleanOrNull != null -> out.append(if (e.booleanOrNull == true) "true" else "false")
                e.longOrNull != null -> out.append(e.longOrNull)
                else -> throw IdentityException("a signed list carries a number this phone cannot spell like the server")
            }
        }
    }

    private fun writeString(out: StringBuilder, s: String) {
        out.append('"')
        for (c in s) {
            when {
                c == '"' -> out.append("\\\"")
                c == '\\' -> out.append("\\\\")
                c == '\n' -> out.append("\\n")
                c == '\r' -> out.append("\\r")
                c == '\t' -> out.append("\\t")
                c == '\b' -> out.append("\\b")
                c == '\u000c' -> out.append("\\f")
                c.code < 0x20 -> out.append("\\u%04x".format(c.code))
                else -> out.append(c)
            }
        }
        out.append('"')
    }

    private val CODE_POINT_ORDER = Comparator<String> { a, b ->
        val ia = a.codePoints().iterator()
        val ib = b.codePoints().iterator()
        while (ia.hasNext() && ib.hasNext()) {
            val d = ia.nextInt().compareTo(ib.nextInt())
            if (d != 0) return@Comparator d
        }
        ia.hasNext().compareTo(ib.hasNext())
    }

    private fun str(o: JsonObject, key: String): String? =
        (o[key] as? JsonPrimitive)?.takeIf { it.isString }?.content

    /** An integer that is not a bool and not a float spelling. */
    private fun wholeNumber(e: JsonElement?): Long? {
        val p = e as? JsonPrimitive ?: return null
        if (p.isString || p.booleanOrNull != null) return null
        if (p.content.any { it == '.' || it == 'e' || it == 'E' }) return null
        return p.longOrNull
    }

    private fun unb64(value: String?): ByteArray? {
        if (value.isNullOrEmpty() || value.length > 256) return null
        return runCatching { Base64.getDecoder().decode(value) }.getOrNull()
    }
}
