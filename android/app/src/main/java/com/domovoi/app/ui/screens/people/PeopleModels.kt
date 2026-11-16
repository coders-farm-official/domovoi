package com.domovoi.app.ui.screens.people

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import com.domovoi.app.net.failureText
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

// ---------------------------------------------------------------------------
// Models — mirror web/backend/schemas.py (Person / Memory / Favorite /
// Session / ConversationTurn / DenylistEntry). Nullable + defaults because
// the backend evolves.
// ---------------------------------------------------------------------------

@Serializable
internal data class Person(
    val id: Long = 0,
    val name: String = "",
    val created_at: String? = null,
    val last_seen_at: String? = null,
    val notes: String? = null,
    val voice_profile_count: Int = 0,
    val presence_tier: String? = null,
    /** How many OTHER people have this name (case and spacing aside). Voice
     *  enrolment makes a second person with a name only when asked to (a
     *  "no" to "I already know someone called X. Is that you?"); people who
     *  share one are never guessed between when the name is said, so the
     *  page flags them until one is renamed or removed. */
    val namesakes: Int = 0,
    /** How many phones and browsers are claimed as this person ("this is
     *  me", `PUT /api/devices/{id}/person`). */
    val device_count: Int = 0,
    /** Their voice samples went with the speaker model 1.3.0 replaced (V061)
     *  and they have none of the current one: nobody is named by a voice until
     *  it says "my name is …" again. Absent (false) on an older server. */
    val voice_relearn: Boolean = false,
)

// ---------------------------------------------------------------------------
// One name, one person (server: handlers/voice_profile.py, 2026-10-15) — the
// web People page's PersonNamesakes and rename, for the phone.
// ---------------------------------------------------------------------------

/** The longest name the server takes (PersonRename, the enrolment limit). */
internal const val PERSON_NAME_MAX = 60

/** A typed name as the server keeps it: trimmed, whitespace runs as one space. */
internal fun cleanPersonName(raw: String): String =
    raw.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")

/** Whether "save" means something: a non-empty name within the limit that
 *  isn't the person's name already. */
internal fun canRename(person: Person, raw: String): Boolean {
    val name = cleanPersonName(raw)
    return name.isNotEmpty() && name.length <= PERSON_NAME_MAX && name != person.name
}

/** Roster subtitle prefix that tells people with one name apart. */
internal fun rosterNameTag(p: Person): String = if (p.namesakes > 0) "#${p.id} · same name · " else ""

/** What the profile says about a shared name, or null when nobody shares it. */
internal fun namesakeNote(p: Person): String? {
    if (p.namesakes <= 0) return null
    val n = p.name
    return "${p.namesakes + 1} people are called $n. When someone says “my name is $n”, " +
        "Domovoi won’t guess which one they are. If these are the same person, forget the " +
        "extra one; they can then say “my name is $n” wherever Domovoi doesn’t know their " +
        "voice yet. If they are different people, rename one of them."
}

/** Toast for a refused rename: the server's own reason for a name someone else
 *  has (409), the usual failure text for anything else. */
internal fun renameFailureText(e: Throwable): String {
    if (e is ApiException && e.status == 409) {
        val detail = runCatching {
            ((DomovoiJson.parseToJsonElement(e.body) as? JsonObject)?.get("detail") as? JsonPrimitive)
                ?.content
        }.getOrNull()
        if (!detail.isNullOrBlank()) return detail
    }
    return failureText("rename", e)
}

// ---------------------------------------------------------------------------
// Rooms that have learned this voice (GET /api/people/{id}/voice-rooms, 1.3.0)
// — the web People page's PersonVoiceRooms. Every room compares a voice with
// all of the person's samples, so any room can name them; a room that has
// learned the voice itself (a sample recorded there) names it more reliably.
// The chips say which rooms have learned it, never which rooms "know" the
// person (owner device check, 1.3.0: "not yet" read as "this room can't
// recognize me" right after it had). "my name is …" said in a room, then
// "yes", adds a sample there. Read-only: the phone records nothing.
// ---------------------------------------------------------------------------

/** One room and whether it has learned this person's voice (server VoiceRoom):
 *  [current] a satellite sample of the current speaker model. Since V061 that
 *  is the only kind that counts, and the server no longer sends [legacy]. It
 *  is still read because a 1.3.0 build from before V061 (main between
 *  67dea58a and 79acc157, never released) sent `legacy: true` for a room
 *  whose only sample was pre-1.3.0, and on that server such a room did know
 *  the voice; calling it "not yet learned" there would be wrong. */
