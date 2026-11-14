package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ThumbDown
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.PhoneUpNext
import com.domovoi.app.player.UP_NEXT_AUTOPLAY_LABEL
import com.domovoi.app.player.UpNextRules
import com.domovoi.app.player.UpNextState
import com.domovoi.app.player.phoneUpNextForShown
import com.domovoi.app.player.phoneUpNextLine
import com.domovoi.app.player.phoneUpNextShown
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.launchAction
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable

/*
 * The phone's own "up next" section (P8; the web's PlayerUpNextHere), under
 * the explicit queue rows of the Player tab and the player sheet: what the
 * mix is built from, the Autoplay switch, "for" whom (two people or more),
 * one muted line, then each pick still to come with why it was picked,
 * "less like this", remove, and tap to play it now. No new screen (owner).
 *
 * No misleading controls: nothing at all for an unpaired app, a queue with
 * no library song, a play the server refused Up next for, or while casting
 * (the room's own Up next shows in its queue then).
 */

/** What the queue panels need to draw the phone's Up next; see [rememberPhoneUpNextView]. */
internal data class PhoneUpNextView(
    /** The section shows ([phoneUpNextShown]). */
    val shown: Boolean,
    val state: UpNextState?,
    /** What the switch says: the session's, else this phone's preference. */
    val autoplay: Boolean,
) {
    /** The queue rows drawn above the section (real indices): all of them
     *  when the section is hidden, so no pick plays unseen. */
    fun explicitRows(queue: List<PlayItem>, index: Int): List<Int> =
        if (shown) UpNextRules.explicitRows(queue, index) else queue.indices.toList()

    fun pickRows(queue: List<PlayItem>, index: Int): List<Int> =
        if (shown) UpNextRules.pickRows(queue, index) else emptyList()

    /** `queue · N`: not the waiting picks. */
    fun count(queue: List<PlayItem>, index: Int): Int = explicitRows(queue, index).size

    companion object {
        val NONE = PhoneUpNextView(shown = false, state = null, autoplay = true)

        fun of(paired: Boolean, queue: List<PlayItem>, state: UpNextState?, casting: Boolean, autoplayPref: Boolean) =
            PhoneUpNextView(
                shown = phoneUpNextShown(paired, queue, state, casting),
                state = state,
                autoplay = state?.autoplay ?: autoplayPref,
            )
    }
}

/**
 * Collects what [PhoneUpNextView] is made of. An app without Up next wired
 * (the JVM tests' player) reads nothing more and shows none.
 */
@Composable
internal fun rememberPhoneUpNextView(queue: List<PlayItem>, casting: Boolean): PhoneUpNextView {
    val app = LocalApp.current
    val upNext = app.player.upNext ?: return PhoneUpNextView.NONE
    val state by upNext.state.collectAsState()
    val token by app.prefs.deviceToken.collectAsState()
    val autoplayPref by app.prefs.upNextAutoplay.collectAsState()
    return remember(queue, state, token, casting, autoplayPref) {
        PhoneUpNextView.of(!token.isNullOrBlank(), queue, state, casting, autoplayPref)
    }
}

/** The section's items, after the explicit queue rows. [keyPrefix] keeps
 *  keys unique in the list it joins. */
internal fun LazyListScope.phoneUpNext(
    view: PhoneUpNextView,
    queue: List<PlayItem>,
    index: Int,
    keyPrefix: String,
) {
    if (!view.shown) return
    val rows = view.pickRows(queue, index)
    item(key = "$keyPrefix-up-next-head") { PhoneUpNextHead(view, rows.size) }
    items(
        count = rows.size,
        key = { "$keyPrefix-up-next-${rows[it]}-${queue[rows[it]].uid}" },
        contentType = { "$keyPrefix-up-next-pick" },
    ) { k ->
        val i = rows[k]
        PhoneUpNextRow(i, queue[i], view.state)
    }
}

/** `GET /api/people`'s rows, as the "for" selector reads them. */
@Serializable
internal data class UpNextPersonRow(val id: Long = 0, val name: String? = null)

@Composable
private fun PhoneUpNextHead(view: PhoneUpNextView, waiting: Int) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = app.scope
    val upNext = app.player.upNext ?: return
    val u = view.state
    val basis = u?.let { upNextBasis(it.mode, it.seed) }.orEmpty()
    val line = phoneUpNextLine(u, view.autoplay, waiting)
    // The roster is a household read; failing, the selector just isn't there.
    val people = rememberApi("up-next-people") { it.api.get("/api/people").decode<List<UpNextPersonRow>>() }
    val roster = people.data.orEmpty().filter { it.id > 0 }
    Column(Modifier.fillMaxWidth().padding(top = 10.dp)) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 48.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Column(Modifier.weight(1f)) {
                SectionLabel(UP_NEXT_HEAD)
                if (basis.isNotEmpty()) {
                    Text(
                        basis,
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            Text(AUTOPLAY, style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted)
            Switch(
                checked = view.autoplay,
                onCheckedChange = { on ->
                    scope.launchAction(
                        action = { upNext.setAutoplay(on) },
                        onSuccess = {},
                        onFailure = { e -> if (!isAuthRefusal(e)) toast("Couldn't tell the home server: ${apiErrorText(e)}") },
                    )
                },
                modifier = Modifier.semantics { contentDescription = UP_NEXT_AUTOPLAY_LABEL },
            )
        }
        if (phoneUpNextForShown(roster.size, u)) {
            UpNextFor(app, upNext, roster, u, toast)
        }
        line?.let {
            Text(
                it,
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.padding(bottom = 4.dp).semantics { liveRegion = LiveRegionMode.Polite },
            )
        }
    }
}

