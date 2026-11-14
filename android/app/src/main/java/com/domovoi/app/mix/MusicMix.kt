package com.domovoi.app.mix

import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DEVICE_ID_HEADER
import com.domovoi.app.net.DeviceOwner
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.apiDetail
import com.domovoi.app.net.decode
import com.domovoi.app.net.isDeviceTokenRefusal
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject

/*
 * The Music mix (1.3.0): how Up next picks the songs that follow. The
 * server's routes are web/backend/api/music_mix.py (shapes:
 * docs/API_REFERENCE.md §3.5f "The Music mix"); the words and the layout
 * are the dashboard's (web/static/music_mix.jsx MusicMixPanel and
 * PersonMusicMix). This file is the wire shapes, the words, who may change
 * what, and the calls. Pure but for the calls.
 *
 * Who may change what (wf/mix-gate, 2026-11-14):
 *   - the household's mix (`PUT /api/music/mix`): an admin only — a phone's
 *     household token gets 401 "admin session required";
 *   - a person's own sliders (`PUT /api/people/{id}/music-mix`): an admin,
 *     or the device whose `owner_person_id` is that person (a plain owner
 *     match by the id the caller names itself by: no claim key, no wait);
 *     anyone else 403 [PERSON_MIX_NOT_YOURS];
 *   - the two internet switches: an admin (`PATCH /api/config/editable`).
 * This app holds no admin session ([APP_IS_ADMIN]), so on a phone the
 * household's mix and the internet switches are read-only, and only the
 * person this phone is ("this is me") gets sliders.
 */

// ── The wire ─────────────────────────────────────────────────────────────

/** `internet`: the two ListenBrainz switches as the core last reported
 *  them (null: not reported yet), and its fetch state. */
@Serializable
data class MixInternet(
    @SerialName("similar_artists") val similarArtists: Boolean? = null,
    val popularity: Boolean? = null,
    val state: String = "unknown",
)

/** `GET /api/music/mix` (music_mix.py `_mix_answer`: Mix.json() + steps,
 *  range, internet, people_with_own_mix). */
@Serializable
data class MusicMix(
    @SerialName("discovery_favorites") val discoveryFavorites: Int = MIX_MIDDLE,
    @SerialName("close_wander") val closeWander: Int = MIX_MIDDLE,
    val autoplay: Boolean = true,
    @SerialName("list_repeat_min") val listRepeatMin: Int = 50,
    val steps: List<Int> = listOf(1, 2, 3, 4, 5),
    @SerialName("list_repeat_min_range") val listRepeatMinRange: List<Int> = listOf(20, 500),
    val internet: MixInternet = MixInternet(),
    @SerialName("people_with_own_mix") val peopleWithOwnMix: List<Long> = emptyList(),
)

/** A person's two sliders: `override` (either may be missing in a stored
 *  override, mix.py person_override) and `uses` (always both). */
@Serializable
data class MixSliders(
    @SerialName("discovery_favorites") val discoveryFavorites: Int? = null,
    @SerialName("close_wander") val closeWander: Int? = null,
)

/** `GET/PUT /api/people/{id}/music-mix` (music_mix.py `_person_answer`). */
@Serializable
data class PersonMix(
    @SerialName("person_id") val personId: Long,
    /** null: "Use the household's mix". */
    val override: MixSliders? = null,
    /** What that person's sessions use now: theirs, else the household's. */
    val uses: MixSliders = MixSliders(),
) {
    val ownMix: Boolean get() = override != null
}

// ── The words (music_mix.jsx) ────────────────────────────────────────────

const val MIX_MIDDLE = 3

/** One slider: its label, its two ends, and each step said in words. */
data class MixSliderSpec(val label: String, val left: String, val right: String, val words: Map<Int, String>) {
    fun said(step: Int?): String = words[step ?: MIX_MIDDLE] ?: words.getValue(MIX_MIDDLE)
}

val MIX_DISCOVERY = MixSliderSpec(
    label = "discovery or favorites", left = "Discovery", right = "Favorites",
    words = mapOf(
        1 to "mostly songs you haven’t heard in a while",
        2 to "more songs you haven’t heard in a while",
        3 to "a balance (the default)",
        4 to "more of what you play most",
        5 to "mostly what you play most",
    ),
)

