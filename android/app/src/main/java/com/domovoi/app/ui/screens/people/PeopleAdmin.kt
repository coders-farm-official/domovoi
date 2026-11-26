package com.domovoi.app.ui.screens.people

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.AdminSession
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.failureText
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/*
 * People as admin on this phone (1.5.0; design §1's People jobs): rename
 * someone, forget someone. Both are admin on the server (PATCH and DELETE
 * /api/people/{id}, docs/API_REFERENCE.md), so they are offered only while
 * this phone holds admin (net/AdminSession.kt) and never on a shared screen
 * (where People is hidden anyway), each through AdminSession.act. Forget
 * asks first in the dashboard's words (people.jsx ConfirmForget), typing the
 * name included. Signed out, the profile's line adds "or sign in as admin
 * on this phone".
 */

internal object PeopleAdminWords {
    /** The profile's line, signed out on a household phone. */
    const val EDIT_OR_PHONE = "renaming and forgetting someone are done on the dashboard, or sign in as admin on this phone"

    const val RENAME = "rename"
    const val SAVE = "save"
    const val CANCEL = "cancel"
    const val NEW_NAME = "new name"
    const val FORGET = "forget person"

    fun renamed(name: String) = "renamed to $name"
    fun forgot(name: String) = "forgot $name · cascade complete"

    /** ConfirmForget's title: the id too when somebody shares the name. */
    fun forgetTitle(p: Person): String = "Forget ${p.name}${if (p.namesakes > 0) " (#${p.id})" else ""}?"

    /** ConfirmForget's body, line for line: what Domovoi will lose. */
    fun forgetBody(p: Person, sessionCount: Int, turnCount: Int, turnsFetched: Int = PERSON_TURNS_FETCHED): String {
        val samples = p.voice_profile_count
        val turns = "$turnCount${if (turnCount >= turnsFetched) "+" else ""} conversation turn${if (turnCount == 1) "" else "s"}"
        val sessions = if (sessionCount > 0) " in $sessionCount session${if (sessionCount == 1) "" else "s"}" else ""
        return "This is a cascade delete. Domovoi will lose:\n" +
            "• $samples voice profile${if (samples == 1) "" else "s"}\n" +
            "• what they said: $turns$sessions, with Domovoi's answers\n" +
            "• their memories, favorites and preferences\n\n" +
            "Turns where someone else said their name stay: Domovoi goes by who was speaking."
    }

    fun typeToConfirm(name: String) = "Type $name to confirm."
    fun forgetButton(name: String) = "Forget $name"
}

/** How many turns the profile reads (the dashboard's PERSON_TURNS_FETCHED):
 *  that many means "or more". */
internal const val PERSON_TURNS_FETCHED = 200

/** Whether rename and forget are offered: admin held, not a shared screen. */
fun peopleAdminHeld(state: AdminSession.State, shared: Boolean): Boolean =
    !shared && state !is AdminSession.State.SignedOut

/** The profile's line about renaming and forgetting: none with admin held
 *  (the buttons are there), the old one on a shared screen. */
fun peopleEditLine(state: AdminSession.State, shared: Boolean): String? = when {
    peopleAdminHeld(state, shared) -> null
    shared -> PEOPLE_EDIT_ON_DASHBOARD
    else -> PeopleAdminWords.EDIT_OR_PHONE
}

/** The name a rename sends: trimmed, runs of whitespace as one space; null
 *  when there is nothing to send (blank, or the name they have). */
fun renameTo(current: String, draft: String): String? {
    val name = draft.trim().replace(Regex("\\s+"), " ")
    return if (name.isEmpty() || name == current) null else name
}

/** Forget is pressed only once the name is typed (ConfirmForget's `ok`). */
fun forgetConfirmed(typed: String, name: String): Boolean = typed.trim() == name

/** Why a rename or forget didn't work: the server's own sentence for a
 *  taken name (409), an unknown person or a bad name; else the usual. */
fun peopleProblem(action: String, e: Throwable): String {
    if (e is AdminSession.AdminUnavailable) return e.message ?: "admin isn't available"
    if (e is ApiException && e.status in setOf(404, 409, 422)) {
        runCatching {
            ((DomovoiJson.parseToJsonElement(e.body) as? JsonObject)?.get("detail") as? JsonPrimitive)
                ?.takeIf { it.isString }?.content?.takeIf { it.isNotBlank() }
        }.getOrNull()?.let { return it }
    }
    return failureText(action, e)
}

/** The two routes; [headers] and [onlyTo] come from AdminSession. */
class PeopleAdminApi(private val api: ApiClient) {
    suspend fun rename(id: Long, name: String, headers: Map<String, String>, onlyTo: String) {
        api.patch("/api/people/$id", buildJsonObject { put("name", name) }, headers, onlyTo = onlyTo)
    }

