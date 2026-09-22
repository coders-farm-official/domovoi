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
import com.domovoi.app.net.decode
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.Serializable
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
)

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
    val refresh: () -> Unit,
)

@Composable
internal fun rememberPersonDetail(personId: Long): PersonDetailData {
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
    ) { tick++ }
}

internal fun prettyPref(v: JsonElement): String = if (v is JsonPrimitive) v.content else v.toString()

internal fun plural(n: Int, word: String): String = if (n == 1) word else word + "s"
