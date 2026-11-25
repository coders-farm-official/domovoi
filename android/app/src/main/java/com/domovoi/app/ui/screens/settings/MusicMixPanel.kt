package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.mix.ADMIN_CAN_CHANGE_THESE
import com.domovoi.app.mix.ADMIN_CAN_CHANGE_THIS
import com.domovoi.app.mix.AUTOPLAY_DEFAULT_LABEL
import com.domovoi.app.mix.AUTOPLAY_DEFAULT_SUB
import com.domovoi.app.mix.INTERNET_SUB
import com.domovoi.app.mix.INTERNET_TITLE
import com.domovoi.app.mix.MIX_CARD_SUB
import com.domovoi.app.mix.MIX_CARD_TITLE
import com.domovoi.app.mix.MIX_DISCOVERY
import com.domovoi.app.mix.MIX_INTRO
import com.domovoi.app.mix.MIX_MIDDLE
import com.domovoi.app.mix.MIX_WANDER
import com.domovoi.app.mix.MixSliderSpec
import com.domovoi.app.mix.MixSliders
import com.domovoi.app.mix.MusicMix
import com.domovoi.app.mix.NO_REPEATS_LABEL
import com.domovoi.app.mix.NO_REPEATS_SUB
import com.domovoi.app.mix.PERSON_MIX_HEAD
import com.domovoi.app.mix.PLAYING_ON_TITLE
import com.domovoi.app.mix.POPULARITY_LABEL
import com.domovoi.app.mix.POPULARITY_SUB
import com.domovoi.app.mix.PersonMix
import com.domovoi.app.mix.SIMILAR_ARTISTS_LABEL
import com.domovoi.app.mix.SIMILAR_ARTISTS_SUB
import com.domovoi.app.mix.USE_HOUSEHOLDS_MIX
import com.domovoi.app.mix.mixRights
import com.domovoi.app.mix.mixWriteRefusal
import com.domovoi.app.mix.noRepeatsValue
import com.domovoi.app.mix.onOff
import com.domovoi.app.mix.ownMixFrom
import com.domovoi.app.mix.personMixEditable
import com.domovoi.app.mix.personMixReadOnlyNote
import com.domovoi.app.mix.personMixUsedWhen
import com.domovoi.app.mix.phoneAutoplayNote
import com.domovoi.app.mix.readHouseholdMix
import com.domovoi.app.mix.readPersonMix
import com.domovoi.app.mix.savePersonMix
import com.domovoi.app.mix.withStep
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.LocalSharedScreen
import com.domovoi.app.net.failureText
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

/*
 * Settings → Household → Music mix (1.3.0; the dashboard's MusicMixPanel,
 * web/static/music_mix.jsx). How Up next picks the songs that follow.
 *
 * The household's mix and the two internet switches are an admin's
 * (wf/mix-gate) and this app holds no admin session, so they are said in
 * words with "An admin can change this." and a way to the dashboard —
 * never a slider the server would refuse. What a phone CAN change is its
 * own person's mix ("this is me"): that card is here too, the same one the
 * person's page in Settings → People shows ([PersonMusicMixSection]).
 */

