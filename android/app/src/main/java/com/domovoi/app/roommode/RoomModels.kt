package com.domovoi.app.roommode

import android.util.Log
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.IOException
import java.security.MessageDigest

/**
 * The room's listening models, from its own server, never from the app:
 *
 * - openWakeWord's melspectrogram and embedding models, the Silero voice
 *   detector and the stock wake-word model come from `/v1/wake-base` —
 *   the core's satellite media cache, the files a prepared Pi card
 *   carries. Each is taken only when the signed list names it (our
 *   server's key) AND its digest and size are the pins below, the same
 *   pins the core holds (domovoi/satellite_media/fetchers.py
 *   OWW_MODEL_FILES; RoomModelsTest checks the two agree).
 * - a wake word the dashboard trained and pushed (`set_wake_word`) comes
 *   from `/v1/wake-models`, signed, checked against the list's digest.
 * - a room with several words (1.4.0, [WakeWords]) takes each the same
 *   way; an extra word the server lacks, or whose file does not check out,
 *   is left out, never the main word ([Outcome.Ready.skipped]).
 *
 * Nothing is bundled in the APK: the pretrained wake-word models are not
 * under a licence the app may ship. Files live in the app's private
 * `files/room/models/`, written atomically, re-hashed before every load.
 */
class RoomModels(private val dir: File, private val book: ModelBook) {
    /** What the room remembers about the lists and models it took
     *  (RoomStore; an in-memory one in the tests). */
    interface ModelBook {
        /** The last list accepted under [record] (freshness). */
        fun seen(record: String): RoomIdentity.Seen?

        fun remember(record: String, accepted: RoomIdentity.Accepted)

        /** The digest the signed list gave a trained model when it was taken. */
        fun trainedSha(name: String): String?

        fun setTrainedSha(name: String, sha: String)
    }

    sealed class Outcome {
        /** Every file the main word needs is here and checked; [files]
         *  lists the words that can load (main word first). [skipped]:
         *  extra words whose model the server does not have, or did not
         *  check out (the room listens without them). */
        data class Ready(val files: Files, val skipped: List<String> = emptyList()) : Outcome()

        /** The server does not have [names] (its satellite media cache was
         *  never filled, or the pushed model is gone). */
        data class Missing(val names: List<String>) : Outcome()

        /** Could not ask (offline, a refused signature): [reason]. */
        data class Failed(val reason: String) : Outcome()
    }

    /** One wake word's model file. */
    data class WakeFile(val slug: String, val file: File)

    /** What the room loads: the shared front end, the voice detector, and
     *  one model per wake word ([words], main word first, never empty). */
    data class Files(val melspectrogram: File, val embedding: File, val vad: File, val words: List<WakeFile>) {
        init {
            require(words.isNotEmpty()) { "a room loads at least its main word" }
        }

        /** The main word's model and slug. */
        val wake: File get() = words[0].file
        val wakeKey: String get() = words[0].slug
        val slugs: List<String> get() = words.map { it.slug }
    }

    /** What the room loads for [wakeWord] alone, if it is all here and intact. */
    fun local(wakeWord: String): Files? = local(listOf(wakeWord))

    /**
     * What the room loads for [wakeWords] (main word first), if the base
     * models and the main word's are all here and intact; an extra word
     * whose model is not here (or no longer hashes right) is left out.
     */
    fun local(wakeWords: List<String>): Files? {
        val main = wakeWords.firstOrNull() ?: return null
        val base = BASE.map { File(dir, it) }
        if (!base.all { verifiedBase(it) }) return null
        val words = ArrayList<WakeFile>()
        for (w in wakeWords.distinct()) {
            val wakeFile = File(dir, wakeFileName(w))
            val ok = if (stockFile(w) != null) verifiedBase(wakeFile) else trainedModelIntact(wakeFile.name)
            if (!ok) {
                if (w == main) return null
                continue
            }
            words += WakeFile(w, wakeFile)
        }
        return Files(base[0], base[1], base[2], words)
    }

    /** A trained model is loaded only when it still hashes to what its
     *  signed list said when it was taken (re-hashed at every load). */
    fun trainedModelIntact(name: String): Boolean {
        val file = File(dir, name)
        val want = book.trainedSha(name) ?: return false
        return file.isFile && sha256(file) == want
    }

    /** [sync] for [wakeWord] alone. */
    fun sync(http: OkHttpClient, coreUrl: String, fingerprint: String, wakeWord: String): Outcome =
        sync(http, coreUrl, fingerprint, listOf(wakeWord))

