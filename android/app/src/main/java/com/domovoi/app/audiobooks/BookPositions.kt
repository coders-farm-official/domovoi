package com.domovoi.app.audiobooks

import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.player.ServerScope
import com.domovoi.app.ui.components.parseInstant
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import java.io.File

/*
 * One place per book on the phone (owner decision 2026-10-20): streaming and
 * offline play read and write the same position, it is kept on the phone,
 * and it goes up to the server's slot for this phone (device x listener,
 * `POST /api/audiobooks/{id}/position`) whenever the book's server is the
 * connected one. Rooms keep their own places; the "continue from the
 * kitchen" offer reads those ([ContinueRules]).
 *
 * The server's slot for this phone is written by this phone only, so it
 * moves without the phone when the phone forgot (a reinstall, cleared data)
 * or another install shares the device id. [PositionRules] decides which
 * place stands: the phone's when only the phone moved, the server's when
 * only the server did, and the newer of the two when both did.
 */

/** The phone's place in one book, for one listener. */
@Serializable
data class LocalBookPosition(
    val bookId: Long,
    val serverAddress: String? = null,
    val serverFingerprint: String? = null,
    val personId: String? = null,
    val positionSec: Double,
    val speed: Float = 1f,
    /** Phone clock, when the place was last set here. */
    val updatedAtMs: Long,
    /** Set here since the server last had it. */
    val dirty: Boolean = true,
    /** The server's `updated_at` for this slot when the two last agreed. */
    val serverStamp: String? = null,
) {
    val server: ServerScope get() = ServerScope(serverAddress, serverFingerprint)

    fun isFor(bookId: Long, server: ServerScope, personId: String?): Boolean =
        this.bookId == bookId && this.personId == personId && this.server.isSameServer(server)
}

/** The server's slot for this phone. [updatedAt] null: the server has none. */
data class ServerPosition(val positionSec: Double, val speed: Float, val updatedAt: String?)

/** Which place stands after comparing the two. */
data class PositionResolution(
    val positionSec: Double,
    val speed: Float,
    /** The phone's place is newer: send it to the server. */
    val upload: Boolean,
    /** The server's place is newer: the phone takes it. */
    val adopted: Boolean,
)

object PositionRules {
    /** Pure. Null when neither side has a place. */
    fun resolve(local: LocalBookPosition?, server: ServerPosition?): PositionResolution? {
        val serverHas = server != null && server.updatedAt != null
        if (local == null) {
            if (!serverHas) return null
            return PositionResolution(server!!.positionSec, server.speed, upload = false, adopted = true)
        }
        if (!serverHas) {
            return PositionResolution(local.positionSec, local.speed, upload = local.positionSec > 0, adopted = false)
        }
        server!!
        val serverMoved = server.updatedAt != local.serverStamp
        val phoneWins = when {
            !serverMoved -> true
            !local.dirty -> false
            else -> {
                // Both moved: the newer one. The two clocks are the phone's
                // and the server's; on a home network they agree well enough
                // for "which did I touch last".
                val serverMs = parseInstant(server.updatedAt)?.toEpochMilli() ?: Long.MIN_VALUE
                local.updatedAtMs >= serverMs
            }
        }
        return if (phoneWins) {
            PositionResolution(local.positionSec, local.speed, upload = local.dirty, adopted = false)
        } else {
            PositionResolution(server.positionSec, server.speed, upload = false, adopted = true)
        }
    }
}

/** The phone's places, in one small JSON file. */
class PositionStore(private val file: File) {
    private val lock = Any()

    fun all(): List<LocalBookPosition> = synchronized(lock) {
        if (!file.isFile) return emptyList()
        runCatching { DomovoiJson.decodeFromString(ListSerializer(LocalBookPosition.serializer()), file.readText()) }
            .getOrDefault(emptyList())
    }

    fun get(bookId: Long, server: ServerScope, personId: String?): LocalBookPosition? =
        all().firstOrNull { it.isFor(bookId, server, personId) }

    fun put(p: LocalBookPosition) = synchronized(lock) {
        val kept = all().filterNot { it.isFor(p.bookId, p.server, p.personId) }
        // Newest first, and a bounded file: a phone that has listened to a
        // thousand books keeps the last 500 places.
        val next = (listOf(p) + kept).sortedByDescending { it.updatedAtMs }.take(MAX)
        file.parentFile?.mkdirs()
        val tmp = File(file.path + ".tmp")
        tmp.writeText(DomovoiJson.encodeToString(ListSerializer(LocalBookPosition.serializer()), next))
        if (!tmp.renameTo(file)) {
            file.delete()
            tmp.renameTo(file)
        }
    }

    private companion object {
        const val MAX = 500
    }
}

/** The connected server's slot for this phone; a fake in the tests. */
interface PositionRemote {
    /** The server the remote talks to now. */
    val server: ServerScope

    suspend fun get(bookId: Long): ServerPosition

    suspend fun put(bookId: Long, positionSec: Double, speed: Float)
}