/** "for [household | names]": whose taste the mix follows. */
@Composable
private fun UpNextFor(
    app: AppContainer,
    upNext: PhoneUpNext,
    roster: List<UpNextPersonRow>,
    u: UpNextState?,
    toast: (String) -> Unit,
) {
    var open by remember { mutableStateOf(false) }
    val who = u?.person?.let { p -> roster.firstOrNull { it.id == p.id }?.name ?: p.name } ?: HOUSEHOLD
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text("for", style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted)
        Box {
            TextButton(
                onClick = { open = true },
                modifier = Modifier.heightIn(min = 48.dp).semantics { contentDescription = "whose taste this mix follows: $who" },
            ) {
                Text(who, color = Domovoi.colors.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Icon(Icons.Filled.ArrowDropDown, contentDescription = null, tint = Domovoi.colors.fgMuted)
            }
            DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
                val pick: (Long?) -> Unit = { pid ->
                    open = false
                    app.scope.launchAction(
                        action = { upNext.setListener(pid) },
                        onSuccess = {},
                        onFailure = { e -> if (!isAuthRefusal(e)) toast("Couldn't change whose mix this is: ${apiErrorText(e)}") },
                    )
                }
                DropdownMenuItem(text = { Text(HOUSEHOLD) }, onClick = { pick(null) })
                roster.forEach { p ->
                    DropdownMenuItem(text = { Text(p.name ?: "person ${p.id}") }, onClick = { pick(p.id) })
                }
            }
        }
    }
}

/** One pick still to come: tap = play it now; less like this; remove. */
@Composable
private fun PhoneUpNextRow(i: Int, item: PlayItem, u: UpNextState?) {
    val app = LocalApp.current
    val toast = LocalToast.current
    var busy by remember(item.uid, item.upNextSeq) { mutableStateOf(false) }
    // "less like this" only on a pick of the session the phone follows now.
    val canLessLike = u?.id != null && item.sessionId == u.id && item.upNextSeq != null
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 56.dp)
            .background(Color.Transparent, RoundedCornerShape(6.dp)),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Row(
            Modifier
                .weight(1f)
                .clickable(onClickLabel = "play now") { app.player.jumpTo(i) }
                .padding(vertical = 6.dp, horizontal = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(
                Icons.Filled.AutoAwesome, contentDescription = "up next pick",
                tint = Domovoi.colors.fgMuted, modifier = Modifier.size(14.dp),
            )
            Column(Modifier.weight(1f)) {
                Text(
                    item.title,
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fg,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(
                        item.artist ?: "—",
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                    // Why it was picked: a muted chip ("same artist", "90s").
                    item.reasonLabel?.let { Pill(it) }
                }
            }
        }
        if (canLessLike) {
            IconButton(
                onClick = {
                    busy = true
                    app.scope.launch {
                        try {
                            val gone = app.player.upNext?.lessLikeThis(i).orEmpty()
                            if (gone.isNotEmpty()) toast(lessLikeNote(item.artist, gone.size))
                        } catch (e: CancellationException) {
                            throw e
                        } catch (e: Exception) {
                            if (!isAuthRefusal(e)) toast("Couldn't change the mix: ${apiErrorText(e)}")
                        } finally {
                            busy = false
                        }
                    }
                },
                enabled = !busy,
                modifier = Modifier.size(48.dp),
            ) {
                Icon(
                    Icons.Filled.ThumbDown, contentDescription = lessLikeLabel(item.artist),
                    tint = Domovoi.colors.fgMuted, modifier = Modifier.size(18.dp),
                )
            }
        }
        IconButton(onClick = { app.player.removeAt(i) }, modifier = Modifier.size(48.dp)) {
            Icon(
                Icons.Filled.Close, contentDescription = "remove ${item.title} from up next",
                tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(18.dp),
            )
        }
    }
}

/** The "for" selector's word for nobody in particular. */
internal const val HOUSEHOLD = "household"

/** A refused sign-in: the app's pairing screen says so, not a toast. */
private fun isAuthRefusal(e: Throwable): Boolean = e is ApiException && e.deviceTokenRequired