    /**
     * Fetch what is missing or changed for [wakeWords] (main word first)
     * from the core at [coreUrl] (pinned [fingerprint]) over [http].
     * Blocking. The main word is what it always was: missing or refused, the
     * room says so. An extra word the server does not have, or whose file
     * does not check out, is only left out ([Outcome.Ready.skipped]).
     */
    fun sync(http: OkHttpClient, coreUrl: String, fingerprint: String, wakeWords: List<String>): Outcome {
        val words = wakeWords.distinct()
        val main = words.firstOrNull() ?: return Outcome.Failed("no wake word")
        dir.mkdirs()
        return try {
            val baseList = signedList(http, "$coreUrl/v1/wake-base/manifest.sig", RoomIdentity.WAKE_BASE_CHANNEL, fingerprint)
            val mainStock = stockFile(main)
            val wanted = BASE + listOfNotNull(mainStock)
            val missing = wanted.filter { it !in baseList }
            for (name in wanted - missing.toSet()) takeBase(http, coreUrl, baseList, name)
            // Extra stock words: from the same list, each on its own.
            for (w in words.drop(1)) {
                val name = stockFile(w) ?: continue
                if (name in wanted || name !in baseList) continue
                try {
                    takeBase(http, coreUrl, baseList, name)
                } catch (e: IOException) {
                    Log.w(TAG, "extra wake word $w: ${e.message}")
                }
            }
            val trained = words.filter { stockFile(it) == null }
            if (trained.isNotEmpty()) {
                val url = "$coreUrl/v1/wake-models/manifest.sig"
                val list = if (mainStock == null) {
                    signedList(http, url, RoomIdentity.WAKE_MODELS_CHANNEL, fingerprint)
                } else {
                    // Only extra words need it: a list the room can't get
                    // leaves them out, never the main word.
                    try {
                        signedList(http, url, RoomIdentity.WAKE_MODELS_CHANNEL, fingerprint)
                    } catch (e: IOException) {
                        Log.w(TAG, "extra wake words: ${e.message}")
                        emptyMap()
                    } catch (e: RoomIdentity.IdentityException) {
                        Log.w(TAG, "extra wake words: ${e.message}")
                        emptyMap()
                    }
                }
                for (w in trained) {
                    val name = wakeFileName(w)
                    val sha = list[name]
                    if (sha == null) {
                        if (w == main) return Outcome.Missing(missing + name)
                        continue
                    }
                    if (w == main) {
                        takeTrained(http, coreUrl, name, sha)
                    } else {
                        try {
                            takeTrained(http, coreUrl, name, sha)
                        } catch (e: IOException) {
                            Log.w(TAG, "extra wake word $w: ${e.message}")
                        }
                    }
                }
            }
            if (missing.isNotEmpty()) return Outcome.Missing(missing)
            val files = local(words) ?: return Outcome.Failed("the models did not check out after the download")
            Outcome.Ready(files, words.filter { it !in files.slugs })
        } catch (e: RoomIdentity.IdentityException) {
            Outcome.Failed(e.message ?: "a signed list was refused")
        } catch (e: IOException) {
            Outcome.Failed(e.message ?: e.javaClass.simpleName)
        }
    }

    /** A base or stock file the signed list names, against the app's pin. */
    private fun takeBase(http: OkHttpClient, coreUrl: String, baseList: Map<String, String>, name: String) {
        val pin = PINS.getValue(name)
        if (baseList.getValue(name) != pin.first) throw IOException("the server lists $name with a digest the app does not pin")
        val target = File(dir, name)
        if (verifiedBase(target)) return
        download(http, "$coreUrl/v1/wake-base/$name", target, pin.first, pin.second)
    }

    /** A trained model, against its signed list's digest. */
    private fun takeTrained(http: OkHttpClient, coreUrl: String, name: String, sha: String) {
        val target = File(dir, name)
        if (!(target.isFile && sha256(target) == sha)) download(http, "$coreUrl/v1/wake-models/$name", target, sha, null)
        book.setTrainedSha(name, sha)
    }

    private fun signedList(http: OkHttpClient, url: String, channel: String, fingerprint: String): Map<String, String> {
        val body = get(http, url).toString(Charsets.UTF_8)
        val accepted = RoomIdentity.verifyManifestEnvelope(Json.parseToJsonElement(body), channel, fingerprint)
        RoomIdentity.checkFreshness(accepted, book.seen(channel))
        book.remember(channel, accepted)
        return accepted.manifest
    }