val MIX_WANDER = MixSliderSpec(
    label = "stay close or wander", left = "Stay close", right = "Wander",
    words = mapOf(
        1 to "stays close to the song playing",
        2 to "mostly close to the song playing",
        3 to "a balance (the default)",
        4 to "wanders more across your library",
        5 to "wanders widely across your library",
    ),
)

const val MIX_INTRO = "How Up next picks the songs that follow when the queue runs out — in every room, browser and phone. " +
    "Up next leans a little toward songs you haven’t heard in a while; each song in the queue can tell you why it was picked."
const val MIX_CARD_TITLE = "The mix"
const val MIX_CARD_SUB = "People can set their own in Settings → People; everyone else gets these."
const val PLAYING_ON_TITLE = "Playing on"
const val AUTOPLAY_DEFAULT_LABEL = "Autoplay when a playlist ends"
const val AUTOPLAY_DEFAULT_SUB = "Similar songs keep playing when the queue runs out. Rooms and new players start this way; " +
    "a browser or phone can still turn it off for itself in its queue."
const val NO_REPEATS_LABEL = "No repeats within"
const val NO_REPEATS_SUB = "A list shorter than this plays once, then similar songs follow. A longer one starts over, shuffled again."
const val INTERNET_TITLE = "From the internet"
const val INTERNET_SUB = "Optional. Up next works from your library alone without them."
const val SIMILAR_ARTISTS_LABEL = "Similar artists from the internet"
const val SIMILAR_ARTISTS_SUB = "Up next can play songs you have by artists like the one playing. Asks ListenBrainz with your " +
    "artists’ MusicBrainz ids only — no names, nothing you played. Up next never waits for it."
const val POPULARITY_LABEL = "Song popularity from the internet"
const val POPULARITY_SUB = "An artist’s well-known songs come up a little more often (your own taste counts more). " +
    "Asks ListenBrainz with your songs’ MusicBrainz ids only."

/** Under the household's mix on a phone (no admin session here). */
const val ADMIN_CAN_CHANGE_THIS = "An admin can change this."

/** Under the internet switches on a phone. */
const val ADMIN_CAN_CHANGE_THESE = "An admin can change these."

const val NOT_KNOWN_YET = "not known yet"
const val USE_HOUSEHOLDS_MIX = "Use the household’s mix"
const val PERSON_MIX_HEAD = "music mix"

/** The server's 403 for someone else's mix (music_mix.py NOT_YOURS_DETAIL);
 *  shown only when a 403 carries no words of its own. */
const val PERSON_MIX_NOT_YOURS = "Only an admin, or this person's own phone, can change their mix."

fun personMixReadOnlyNote(name: String) = "An admin, or $name’s own phone, can change this."
fun personMixUsedWhen(name: String) = "Used when Up next plays for $name: “listening as” them, their phone, or their voice in a room."
fun noRepeatsValue(n: Int) = "$n songs"
fun onOff(v: Boolean?) = when (v) { true -> "on"; false -> "off"; null -> NOT_KNOWN_YET }

// ── Who may change what ──────────────────────────────────────────────────

/** This app never signs in as an admin (Settings → Advanced is the
 *  dashboard's): every admin-only write is left out on a phone. */
const val APP_IS_ADMIN = false

/** What a Music mix screen offers for writing; anything false is shown as
 *  words, never as a control the server would refuse. */
data class MixRights(
    val household: Boolean,
    val internet: Boolean,
)

fun mixRights(admin: Boolean = APP_IS_ADMIN) = MixRights(household = admin, internet = admin)

/**
 * Whether this phone may change [personId]'s own sliders: an admin, or the
 * phone whose owner (the server's `owner_person_id`, "this is me") is that
 * person. A shared screen is nobody's. The server's own check is the same
 * plain owner match, so this is never a promise it won't keep — and a 403
 * (the claim changed meanwhile) is still said in its words.
 */
fun personMixEditable(personId: Long, owner: DeviceOwner?, shared: Boolean, admin: Boolean = APP_IS_ADMIN): Boolean =
    admin || (!shared && owner != null && owner.id == personId)

// ── Autoplay: this phone's choice, else the household's ─────────────────

/**
 * What Up next's Autoplay is on this phone: what the person chose here
 * ([choice], null until they flip it), else the household's Music mix
 * default ([householdDefault], null until read), else on (the server's own
 * default, mix.py `Mix.autoplay`).
 */