/**
 * The phone's places and their sync. [remote] answers for whichever server
 * the app is connected to (null: none); a book's place goes only to the
 * server the book came from.
 */
class PositionSync(
    private val store: PositionStore,
    private val remote: () -> PositionRemote?,
    private val person: () -> String?,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val pushing = Mutex()

    private fun remoteFor(server: ServerScope): PositionRemote? = remote()?.takeIf { it.server.isSameServer(server) }

    /** The phone's place, as it stands here. */
    fun local(bookId: Long, server: ServerScope): LocalBookPosition? = store.get(bookId, server, person())

    /** Set the place here; it goes up on the next [push]. */
    fun record(bookId: Long, server: ServerScope, positionSec: Double, speed: Float) {
        val before = store.get(bookId, server, person())
        store.put(
            LocalBookPosition(
                bookId, server.address ?: before?.serverAddress, server.fingerprint ?: before?.serverFingerprint,
                person(), positionSec.coerceAtLeast(0.0), speed, clock(), dirty = true, serverStamp = before?.serverStamp,
            ),
        )
    }

    /**
     * Where to start [bookId]: the phone's place and the server's, compared
     * ([PositionRules]) when the book's server is connected, else the
     * phone's alone. The winner is kept here, and sent up when it is the
     * phone's. Never throws: a server that can't be read leaves the phone's.
     */
    suspend fun open(bookId: Long, server: ServerScope): Pair<Double, Float> {
        val local = store.get(bookId, server, person())
        val r = remoteFor(server) ?: return (local?.positionSec ?: 0.0) to (local?.speed ?: 1f)
        val theirs = runCatching { r.get(bookId) }.getOrNull()
            ?: return (local?.positionSec ?: 0.0) to (local?.speed ?: 1f)
        val res = PositionRules.resolve(local, theirs) ?: return 0.0 to 1f
        if (res.adopted) {
            store.put(
                LocalBookPosition(
                    bookId, server.address, server.fingerprint, person(), res.positionSec, res.speed,
                    clock(), dirty = false, serverStamp = theirs.updatedAt,
                ),
            )
        } else if (res.upload) {
            push(bookId, server)
        }
        return res.positionSec to res.speed
    }

    /** Send the phone's place for [bookId] when it is newer and the book's
     *  server is connected. False when nothing went (offline, or nothing new). */
    suspend fun push(bookId: Long, server: ServerScope): Boolean = pushing.withLock {
        val p = store.get(bookId, server, person()) ?: return false
        if (!p.dirty) return false
        val r = remoteFor(p.server) ?: return false
        runCatching {
            r.put(bookId, p.positionSec, p.speed)
            // What the server now holds for the slot, so the next compare
            // knows the server has not moved since.
            val stamp = runCatching { r.get(bookId).updatedAt }.getOrNull()
            val now = store.get(bookId, server, person())
            // A newer place recorded while this was on its way stays dirty.
            if (now != null && now.updatedAtMs == p.updatedAtMs) store.put(now.copy(dirty = false, serverStamp = stamp))
            true
        }.getOrDefault(false)
    }

    /** Send every place set here while away, for books of [server]
     *  (on reconnect). Returns how many went. */
    suspend fun pushAll(server: ServerScope): Int {
        if (remoteFor(server) == null) return 0
        val mine = store.all().filter { it.dirty && it.personId == person() && it.server.isSameServer(server) }
        return mine.count { push(it.bookId, it.server) }
    }
}

/** The "continue from the kitchen" offer. */
data class ContinueOffer(val label: String, val positionSec: Double, val isRoom: Boolean)

object ContinueRules {
    /** A room (or another device) counts as further only past this lead. */
    const val LEAD_SEC = 60.0

    /** Pure. The place to offer over the phone's own [mineSec]: of the
     *  others that are further by [LEAD_SEC], the one used most recently
     *  (then the furthest). Null when none got further. */
    fun pick(mineSec: Double, others: List<OtherPosition>): ContinueOffer? {
        val ahead = others.filter { it.position_sec > mineSec + LEAD_SEC && (it.room != null || it.device_label != null) }
        val best = ahead.maxWithOrNull(
            compareBy<OtherPosition>({ parseInstant(it.updated_at)?.toEpochMilli() ?: Long.MIN_VALUE }, { it.position_sec }),
        ) ?: return null
        val room = best.room?.takeIf { it.isNotBlank() }
        return ContinueOffer(room ?: best.device_label.orEmpty(), best.position_sec, isRoom = room != null)
    }

    /** "Continue from the kitchen (2 h 13 m)". */
    fun text(offer: ContinueOffer): String =
        "continue from " + (if (offer.isRoom) "the " else "") + offer.label + " (" + hm(offer.positionSec) + ")"

    /** "2 h 13 m", "13 m", "40 s". */
    fun hm(sec: Double): String {
        val s = sec.coerceAtLeast(0.0).toLong()
        val h = s / 3600
        val m = (s % 3600) / 60
        return when {
            h > 0 -> "$h h $m m"
            m > 0 -> "$m m"
            else -> "$s s"
        }
    }
}