    private fun download(http: OkHttpClient, url: String, target: File, sha: String, size: Long?) {
        val bytes = get(http, url, limit = MAX_MODEL_BYTES)
        if (size != null && bytes.size.toLong() != size) throw IOException("${target.name} is ${bytes.size} bytes, not $size")
        if (sha256(bytes) != sha) throw IOException("${target.name} is not the file the signed list names")
        val tmp = File(target.parentFile, ".${target.name}.part")
        tmp.writeBytes(bytes)
        if (!tmp.renameTo(target)) {
            target.delete()
            if (!tmp.renameTo(target)) throw IOException("could not save ${target.name}")
        }
    }

    private fun verifiedBase(file: File): Boolean {
        val pin = PINS[file.name] ?: return false
        return file.isFile && file.length() == pin.second && sha256(file) == pin.first
    }

    companion object {
        private const val TAG = "RoomModels"

        /** The three networks every room runs. */
        val BASE = listOf("melspectrogram.onnx", "embedding_model.onnx", "silero_vad.onnx")

        /** domovoi/satellite_media/fetchers.py OWW_MODEL_FILES, the .onnx rows. */
        val PINS: Map<String, Pair<String, Long>> = mapOf(
            "alexa_v0.1.onnx" to ("6ff566a01d12670e8d9e3c59da32651db1575d17272a601b7f8a39283dfbae3e" to 854246L),
            "embedding_model.onnx" to ("70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f" to 1326578L),
            "hey_jarvis_v0.1.onnx" to ("94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb" to 1271370L),
            "hey_mycroft_v0.1.onnx" to ("c2a311e8fa1338de89c31b3b46dc4dffd4af2f9a8d6ddead48893c2d301b1f18" to 857691L),
            "hey_rhasspy_v0.1.onnx" to ("5a9b3ed3be2910e35780e097905aa9f35a9c10038df47914cf2b3ec4d670f6ea" to 204081L),
            "melspectrogram.onnx" to ("ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f" to 1087958L),
            "silero_vad.onnx" to ("a35ebf52fd3ce5f1469b2a36158dba761bc47b973ea3382b3186ca15b1f5af28" to 1807522L),
            "timer_v0.1.onnx" to ("371e44535470a29248b3b8f1bbbbaf2525c86417fd8f75c67fcf02ae0b9626df" to 1742475L),
            "weather_v0.1.onnx" to ("8441da8e746899e8d969528d5bad5651cdd563079c05962788f77753041f60e7" to 1149158L),
        )

        /** No model is anywhere near this; a body over it is refused. */
        const val MAX_MODEL_BYTES = 32L * 1024 * 1024

        /** The stock model for a wake word openWakeWord ships ("hey_jarvis"
         *  → hey_jarvis_v0.1.onnx), or null for a trained one. */
        fun stockFile(wakeWord: String): String? = "${wakeWord}_v0.1.onnx".takeIf { it in PINS }

        /** The file a wake word loads: the stock model, or `<slug>.onnx`. */
        fun wakeFileName(wakeWord: String): String = stockFile(wakeWord) ?: "$wakeWord.onnx"

        /** A slug the core may push: the model file stem it serves. */
        fun isSlug(value: String?): Boolean = value != null && Regex("^[a-z0-9][a-z0-9_]{0,63}$").matches(value)

        fun sha256(file: File): String = file.inputStream().use { input ->
            val md = MessageDigest.getInstance("SHA-256")
            val buf = ByteArray(1 shl 16)
            while (true) {
                val n = input.read(buf)
                if (n < 0) break
                md.update(buf, 0, n)
            }
            md.digest().joinToString("") { "%02x".format(it) }
        }

        fun sha256(bytes: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

        /** GET [url] on [http]: the body, or IOException (any non-200 too). */
        fun get(http: OkHttpClient, url: String, limit: Long = 4L * 1024 * 1024): ByteArray {
            http.newCall(Request.Builder().url(url).build()).execute().use { r ->
                if (r.code != 200) throw IOException("${r.code} from ${url.substringBefore('?')}")
                val body = r.body ?: throw IOException("no body from $url")
                val len = body.contentLength()
                if (len > limit) throw IOException("$url is too large")
                val source = body.source()
                val out = java.io.ByteArrayOutputStream()
                val buf = ByteArray(1 shl 16)
                val stream = source.inputStream()
                while (true) {
                    val n = stream.read(buf)
                    if (n < 0) break
                    out.write(buf, 0, n)
                    if (out.size() > limit) throw IOException("$url is too large")
                }
                return out.toByteArray()
            }
        }
    }
}