@Serializable
internal data class VoiceRoom(
    val room_id: String = "",
    val current: Boolean = false,
    val legacy: Boolean = false,
) {
    val known: Boolean get() = current || legacy
}

internal const val VOICE_ROOMS_LABEL = "rooms that have learned this voice"
internal const val VOICE_ROOM_LEARNED = "learned"
internal const val VOICE_ROOM_NOT_YET = "not yet learned"

/** The rows of a voice-rooms answer: anything that isn't a room (not an
 *  object, no room name) is dropped; a missing flag is false. */
internal fun parseVoiceRooms(json: JsonElement): List<VoiceRoom> =
    ((json as? JsonArray) ?: return emptyList()).mapNotNull { row ->
        if (row !is JsonObject) return@mapNotNull null
        runCatching { DomovoiJson.decodeFromJsonElement(VoiceRoom.serializer(), row) }
            .getOrNull()
            ?.takeIf { it.room_id.isNotBlank() }
    }

/**
 * Reads the rooms with [get]. A server from before 1.3.0 has no such route
 * (404): no rooms, so the section stays hidden and nothing is said. Any other
 * failure is null — keep what is shown (nothing, on a first load) rather than
 * flash the section away; it is a hint, so it raises no toast of its own.
 */
internal suspend fun loadVoiceRooms(get: suspend () -> JsonElement): List<VoiceRoom>? =
    try {
        parseVoiceRooms(get())
    } catch (e: CancellationException) {
        throw e
    } catch (e: ApiException) {
        if (e.status == 404) emptyList() else null
    } catch (e: Exception) {
        null
    }

/** Whether the section shows at all: not when the house has no rooms, not
 *  while voice ID can't run ([voiceIdOff]), and not while the person has to
 *  introduce themselves again (the relearn notice says so instead). */
internal fun showVoiceRooms(person: Person, rooms: List<VoiceRoom>, voiceId: VoiceIdStatus?): Boolean =
    rooms.isNotEmpty() && !person.voice_relearn && !voiceIdOff(voiceId)

/** "known/total", e.g. "2/3". */
internal fun voiceRoomsCount(rooms: List<VoiceRoom>): String = "${rooms.count { it.known }}/${rooms.size}"

/** A room's chip text: "kitchen · learned" or "garage · not yet learned". */
internal fun voiceRoomChip(room: VoiceRoom): String =
    room.room_id + " · " + if (room.known) VOICE_ROOM_LEARNED else VOICE_ROOM_NOT_YET

/** How to teach the rooms that haven't learned the voice, or null when every
 *  room has. Says first that any room can recognize them already. */
internal fun voiceRoomsHint(name: String, rooms: List<VoiceRoom>): String? {
    if (rooms.all { it.known }) return null
    val named = name.trim()
    val subject = named.ifEmpty { "this person" }
    val who = named.ifEmpty { "they" }
    val said = named.ifEmpty { "…" }
    return "Every room can recognize $subject from any sample. A room recognizes a voice more " +
        "reliably once it has learned it there: in a room marked “not yet learned”, $who can " +
        "say “my name is $said” and answer yes."
}

// ---------------------------------------------------------------------------
// Voice ID's state (GET /api/people/voice-id, V061) — the web People page's
// VoiceIdNotice and PersonVoiceRelearn. 1.3.0 changed the speaker model and
// voices the old one learned can't be used, so nobody is recognized until
// they say "my name is …" again. When voice ID can't run at all (its model
// isn't on the server) that is all the page says: asking anyone to introduce
// themselves then would be a control that can't do what it says.
// ---------------------------------------------------------------------------

/** Server VoiceIdStatus. [ready] defaults to true so an answer missing it
 *  never claims voice ID is off; [problem] is the server's own words. */
@Serializable
internal data class VoiceIdStatus(
    val ready: Boolean = true,
    val problem: String? = null,
    val relearn: Int = 0,
)

/** One read of the voice-id route. */
internal sealed class VoiceIdRead {
    data class Known(val status: VoiceIdStatus) : VoiceIdRead()

    /** The server has no such route: show nothing. */
    data object Absent : VoiceIdRead()

    /** Anything else went wrong: keep what is shown. */
    data object Failed : VoiceIdRead()
}

