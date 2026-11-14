package com.domovoi.app.player

import com.domovoi.app.net.ApiException
import com.domovoi.app.ui.screens.music.UpNextContext
import com.domovoi.app.ui.screens.music.listenerFields
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonObjectBuilder
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject

/**
 * The phone's own Up next session (P8; the web's player.jsx startUpNext,
 * refill, setAutoplay, lessLikeThis, setUpNextListener): it starts one for
 * every kind of play, asks for more at [UpNextRules.REFILL_AT] songs left,
 * takes picks back when the session does, and follows the Autoplay switch.
 *
 * The player ([PlayerController]) owns the queue and is the [Host]; this
 * class only edits it through the host. Everything runs on the player's
 * (main) thread: [launch] starts the network part, and each answer is
 * applied only while the play it was for ([gen]) and its server are still
 * the current ones — a newer play, a dropped session or another server
 * makes it a no-op.
 */
class PhoneUpNext(
    private val api: UpNextApi,
    private val host: Host,
    private val env: Env,
    /** Runs a call's network part; each answer lands back on the player's thread. */
    private val launch: (suspend () -> Unit) -> Unit,
) {
    /** The queue, as the player holds it. */
    interface Host {
        val queue: List<PlayItem>
        val index: Int
        /** Casting to a room: the room's server carries the session. */
        val casting: Boolean
        /** Change songs in place (the same songs, new tags). */
        fun retag(f: (PlayItem) -> PlayItem)
        /** Add [items] at the end of the queue. */
        fun append(items: List<PlayItem>)
        /** Take out the songs after the one playing that [drop] names. */
        fun removeWaiting(drop: (PlayItem) -> Boolean)
        /** The session's id was just put on queued songs (the listen of
         *  the one playing should carry it from now on). */
        fun sessionArrived() {}
    }

    /** What the session needs to know about the app. */
    interface Env {
        /** The server the app talks to now (its saved address). */
        fun server(): String
        /** Paired with that server, and not a room: Up next may be asked for. */
        fun paired(): Boolean
        fun deviceId(): String
        /** The Autoplay preference (per device, default on). */
        fun autoplay(): Boolean
        fun saveAutoplay(on: Boolean)
        /** The raw "listening as" pick for [server] ([com.domovoi.app.listens.ListenerPicks]). */
        fun listenerPick(server: String): String?
        fun shared(server: String): Boolean
        /** The "for" selector picked [personId] (null: the household). */
        fun setListener(personId: String?)
    }

    private val _state = MutableStateFlow<UpNextState?>(null)
    val state: StateFlow<UpNextState?> = _state

    /** How many plays started (or dropped) a session: an answer for an older one is ignored. */
    var gen: Int = 0
        private set

    private var refilling = false

    private fun current(u: UpNextState): Boolean = u.gen == gen && u.server == env.server()

    private fun stillFor(g: Int, server: String): Boolean = g == gen && server == env.server()

    // ── Plays ────────────────────────────────────────────────────────────

    /** A new play of anything: the session before it is gone (here). The
     *  number to tag the play's songs with ([PlayItem.upNextGen]). */
    fun newPlay(): Int {
        gen += 1
        refilling = false
        _state.value = null
        return gen
    }

    /** The queue was cleared, or its server left: no session. */
    fun drop() {
        newPlay()
    }

    /** A song tapped inside a list ([context]; null: on its own, library
     *  mode). [listIds]: the list's songs, for an `ids` list. */
    internal fun startFromList(trackId: Long, context: UpNextContext?, listIds: List<Long>) {
        start(startFromListBody(trackId, context, listIds))
    }

    /** A whole list played on purpose ([listIds] in play order). */
    internal fun startWholeList(context: UpNextContext?, listIds: List<Long>) {
        if (listIds.isEmpty()) return
        start(startWholeListBody(context, listIds))
    }

    private fun start(start: JsonObject) {
        if (!env.paired()) return
        val g = gen
        val server = env.server()
        val auto = env.autoplay()
        _state.value = UpNextState(server = server, gen = g, loading = true, autoplay = auto, start = start)
        val body = buildJsonObject {
            putJsonObject("target") {
                put("kind", "device")
                put("device_id", env.deviceId())
            }
            put("start", start)
            put("autoplay", auto)
            listenerFields(env.listenerPick(server), env.shared(server))
        }
        launch {
            val s = try {
                api.start(server, body)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (!stillFor(g, server)) return@launch
                // Refused (a song the library lost, a sign-in): no Up next
                // for this play, quietly. Unreachable or failing: say Up next
                // needs the home server, and try again when the song changes.
                _state.value = if (e is ApiException && e.status in 400..499) {
                    UpNextState(server = server, gen = g, refused = true, autoplay = auto)
                } else {
                    UpNextState(server = server, gen = g, offline = true, autoplay = auto, start = start)
                }
                return@launch
            }
            if (!stillFor(g, server)) return@launch
            _state.value = UpNextState.from(s, server, g)
            // The songs this start played are the session's too (listens feed it).
            host.retag { if (it.upNextGen == g && it.sessionId == null) it.copy(sessionId = s.id) else it }
            host.append(s.picks.map { UpNextRules.pickItem(it, s.id) })
            host.sessionArrived()
            check()
        }
    }

    // ── The refill (on every change of song or queue) ────────────────────

    /**
     * Asks for more once [UpNextRules.REFILL_AT] or fewer songs are left
     * after the one playing; starts a session for a queue of library songs
     * that has none (songs added one by one); tries a failed start again on
     * a new song. Never while casting (the room refills itself), never twice
     * at once, never twice on the same song after a failure.
     */
    fun check() {
        if (host.casting || refilling) return
        val q = host.queue
        val index = host.index
        if (q.isEmpty() || index !in q.indices) return
        if (q.size - 1 - index > UpNextRules.REFILL_AT) return
        val u = _state.value
        if (u == null) {
            if (!env.autoplay() || !env.paired()) return
            val lib = q.filter { it.isLibrarySong }
            if (lib.isEmpty()) return
            val g = newPlay()
            host.retag { if (it.isLibrarySong) it.copy(upNextGen = g) else it }
            start(queueStartBody(lib.map { it.id }))
            return
        }
        if (!current(u)) return
        if (u.failedAt == index) return
        if (u.id == null) {
            if (u.offline && u.start != null && !u.loading) {
                if (u.failedAt == null) _state.value = u.copy(failedAt = index) else start(u.start)
            }
            return
        }
        if (u.loading || u.ended || u.refused) return
        if (!u.autoplay && u.mode != "list") return
        refill(u, index)
    }

    private fun refill(u: UpNextState, at: Int) {
        val id = u.id ?: return
        val g = gen
        refilling = true
        launch {
            try {
                val r = api.next(u.server, id, u.haveThrough)
                if (!stillFor(g, u.server)) return@launch
                val gone = r.removed.toSet()
                val picks = r.picks.filter { it.seq > u.haveThrough && it.seq !in gone }
                val top = r.picks.fold(u.haveThrough) { m, p -> maxOf(m, p.seq) }
                if (gone.isNotEmpty()) host.removeWaiting { it.isPickOf(id) && it.upNextSeq in gone }
                if (picks.isNotEmpty()) host.append(picks.map { UpNextRules.pickItem(it, id) })
                // Nothing new and not over: ask again when the song changes, not in a loop.
                val idle = picks.isEmpty() && !r.ended
                _state.value = _state.value?.takeIf { it.id == id && current(it) }?.copy(
                    haveThrough = top, ended = r.ended, offline = false,
                    failedAt = if (idle) at else null, mode = r.mode ?: u.mode,
                )
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (!stillFor(g, u.server)) return@launch
                val x = _state.value?.takeIf { it.id == id } ?: return@launch
                _state.value = if (e is ApiException && e.status in 400..499) {
                    // The session is gone or no longer this phone's: no more
                    // asking, and nothing shown that can't work.
                    x.copy(refused = true)
                } else {
                    x.copy(offline = true, failedAt = at)
                }
            } finally {
                if (g == gen) refilling = false
            }
            if (g == gen) check()
        }
    }

    // ── What the person does ─────────────────────────────────────────────

    /**
     * The Autoplay switch. Off: the waiting picks of a mix leave the queue
     * and nothing more is added (a list keeps the rest of itself, then
     * stops). Remembered for this device. Throws when the server didn't
     * take it (the switch still says what this phone does).
     */
    suspend fun setAutoplay(on: Boolean) {
        env.saveAutoplay(on)
        val u = _state.value
        if (u == null || !current(u)) {
            check()
            return
        }
        _state.value = u.copy(autoplay = on, ended = if (on) false else u.ended, failedAt = null)
        if (!on && u.mode != "list") host.removeWaiting { it.queueOrigin == QueueOrigin.Auto }
        try {
            val id = u.id
            if (id != null) api.patch(u.server, id, buildJsonObject { put("autoplay", on) })
        } finally {
            check()
        }
    }

    /**
     * "Less like this" on the pick at queue row [i]: that artist is out of
     * the mix for the rest of the session, their waiting picks with it. The
     * seqs gone (the row's own included); empty when [i] is no pick of the
     * session. Throws when the server didn't take it.
     */
    suspend fun lessLikeThis(i: Int): List<Int> {
        val it = host.queue.getOrNull(i) ?: return emptyList()
        val u = _state.value ?: return emptyList()
        val id = u.id ?: return emptyList()
        val seq = it.upNextSeq
        if (!it.isPickOf(id) || seq == null || !current(u)) return emptyList()
        val g = gen
        val removed = api.feedback(u.server, id, seq, "less_like_this")
        val gone = (removed + seq).toSet()
        if (stillFor(g, u.server) && _state.value?.id == id) {
            host.removeWaiting { x -> x.isPickOf(id) && x.upNextSeq in gone }
            check()
        }
        return gone.toList()
    }

    /** A pick taken out of the queue ([item], already gone): the session
     *  hears it, and any picks it takes back with it leave too. */
    fun removed(item: PlayItem) {
        val u = _state.value ?: return
        val id = u.id ?: return
        val seq = item.upNextSeq ?: return
        if (!item.isPickOf(id) || !current(u)) return
        val g = gen
        launch {
            val back = try {
                api.feedback(u.server, id, seq, "remove")
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                return@launch // best-effort, as on the web
            }
            if (!stillFor(g, u.server) || _state.value?.id != id) return@launch
            val gone = back.toSet() - seq
            if (gone.isNotEmpty()) host.removeWaiting { it.isPickOf(id) && it.upNextSeq in gone }
        }
    }

    /**
     * Whose taste the mix follows (the "for" selector): the same "listening
     * as" the rest of the player uses; the session follows it from its next
     * batch. [personId] null: the household. Throws when the server didn't
     * take it.
     */
    suspend fun setListener(personId: Long?) {
        env.setListener(personId?.toString())
        val u = _state.value ?: return
        val id = u.id ?: return
        if (!current(u)) return
        val s = api.patch(u.server, id, buildJsonObject {
            if (personId == null) put("listener_person_id", JsonNull) else put("listener_person_id", personId)
        })
        val now = _state.value
        if (s != null && now != null && now.id == id && current(now)) _state.value = now.copy(person = s.person)
    }

    // ── Casting ──────────────────────────────────────────────────────────

    /**
     * What a cast (`POST /api/music/play-tracks`) adds: this
     * phone's session goes with the queue (the room's server carries it on
     * and refills the room itself), autoplay when off, and whose mix it is.
     */
    fun castFields(b: JsonObjectBuilder) {
        val server = env.server()
        val u = _state.value
        val id = u?.id
        if (u != null && id != null && !u.ended && !u.refused && u.server == server && u.gen == gen) {
            b.put("up_next_session_id", id)
        }
        if (!env.autoplay()) b.put("autoplay", false)
        b.listenerFields(env.listenerPick(server), env.shared(server))
    }

    /**
     * Back to this phone from a room: the session the room carried on comes
     * home (`…/move` to this device). One the room no longer has (another
     * play replaced it there: 409 / 404), or a move that didn't happen, is
     * dropped here; the queue starts a new one when it is nearly over.
     */
    fun handBack() {
        val u = _state.value ?: return
        val id = u.id ?: return
        if (!current(u)) return
        val g = gen
        val target = buildJsonObject {
            put("kind", "device")
            put("device_id", env.deviceId())
        }
        launch {
            val moved = try {
                api.move(u.server, id, target)
                true
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                false
            }
            if (!stillFor(g, u.server) || _state.value?.id != id) return@launch
            if (!moved) {
                drop()
                check()
            } else {
                check()
            }
        }
    }

    // ── Back on screen ───────────────────────────────────────────────────

    /**
     * Read the session again (`GET …/{id}`): the picks still waiting there
     * replace the ones waiting here (the server may have taken some back
     * while the app was away: two early skips of one artist), and anything
     * the phone doesn't hold yet joins. A session the server no longer has
     * (404) is dropped. Quiet: any other failure changes nothing.
     */
    fun reread() {
        val u = _state.value ?: return
        val id = u.id ?: return
        if (host.casting || refilling || u.loading || !current(u)) return
        val g = gen
        launch {
            val s = try {
                api.read(u.server, id)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (stillFor(g, u.server) && _state.value?.id == id && e is ApiException && e.status == 404) {
                    drop()
                    check()
                }
                return@launch
            }
            if (!stillFor(g, u.server) || refilling) return@launch
            val now = _state.value?.takeIf { it.id == id } ?: return@launch
            val waiting = s.picks.filter { it.status == null || it.status == "queued" }
            val keep = waiting.map { it.seq }.toSet()
            val q = host.queue
            val held = q.indices.filter { i -> q[i].isPickOf(id) }.mapNotNull { q[it].upNextSeq }.toSet()
            host.removeWaiting { it.isPickOf(id) && it.upNextSeq !in keep }
            val add = waiting.filter { it.seq !in held && it.seq > now.haveThrough }
            if (add.isNotEmpty()) host.append(add.map { UpNextRules.pickItem(it, id) })
            _state.value = now.copy(
                mode = s.mode ?: now.mode, autoplay = s.autoplay, person = s.person, seed = s.seed ?: now.seed,
                ended = s.ended, haveThrough = maxOf(now.haveThrough, s.haveThroughSeq), target = s.target ?: now.target,
            )
            check()
        }
    }

    private fun PlayItem.isPickOf(id: String) = queueOrigin == QueueOrigin.Auto && sessionId == id

    companion object {
        /** `start` for a song tapped in a list (or on its own: no context). */
        internal fun startFromListBody(trackId: Long, context: UpNextContext?, listIds: List<Long>): JsonObject = buildJsonObject {
            put("track_id", trackId)
            if (context != null) {
                val ctx = if (context.kind == "ids") context.copy(trackIds = listIds.take(UpNextRules.IDS_CAP)) else context
                put("context", ctx.toJson())
            }
        }

        /** `start` for a whole list played on purpose (continue mode). An
         *  `ids` list sends its last [UpNextRules.IDS_CAP] songs (the web's
         *  playList), what a continue session seeds from. */
        internal fun startWholeListBody(context: UpNextContext?, listIds: List<Long>): JsonObject = buildJsonObject {
            val base = context ?: UpNextContext("ids", name = "this list")
            val ctx = if (base.kind == "ids") base.copy(trackIds = listIds.takeLast(UpNextRules.IDS_CAP)) else base
            put("context", ctx.toJson())
            put("whole_list", true)
            put("track_id", listIds.first())
        }

        /** `start` for a queue of library songs added one by one. */
        fun queueStartBody(ids: List<Long>): JsonObject = buildJsonObject {
            put("context", UpNextContext("ids", name = "this queue", trackIds = ids.takeLast(UpNextRules.IDS_CAP)).toJson())
            put("whole_list", true)
            put("track_id", ids.first())
        }
    }
}