@Composable
internal fun MusicMixPanel() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val shared = LocalSharedScreen.current
    val server by app.prefs.serverUrl.collectAsState()
    val token by app.prefs.deviceToken.collectAsState()
    val choice by app.prefs.upNextAutoplay.collectAsState()
    val owners by app.prefs.deviceOwners.collectAsState()
    val owner = owners[ServerCredentials.normalize(server)]
    val paired = !token.isNullOrBlank()
    val rights = mixRights()
    val mix = rememberApi(server, token) { a ->
        if (!paired) return@rememberApi null
        val at = server
        readHouseholdMix(a.api, at).also { a.mixDefaults.heard(at, it) }
    }
    val dashboard = dashboardSettingsUrl(server, "music-mix")

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item(key = "intro") {
            Text(MIX_INTRO, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted)
        }
        val data = mix.data
        when {
            !paired -> item(key = "unpaired") {
                Text(
                    "Pair this phone with your home server to see its music mix (Settings → Phones & browsers).",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fgMuted,
                )
            }
            data == null -> item(key = "loading") {
                Text(
                    mixReadProblem(mix.error) ?: "reading…",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fgMuted,
                )
            }
            else -> {
                item(key = "mix") {
                    PanelCard(MIX_CARD_TITLE, MIX_CARD_SUB) {
                        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            if (!rights.household) AdminNote(ADMIN_CAN_CHANGE_THIS)
                            SaidRow(MIX_DISCOVERY.label, MIX_DISCOVERY.said(data.discoveryFavorites))
                            SaidRow(MIX_WANDER.label, MIX_WANDER.said(data.closeWander))
                        }
                    }
                }
                item(key = "playing-on") {
                    PanelCard(PLAYING_ON_TITLE) {
                        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            SaidRow(AUTOPLAY_DEFAULT_LABEL, onOff(data.autoplay), AUTOPLAY_DEFAULT_SUB)
                            Text(
                                phoneAutoplayNote(choice),
                                style = MaterialTheme.typography.bodySmall,
                                color = Domovoi.colors.fgMuted,
                            )
                            SaidRow(NO_REPEATS_LABEL, noRepeatsValue(data.listRepeatMin), NO_REPEATS_SUB)
                        }
                    }
                }
                item(key = "internet") {
                    PanelCard(INTERNET_TITLE, INTERNET_SUB) {
                        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            if (!rights.internet) AdminNote(ADMIN_CAN_CHANGE_THESE)
                            SaidRow(SIMILAR_ARTISTS_LABEL, onOff(data.internet.similarArtists), SIMILAR_ARTISTS_SUB)
                            SaidRow(POPULARITY_LABEL, onOff(data.internet.popularity), POPULARITY_SUB)
                        }
                    }
                }
                if (!rights.household || !rights.internet) {
                    item(key = "dashboard") {
                        TextButton(
                            enabled = dashboard != null,
                            onClick = {
                                if (dashboard != null && !openInBrowser(context, dashboard)) toast("no browser found to open the dashboard")
                            },
                        ) {
                            Icon(Icons.AutoMirrored.Filled.OpenInNew, contentDescription = null, modifier = Modifier.padding(end = 6.dp))
                            Text("Open on the dashboard", color = Domovoi.colors.brand)
                        }
                    }
                }
                // The one mix this phone may change: its own person's.
                val me = owner
                if (!shared && me != null) {
                    item(key = "mine") {
                        PanelCard("Your mix", "${me.name.ifBlank { "Your" }}’s own sliders, if you want them.") {
                            PersonMusicMixSection(me.id, me.name.ifBlank { "you" }, showHead = false)
                        }
                    }
                }
            }
        }
    }
}

/** What to say when the household's mix can't be read. */
internal fun mixReadProblem(error: String?): String? = when {
    error == null -> null
    error.startsWith("404") -> "This home server is older than the music mix. Update Domovoi to choose how Up next picks songs."
    else -> "couldn’t read the music mix: $error"
}