/** A voice-id answer, or null when it isn't an object. */
internal fun parseVoiceIdStatus(json: JsonElement): VoiceIdStatus? =
    (json as? JsonObject)?.let { obj ->
        runCatching { DomovoiJson.decodeFromJsonElement(VoiceIdStatus.serializer(), obj) }.getOrNull()
    }

/**
 * Reads voice ID's state with [get]. A server from before V061 has no such
 * route: FastAPI answers 404, or 422 when `/api/people/{person_id}` takes
 * "voice-id" for a person id that isn't a number. Either is
 * [VoiceIdRead.Absent]: nothing new is shown. Any other failure is
 * [VoiceIdRead.Failed]; the page keeps what it showed and raises no toast.
 */
internal suspend fun loadVoiceId(get: suspend () -> JsonElement): VoiceIdRead =
    try {
        parseVoiceIdStatus(get())?.let { VoiceIdRead.Known(it) } ?: VoiceIdRead.Failed
    } catch (e: CancellationException) {
        throw e
    } catch (e: ApiException) {
        if (e.status == 404 || e.status == 422) VoiceIdRead.Absent else VoiceIdRead.Failed
    } catch (e: Exception) {
        VoiceIdRead.Failed
    }

/** What to show after [read], given what was [shown] before it. */
internal fun nextVoiceId(shown: VoiceIdStatus?, read: VoiceIdRead): VoiceIdStatus? = when (read) {
    is VoiceIdRead.Known -> read.status
    VoiceIdRead.Absent -> null
    VoiceIdRead.Failed -> shown
}

/** Voice ID is known not to run on this server (unknown is not "off"). */
internal fun voiceIdOff(voiceId: VoiceIdStatus?): Boolean = voiceId != null && !voiceId.ready

/** A notice's bold line and, when there is one, the line under it. */
internal data class VoiceNotice(val title: String, val body: String?)

internal const val VOICE_ID_OFF_TITLE = "Voice recognition is off"
internal const val VOICE_RELEARN_TITLE = "Domovoi needs to learn voices again"
internal const val PERSON_RELEARN_TITLE = "Not recognized by voice yet"
internal const val RELEARN_DOT_DESCRIPTION = "has to say “my name is …” again to be recognized by voice"

/** The household notice at the top of the People list (web VoiceIdNotice),
 *  or null when there is nothing to say. */
internal fun voiceIdNotice(voiceId: VoiceIdStatus?): VoiceNotice? {
    if (voiceId == null) return null
    if (!voiceId.ready) {
        return VoiceNotice(VOICE_ID_OFF_TITLE, voiceId.problem?.takeIf { it.isNotBlank() })
    }
    if (voiceId.relearn <= 0) return null
    val left = if (voiceId.relearn == 1) "1 person still has" else "${voiceId.relearn} people still have"
    return VoiceNotice(
        VOICE_RELEARN_TITLE,
        "Voice recognition was upgraded, and voices it knew before can't be used. " +
            "Until each person says “my name is …” in a room and answers yes, Domovoi " +
            "won’t know who is speaking. $left to.",
    )
}

/** The profile's notice for someone who has to introduce themselves again
 *  (web PersonVoiceRelearn), or null. */
internal fun personVoiceRelearnNotice(person: Person, voiceId: VoiceIdStatus?): VoiceNotice? {
    if (!person.voice_relearn || voiceIdOff(voiceId)) return null
    val name = person.name.trim()
    return VoiceNotice(
        PERSON_RELEARN_TITLE,
        "Voice recognition was upgraded, so Domovoi has to learn ${if (name.isEmpty()) "their" else "$name’s"} voice again. " +
            "To be recognized again, ${name.ifEmpty { "they" }} can say “my name is ${name.ifEmpty { "…" }}” " +
            "in a room and answer yes.",
    )
}

/** The roster row's idle dot ([RELEARN_DOT_DESCRIPTION]); not while voice
 *  ID is off, for the same reason as the notices. */
internal fun showRelearnDot(person: Person, voiceId: VoiceIdStatus?): Boolean =
    person.voice_relearn && !voiceIdOff(voiceId)

/** The People list's answer: the people, and voice ID's state read with them. */
internal data class PeoplePage(val people: List<Person>, val voiceId: VoiceIdRead)

