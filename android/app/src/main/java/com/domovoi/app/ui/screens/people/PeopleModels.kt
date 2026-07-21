package com.domovoi.app.ui.screens.people

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import com.domovoi.app.LocalApp
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
// preferences), with each source falling back to empty on failure.
// ---------------------------------------------------------------------------

internal class PersonDetailData(
    val sessions: List<PersonSession>,
    val conversations: List<PersonTurn>,
    val memories: List<PersonMemory>,
    val favorites: List<PersonFavorite>,
    val preferences: Map<String, JsonElement>,
    val loading: Boolean,
    val refresh: () -> Unit,
)

private suspend fun <T> orDefault(default: T, block: suspend () -> T): T =
    try {
        block()
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        default
    }

@Composable
internal fun rememberPersonDetail(personId: Long): PersonDetailData {
    val app = LocalApp.current
    var tick by remember(personId) { mutableIntStateOf(0) }
    var sessions by remember(personId) { mutableStateOf(emptyList<PersonSession>()) }
    var conversations by remember(personId) { mutableStateOf(emptyList<PersonTurn>()) }
    var memories by remember(personId) { mutableStateOf(emptyList<PersonMemory>()) }
    var favorites by remember(personId) { mutableStateOf(emptyList<PersonFavorite>()) }
    var preferences by remember(personId) { mutableStateOf<Map<String, JsonElement>>(emptyMap()) }
    var loading by remember(personId) { mutableStateOf(true) }

    LaunchedEffect(personId, tick) {
        loading = true
        sessions = orDefault(emptyList()) {
            app.api.get("/api/people/$personId/sessions?limit=50").decode<List<PersonSession>>()
        }
        conversations = orDefault(emptyList()) {
            app.api.get("/api/people/$personId/conversations?limit=200").decode<List<PersonTurn>>()
        }
        memories = orDefault(emptyList()) {
            app.api.get("/api/people/$personId/memories").decode<List<PersonMemory>>()
        }
        favorites = orDefault(emptyList()) {
            app.api.get("/api/people/$personId/favorites").decode<List<PersonFavorite>>()
        }
        preferences = orDefault(emptyMap()) {
            (app.api.get("/api/people/$personId/preferences") as? JsonObject) ?: emptyMap()
        }
        loading = false
    }

    return PersonDetailData(sessions, conversations, memories, favorites, preferences, loading) { tick++ }
}

internal fun prettyPref(v: JsonElement): String = if (v is JsonPrimitive) v.content else v.toString()

internal fun plural(n: Int, word: String): String = if (n == 1) word else word + "s"