@Composable
private fun AdminNote(text: String) {
    Text(text, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
}

/** A setting said in words: its name, its value, a quiet line under it. */
@Composable
private fun SaidRow(label: String, value: String, sub: String? = null) {
    Column(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(label, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold, color = Domovoi.colors.fg,
                modifier = Modifier.weight(1f, fill = false))
            Text(value, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
        }
        if (sub != null) Text(sub, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
    }
}

/** A five-step slider with its two ends named and the step said in words. */
@Composable
private fun MixSlider(spec: MixSliderSpec, value: Int?, enabled: Boolean, onChange: (Int) -> Unit) {
    var at by remember(value) { mutableFloatStateOf((value ?: MIX_MIDDLE).toFloat()) }
    val step = at.roundToInt().coerceIn(1, 5)
    Column(Modifier.fillMaxWidth()) {
        Text(spec.label, style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted)
        Slider(
            value = at,
            onValueChange = { at = it },
            onValueChangeFinished = { if (step != (value ?: MIX_MIDDLE)) onChange(step) },
            valueRange = 1f..5f,
            steps = 3,
            enabled = enabled,
            modifier = Modifier.semantics {
                contentDescription = "${spec.left} or ${spec.right}"
                stateDescription = spec.said(step)
            },
        )
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(spec.left, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
            Text(spec.right, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
        }
        Text(spec.said(step), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fg)
    }
}

/**
 * A person's own music mix (the dashboard's PersonMusicMix): "Use the
 * household's mix", and when off, their two sliders. Changeable only from
 * the phone that is theirs ([personMixEditable]); anywhere else it is said
 * in words with who can change it. A refusal (the phone stopped being
 * theirs meanwhile) is said in the server's words. Nothing on a shared
 * screen, which is nobody's (the web hides it there too).
 */
@Composable
internal fun PersonMusicMixSection(personId: Long, name: String, showHead: Boolean = true) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val shared = LocalSharedScreen.current
    val server by app.prefs.serverUrl.collectAsState()
    val token by app.prefs.deviceToken.collectAsState()
    val owners by app.prefs.deviceOwners.collectAsState()
    if (shared || token.isNullOrBlank()) return
    val owner = owners[ServerCredentials.normalize(server)]
    val editable = personMixEditable(personId, owner, shared)
    val read = rememberApi("person-mix", personId, token) { a -> readPersonMix(a.api, server, personId) }
    // The latest answer, kept with the server it came from.
    var saved by remember(personId, server) { mutableStateOf<PersonMix?>(null) }
    var busy by remember(personId, server) { mutableStateOf(false) }
    var refused by remember(personId, server) { mutableStateOf<String?>(null) }
    LaunchedEffect(read.data) { saved = null }
    val pm = saved ?: read.data

    fun put(override: MixSliders?, said: String) {
        val at = server
        busy = true
        refused = null
        scope.launch {
            try {
                val answer = savePersonMix(app.api, at, app.prefs.deviceId, app.prefs.claimKeyFor(at), personId, override)
                if (app.prefs.serverUrl.value == at) {
                    saved = answer
                    toast(said)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (app.prefs.serverUrl.value == at) {
                    refused = mixWriteRefusal(e) ?: failureText("save", e)
                    if (e is ApiException && e.status == 403) {
                        saved = null
                        read.refresh()
                    }
                }
            } finally {
                busy = false
            }
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (showHead) SectionLabel(PERSON_MIX_HEAD)
        if (pm == null) {
            Text(
                if (read.error != null) mixReadProblem(read.error) ?: "" else "reading…",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
            )
            return@Column
        }
        val own = pm.ownMix
        if (editable) {
            Row(
                Modifier.fillMaxWidth().heightIn(min = 48.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(USE_HOUSEHOLDS_MIX, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg, modifier = Modifier.weight(1f))
                Switch(
                    checked = !own,
                    enabled = !busy,
                    onCheckedChange = { useHousehold ->
                        if (useHousehold) put(null, "$name uses the household’s mix")
                        else put(ownMixFrom(pm.uses), "$name has their own mix")
                    },
                    modifier = Modifier.semantics { contentDescription = "$USE_HOUSEHOLDS_MIX for $name" },
                )
            }
            if (own) {
                MixSlider(MIX_DISCOVERY, pm.uses.discoveryFavorites, !busy) { put(withStep(pm.uses, true, it), "$name’s mix saved") }
                MixSlider(MIX_WANDER, pm.uses.closeWander, !busy) { put(withStep(pm.uses, false, it), "$name’s mix saved") }
                Text(personMixUsedWhen(name), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            }
        } else {
            Text(
                if (own) "$name has their own mix." else "$name uses the household’s mix.",
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fg,
            )
            if (own) {
                SaidRow(MIX_DISCOVERY.label, MIX_DISCOVERY.said(pm.uses.discoveryFavorites))
                SaidRow(MIX_WANDER.label, MIX_WANDER.said(pm.uses.closeWander))
            }
            Text(personMixReadOnlyNote(name), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
        }
        refused?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.err) }
    }
}