@Serializable
internal data class PersonMemory(
    val id: Long = 0,
    val person_id: Long = 0,
    val body: String = "",
    val topic: String? = null,
    val source: String? = null,
    val status: String? = null,
    val created_at: String? = null,
)

@Serializable
internal data class PersonFavorite(
    val id: Long = 0,
    val person_id: Long = 0,
    val kind: String = "",
    val value: String = "",
    val rank: Int = 0,
)

@Serializable
internal data class PersonSession(
    val id: String = "",
    val room_id: String? = null,
    val started_at: String? = null,
    val last_activity: String? = null,
    val person_id: Long? = null,
    val intent_count: Int = 0,
)

@Serializable
internal data class PersonTurn(
    val id: Long = 0,
    val session_id: String? = null,
    val at: String? = null,
    val room_id: String? = null,
    val user_text: String? = null,
    val assistant_text: String? = null,
    val matched_handler: String? = null,
    val matched_path: String? = null,
)

@Serializable
internal data class DenylistEntry(
    val id: Long = 0,
    val denylisted_at: String? = null,
    val notes: String? = null,
)

// ---------------------------------------------------------------------------
// Per-person detail loader — the analog of the web page's per-selection
// Promise.all fetch (sessions / conversations / memories / favorites /
// preferences). Each source keeps its own failure (F-A008): a list that
// could not be loaded is empty AND carries an error, so the tabs can show
// an error card with retry instead of an innocent "nothing here yet".
// ---------------------------------------------------------------------------

/** Outcome of one sub-fetch: the rows, or why they could not be loaded. */
internal sealed class Fetched<out T> {
    data class Ok<T>(val value: T) : Fetched<T>()
    data class Failed(val error: String) : Fetched<Nothing>()
}

internal fun <T> Fetched<T>.orElse(default: T): T = when (this) {
    is Fetched.Ok -> value
    is Fetched.Failed -> default
}

internal val Fetched<*>.errorOrNull: String? get() = (this as? Fetched.Failed)?.error

/** Error-card text for a failed fetch — the rememberApi convention (ApiHooks.kt). */
internal fun fetchErrorText(e: Throwable): String =
    e.message?.trim()?.takeIf { it.isNotEmpty() } ?: "request failed"

private suspend fun <T> fetched(block: suspend () -> T): Fetched<T> =
    try {
        Fetched.Ok(block())
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        Fetched.Failed(fetchErrorText(e))
    }

/** One nullable error per sub-list; null means that list loaded fine. */
internal data class PersonDetailErrors(
    val sessions: String? = null,
    val conversations: String? = null,
    val memories: String? = null,
    val favorites: String? = null,
    val preferences: String? = null,
) {
    /** Names of the lists that failed, in tab order. */
    val failed: List<String>
        get() = listOfNotNull(
            sessions?.let { "sessions" },
            conversations?.let { "conversations" },
            memories?.let { "memories" },
            favorites?.let { "favorites" },
            preferences?.let { "preferences" },
        )

    val any: Boolean get() = failed.isNotEmpty()

    companion object {
        val NONE = PersonDetailErrors()
    }
}

/**
 * The once-per-load toast when any sub-fetch failed (null when all loaded):
 * "couldn't load sessions, memories: 500 Server Error: …" — names every
 * failed list and quotes the first failure so the user knows it was the
 * server, not empty data.
 */
internal fun personDetailFailureToast(errors: PersonDetailErrors): String? {
    if (!errors.any) return null
    val first = listOfNotNull(
        errors.sessions, errors.conversations, errors.memories, errors.favorites, errors.preferences,
    ).first()
    return "couldn't load ${errors.failed.joinToString(", ")}: $first"
}

/** Tab-title count: the number, or "?" when that list failed to load. */
internal fun countLabel(n: Int, error: String?): String = if (error != null) "?" else n.toString()

internal class PersonDetailData(
    val sessions: List<PersonSession>,
    val conversations: List<PersonTurn>,
    val memories: List<PersonMemory>,
    val favorites: List<PersonFavorite>,
    val preferences: Map<String, JsonElement>,
    val errors: PersonDetailErrors,
    val loading: Boolean,
    /** Rooms that have learned this voice; empty (section hidden) until read, on a
     *  server without the route, or in a house with no rooms. */
    val voiceRooms: List<VoiceRoom>,
    val refresh: () -> Unit,
)

