package com.domovoi.app.player

import com.domovoi.app.net.IdentityGate
import com.domovoi.app.net.ServerIdentity

/*
 * Which server the phone's queue came from (2026-10-12, Kamron's phone: he
 * played music from one Domovoi, connected the app to a fresh one at
 * another address, and the mini player still showed the first one's song).
 *
 * A library track, a station, an episode or a book is a SERVER path
 * (`/api/music/library/12/audio`): id 12 on another server is another
 * song, or nothing. So what the phone queued from a server belongs to that
 * server — by its identity (the key the phone pinned for it, its
 * fingerprint), not its address — and a move to a different server stops
 * and clears it (PlayerController.serverChanged). The same server again
 * (at another address, or once its identity is learned) keeps it all, and
 * so does the local/server mode switch, which does not change servers.
 * The phone's own songs (PlayKind.Device) belong to no server and stay, and
 * so do books saved on the phone (PlayItem.savedKey): their files are here.
 */

/** A server as the player tells servers apart: its pinned identity's
 *  [fingerprint] when the phone has one, else its [address] (the
 *  scheme://host:port the identity pins are keyed by). */
data class ServerScope(val address: String?, val fingerprint: String?) {

    /** Same server: the same identity when both are known (whatever the
     *  address), else the same address. No server is never the same. */
    fun isSameServer(other: ServerScope): Boolean =
        if (fingerprint != null && other.fingerprint != null) fingerprint == other.fingerprint
        else address != null && address == other.address

    /** This server as [other] — the same server — now knows it: a new
     *  address, an identity pinned since. */
    fun merge(other: ServerScope): ServerScope =
        ServerScope(other.address ?: address, other.fingerprint ?: fingerprint)

    companion object {
        /** The app is connected to no server (it was forgotten). */
        val NONE = ServerScope(null, null)

        /** The saved server [serverUrl] with the identity pinned for it. */
        fun of(serverUrl: String?, pin: ServerIdentity.Pin?): ServerScope = ServerScope(
            IdentityGate.pinKey(serverUrl?.takeIf { it.isNotBlank() }),
            pin?.fingerprint?.takeIf { it.isNotBlank() },
        )
    }
}

/** What a move to a different server leaves of the queue: the phone's own
 *  songs ([kept], in order) and where the play head goes among them. */
data class ServerSwitchPlan(
    val kept: List<PlayItem>,
    val keptIndex: Int,
    /** The old server's entries, by queue position, highest first (the
     *  order they can be taken out of a player in). */
    val removed: List<Int>,
    /** The entry playing now was the old server's: playback stops. */
    val currentGoes: Boolean,
)

object ServerSwitch {
    /** Media that came from a server, by path; everything but the phone's own. */
    fun fromServer(item: PlayItem): Boolean = item.kind != PlayKind.Device && item.savedKey == null

    fun plan(queue: List<PlayItem>, index: Int): ServerSwitchPlan {
        val kept = queue.filterNot(::fromServer)
        val removed = queue.indices.filter { fromServer(queue[it]) }.sortedDescending()
        val current = queue.getOrNull(index)
        val currentGoes = current == null || fromServer(current)
        // The phone's songs before the play head: the current one's place
        // among them, or (when the current one goes) the next one's.
        val before = queue.take(index.coerceIn(0, queue.size)).count { !fromServer(it) }
        val keptIndex = if (kept.isEmpty()) 0 else before.coerceAtMost(kept.lastIndex)
        return ServerSwitchPlan(kept, keptIndex, removed, currentGoes)
    }
}