fun effectiveAutoplay(choice: Boolean?, householdDefault: Boolean?): Boolean = choice ?: householdDefault ?: true

/**
 * The household's Autoplay default as each server last answered it, kept
 * by server (normalised address) so one house's answer never shows on
 * another's switch. Memory only: it is read again whenever the Up next
 * section or the Music mix screen is on screen.
 */
class MixDefaults {
    private val _autoplay = MutableStateFlow<Map<String, Boolean>>(emptyMap())
    val autoplay: StateFlow<Map<String, Boolean>> = _autoplay

    fun autoplayFor(server: String): Boolean? = _autoplay.value[ServerCredentials.normalize(server)]

    /** [server]'s answer: the server the read was SENT to, captured before it. */
    fun heard(server: String, mix: MusicMix) {
        val key = ServerCredentials.normalize(server)
        _autoplay.update { it + (key to mix.autoplay) }
    }
}

// ── The calls ────────────────────────────────────────────────────────────

/** `PUT /api/people/{id}/music-mix`'s body: the two sliders, or null for
 *  "Use the household's mix". */
fun personMixBody(override: MixSliders?): JsonObject = buildJsonObject {
    if (override == null) {
        put("override", JsonNull)
    } else {
        putJsonObject("override") {
            put("discovery_favorites", override.discoveryFavorites ?: MIX_MIDDLE)
            put("close_wander", override.closeWander ?: MIX_MIDDLE)
        }
    }
}

/** Turning "Use the household's mix" off: start from what they use now
 *  (the web's put({…uses || 3})). */
fun ownMixFrom(uses: MixSliders) = MixSliders(uses.discoveryFavorites ?: MIX_MIDDLE, uses.closeWander ?: MIX_MIDDLE)

/** One slider moved: the other stays as it is used now. */
fun withStep(uses: MixSliders, discovery: Boolean, step: Int) =
    if (discovery) ownMixFrom(uses).copy(discoveryFavorites = step) else ownMixFrom(uses).copy(closeWander = step)

/** `GET /api/music/mix` from [server] only (sent nowhere else). */
suspend fun readHouseholdMix(api: ApiClient, server: String): MusicMix =
    parse(api.raw("GET", "/api/music/mix", onlyTo = server)).decode()

/** `GET /api/people/{id}/music-mix` from [server] only. */
suspend fun readPersonMix(api: ApiClient, server: String, personId: Long): PersonMix =
    parse(api.raw("GET", "/api/people/$personId/music-mix", onlyTo = server)).decode()

/** `PUT /api/people/{id}/music-mix` to [server] only, naming this phone
 *  ([DEVICE_ID_HEADER]: the server matches its owner against [personId]). */
suspend fun savePersonMix(api: ApiClient, server: String, deviceId: String, personId: Long, override: MixSliders?): PersonMix =
    api.put(
        "/api/people/$personId/music-mix",
        personMixBody(override),
        headers = mapOf(DEVICE_ID_HEADER to deviceId),
        onlyTo = server,
    ).decode()

private fun parse(text: String) = if (text.isBlank()) JsonNull else DomovoiJson.parseToJsonElement(text)

/**
 * What to say when a mix write was refused: the server's own words for a
 * 403 ([PERSON_MIX_NOT_YOURS] when it gave none); for a 401 that is not
 * about the household token (the admin gate's "admin session required"),
 * [ADMIN_CAN_CHANGE_THIS] — never a lost pairing. Null for anything else
 * (the caller's usual failure line).
 */
fun mixWriteRefusal(e: Throwable): String? {
    if (e !is ApiException) return null
    return when {
        e.status == 403 && !isDeviceTokenRefusal(e.status, e.body) -> apiDetail(e) ?: PERSON_MIX_NOT_YOURS
        e.status == 401 && !isDeviceTokenRefusal(e.status, e.body) -> ADMIN_CAN_CHANGE_THIS
        else -> null
    }
}

/** Under the household's Autoplay default: what THIS phone does. Null
 *  [choice]: it follows the household's; else what was chosen in its queue. */
fun phoneAutoplayNote(choice: Boolean?): String = when (choice) {
    null -> "This phone follows this until you switch autoplay in its queue."
    true -> "This phone: autoplay on (switched in its queue)."
    false -> "This phone: autoplay off (switched in its queue)."
}