    suspend fun forget(id: Long, headers: Map<String, String>, onlyTo: String) {
        api.delete("/api/people/$id", null, headers, onlyTo = onlyTo)
    }
}

/** The name with a rename beside it (admin held): the box, save, cancel. */
@Composable
internal fun PersonRename(person: Person, onRenamed: () -> Unit) {
    val app = LocalApp.current
    val context = LocalContext.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val api = remember(app) { PeopleAdminApi(app.api) }
    var editing by remember(person.id) { mutableStateOf(false) }
    var draft by remember(person.id, person.name) { mutableStateOf(person.name) }
    var saving by remember { mutableStateOf(false) }
    DisposableEffect(context) {
        app.adminLocker.promptHost = context
        onDispose { if (app.adminLocker.promptHost === context) app.adminLocker.promptHost = null }
    }
    fun save() {
        val name = renameTo(person.name, draft)
        if (name == null) { editing = false; draft = person.name; return }
        saving = true
        scope.launch {
            try {
                app.admin.act { h, srv -> api.rename(person.id, name, h, srv) }
                toast(PeopleAdminWords.renamed(name))
                editing = false
                onRenamed()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // The box stays open with what was typed (a taken name, say).
                toast(peopleProblem("rename", e))
            } finally {
                saving = false
            }
        }
    }
    if (!editing) {
        TextButton(onClick = { editing = true }) { Text(PeopleAdminWords.RENAME, color = Domovoi.colors.brand) }
        return
    }
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        OutlinedTextField(
            value = draft,
            onValueChange = { draft = it.take(60) },
            singleLine = true,
            enabled = !saving,
            label = { Text(PeopleAdminWords.NEW_NAME) },
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
            modifier = Modifier.fillMaxWidth(),
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton(enabled = !saving, onClick = { save() }) { Text(PeopleAdminWords.SAVE, color = Domovoi.colors.brand) }
            TextButton(enabled = !saving, onClick = { editing = false; draft = person.name }) {
                Text(PeopleAdminWords.CANCEL, color = Domovoi.colors.fgMuted)
            }
        }
    }
}

/** "forget person" and its confirm (admin held). */
@Composable
internal fun PersonForget(person: Person, sessionCount: Int, turnCount: Int, onForgotten: () -> Unit) {
    val app = LocalApp.current
    val context = LocalContext.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val api = remember(app) { PeopleAdminApi(app.api) }
    var asking by remember(person.id) { mutableStateOf(false) }
    var typed by remember(person.id) { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    DisposableEffect(context) {
        app.adminLocker.promptHost = context
        onDispose { if (app.adminLocker.promptHost === context) app.adminLocker.promptHost = null }
    }
    Button(
        enabled = !busy,
        onClick = { typed = ""; asking = true },
        colors = androidx.compose.material3.ButtonDefaults.buttonColors(
            containerColor = Domovoi.colors.errSoft, contentColor = Domovoi.colors.err,
        ),
    ) { Text(PeopleAdminWords.FORGET) }
    if (!asking) return
    val ok = forgetConfirmed(typed, person.name)
    AlertDialog(
        onDismissRequest = { asking = false },
        containerColor = Domovoi.colors.raised,
        title = { Text(PeopleAdminWords.forgetTitle(person), style = MaterialTheme.typography.titleMedium) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(PeopleAdminWords.forgetBody(person, sessionCount, turnCount), style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted)
                Text(PeopleAdminWords.typeToConfirm(person.name), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
                OutlinedTextField(
                    value = typed,
                    onValueChange = { typed = it },
                    singleLine = true,
                    textStyle = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(enabled = ok && !busy, onClick = {
                asking = false
                busy = true
                scope.launch {
                    try {
                        app.admin.act { h, srv -> api.forget(person.id, h, srv) }
                        toast(PeopleAdminWords.forgot(person.name))
                        onForgotten()
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        toast(peopleProblem("forget", e))
                    } finally {
                        busy = false
                    }
                }
            }) { Text(PeopleAdminWords.forgetButton(person.name), color = if (ok) Domovoi.colors.err else Domovoi.colors.fgFaint) }
        },
        dismissButton = { TextButton(onClick = { asking = false }) { Text(PeopleAdminWords.CANCEL, color = Domovoi.colors.fgMuted) } },
    )
}
