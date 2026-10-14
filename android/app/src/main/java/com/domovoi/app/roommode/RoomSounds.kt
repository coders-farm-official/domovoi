package com.domovoi.app.roommode

import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import java.io.File
import java.io.IOException
import kotlin.random.Random

/**
 * The room's sound clips — the wake greetings and the canned lines the
 * server renders (`/v1/sounds`, e.g. `greetings/greet_<hash>.mp3`,
 * `network_issues.mp3`) — mirrored into `files/room/sounds/` the way a Pi
 * mirrors them (satellite/sound_sync.py): only from a list our server
 * signed (`satellite-sounds`, kept per voice; the room uses the server's
 * default voice), every body checked against it, clips no longer listed
 * removed. On connect, and on `sounds_changed`.
 */
class RoomSounds(private val dir: File, private val store: RoomModels.ModelBook) {
    /** Mirror the list. Returns how many clips were downloaded; throws
     *  [IOException] / [RoomIdentity.IdentityException] on failure (the
     *  cache is kept as it was). Blocking. */
    fun sync(http: OkHttpClient, coreUrl: String, fingerprint: String): Int {
        val body = RoomModels.get(http, "$coreUrl/v1/sounds/manifest.sig").toString(Charsets.UTF_8)
        val accepted = RoomIdentity.verifyManifestEnvelope(Json.parseToJsonElement(body), RoomIdentity.SOUNDS_CHANNEL, fingerprint)
        // The default voice's record (`sounds_record(None)`); the channel's
        // own record is its floor until that has been written.
        val record = "${RoomIdentity.SOUNDS_CHANNEL}@"
        RoomIdentity.checkFreshness(accepted, store.seen(record) ?: store.seen(RoomIdentity.SOUNDS_CHANNEL))
        dir.mkdirs()
        var downloaded = 0
        for ((rel, sha) in accepted.manifest) {
            if (!safeRel(rel)) continue
            val dest = File(dir, rel)
            if (dest.isFile && RoomModels.sha256(dest) == sha) continue
            val bytes = RoomModels.get(http, "$coreUrl/v1/sounds/" + rel.split('/').joinToString("/") { encodeSegment(it) }, limit = MAX_CLIP_BYTES)
            if (RoomModels.sha256(bytes) != sha) continue
            dest.parentFile?.mkdirs()
            val tmp = File(dest.parentFile, ".${dest.name}.part")
            tmp.writeBytes(bytes)
            if (!tmp.renameTo(dest)) { dest.delete(); tmp.renameTo(dest) }
            downloaded += 1
        }
        store.remember(record, accepted)
        prune(accepted.manifest.keys)
        return downloaded
    }

    /** A random greeting clip (a funny one one time in ten), or null. */
    fun pickGreeting(random: Random = Random.Default): File? {
        val clips = File(dir, "greetings").listFiles { f -> f.isFile && f.name.startsWith("greet_") && f.name.endsWith(".mp3") }
            ?.toList().orEmpty()
        if (clips.isEmpty()) return null
        val (funny, plain) = clips.partition { it.name.startsWith("greet_funny_") }
        val pool = if (funny.isNotEmpty() && random.nextDouble() < FUNNY_CHANCE) funny else plain.ifEmpty { funny }
        return pool.random(random)
    }

    /** A canned clip by name (`network_issues.mp3`), if mirrored. */
    fun clip(rel: String): File? = File(dir, rel).takeIf { safeRel(rel) && it.isFile }

    private fun prune(keep: Set<String>) {
        dir.walkTopDown().filter { it.isFile && it.name.endsWith(".mp3") }.forEach { f ->
            val rel = f.relativeTo(dir).invariantSeparatorsPath
            if (rel !in keep) f.delete()
        }
    }

    companion object {
        const val FUNNY_CHANCE = 0.1
        const val MAX_CLIP_BYTES = 4L * 1024 * 1024

        /** The Pi's `_safe_rel`: relative, no `..`, no backslash. */
        fun safeRel(rel: String): Boolean {
            if (rel.isEmpty() || rel.startsWith("/") || '\\' in rel) return false
            return rel.split('/').all { it.isNotEmpty() && it != ".." && it != "." }
        }

        private fun encodeSegment(s: String): String =
            java.net.URLEncoder.encode(s, "UTF-8").replace("+", "%20")
    }
}