@Composable
internal fun rememberPersonDetail(personId: Long, voiceSamples: Int = 0): PersonDetailData {
    val app = LocalApp.current
    val toast = LocalToast.current
    var tick by remember(personId) { mutableIntStateOf(0) }
    var sessions by remember(personId) { mutableStateOf<Fetched<List<PersonSession>>>(Fetched.Ok(emptyList())) }
    var conversations by remember(personId) { mutableStateOf<Fetched<List<PersonTurn>>>(Fetched.Ok(emptyList())) }
    var memories by remember(personId) { mutableStateOf<Fetched<List<PersonMemory>>>(Fetched.Ok(emptyList())) }
    var favorites by remember(personId) { mutableStateOf<Fetched<List<PersonFavorite>>>(Fetched.Ok(emptyList())) }
    var preferences by remember(personId) {
        mutableStateOf<Fetched<Map<String, JsonElement>>>(Fetched.Ok(emptyMap()))
    }
    var loading by remember(personId) { mutableStateOf(true) }
    var voiceRooms by remember(personId) { mutableStateOf<List<VoiceRoom>>(emptyList()) }

    // Its own effect so the other lists don't wait on it, re-read on every
    // refresh and when the person's sample count changes (a room just
    // learned them). Not part of the failure toast: see [loadVoiceRooms].
    LaunchedEffect(personId, tick, voiceSamples) {
        loadVoiceRooms { app.api.get("/api/people/$personId/voice-rooms") }?.let { voiceRooms = it }
    }

    LaunchedEffect(personId, tick) {
        loading = true
        sessions = fetched {
            app.api.get("/api/people/$personId/sessions?limit=50").decode<List<PersonSession>>()
        }
        conversations = fetched {
            app.api.get("/api/people/$personId/conversations?limit=200").decode<List<PersonTurn>>()
        }
        memories = fetched {
            app.api.get("/api/people/$personId/memories").decode<List<PersonMemory>>()
        }
        favorites = fetched {
            app.api.get("/api/people/$personId/favorites").decode<List<PersonFavorite>>()
        }
        preferences = fetched {
            (app.api.get("/api/people/$personId/preferences") as? JsonObject) ?: emptyMap()
        }
        loading = false
        personDetailFailureToast(
            PersonDetailErrors(
                sessions.errorOrNull, conversations.errorOrNull, memories.errorOrNull,
                favorites.errorOrNull, preferences.errorOrNull,
            ),
        )?.let { toast(it) }
    }

    val errors = PersonDetailErrors(
        sessions = sessions.errorOrNull,
        conversations = conversations.errorOrNull,
        memories = memories.errorOrNull,
        favorites = favorites.errorOrNull,
        preferences = preferences.errorOrNull,
    )
    return PersonDetailData(
        sessions = sessions.orElse(emptyList()),
        conversations = conversations.orElse(emptyList()),
        memories = memories.orElse(emptyList()),
        favorites = favorites.orElse(emptyList()),
        preferences = preferences.orElse(emptyMap()),
        errors = errors,
        loading = loading,
        voiceRooms = voiceRooms,
    ) { tick++ }
}

// ---------------------------------------------------------------------------
// Where a person was last heard (the profile header, web heardIn). The room
// of their newest identified turn: GET /api/people/{id}/conversations is
// newest first (ORDER BY at DESC), so it is the first row the detail already
// holds; no extra read. A phone's chat mic logs as room "phone-<device>"
// ("the phone"); a turn with no room came from chat.
// ---------------------------------------------------------------------------

/** "last heard in": a room name (dashes and underscores as spaces, as the
 *  web shows it), "the phone", "chat", or "—" before they have said anything.
 *  [failed]: the conversations read failed, so "?" (F-A008: not knowing is
 *  not "never"); [loading] with nothing read yet is "…", not "—". */
internal fun lastHeardIn(turns: List<PersonTurn>, failed: Boolean = false, loading: Boolean = false): String {
    if (failed) return "?"
    val turn = turns.firstOrNull() ?: return if (loading) "…" else "—"
    val room = turn.room_id.orEmpty().trim()
    if (room.isEmpty()) return "chat"
    if (room.startsWith("phone-")) return "the phone"
    return room.replace(Regex("[-_]+"), " ")
}

internal fun prettyPref(v: JsonElement): String = if (v is JsonPrimitive) v.content else v.toString()

internal fun plural(n: Int, word: String): String = if (n == 1) word else word + "s"
