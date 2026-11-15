package com.domovoi.app.ui.screens.satellites

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Speaker
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.runAction
import com.domovoi.app.ui.screens.music.GROUPS_EXPLAINER
import com.domovoi.app.ui.screens.music.GROUP_DELETE_BODY
import com.domovoi.app.ui.screens.music.GROUP_NAME_MAX
import com.domovoi.app.ui.screens.music.MusicGroup
import com.domovoi.app.ui.screens.music.MusicGroupsApi
import com.domovoi.app.ui.screens.music.apiErrorText
import com.domovoi.app.ui.screens.music.groupDeleteTitle
import com.domovoi.app.ui.screens.music.groupDeletedToast
import com.domovoi.app.ui.screens.music.groupFormReady
import com.domovoi.app.ui.screens.music.groupFormRooms
import com.domovoi.app.ui.screens.music.groupSaveFailure
import com.domovoi.app.ui.screens.music.groupSavedToast
import com.domovoi.app.ui.screens.music.groupsEmptyText
import com.domovoi.app.ui.screens.music.groupsFor
import com.domovoi.app.ui.screens.music.rememberMusicGroups
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch

/*
 * Settings → Rooms → Groups (V060) — web/static/satellites.jsx
 * MusicGroupsCard. Named sets of rooms the household makes ("upstairs",
 * "the north wing"), so "play jazz upstairs" plays in all of them at once:
 * one shared queue, the same music in each. Made, renamed, re-roomed and
 * deleted here (no screen of its own). "Everywhere" needs no group.
 *
 * The household's: drawn only when the groups read answered this phone on
 * this server ([groupsFor]); an unpaired phone, a refusal (401/403) and an
 * older server (404) leave the card out without a word.
 */

private const val NEW = -1L

@Composable
internal fun MusicGroupsCard(sats: List<Satellite>) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val token by app.prefs.deviceToken.collectAsState()
    val server by app.prefs.serverUrl.collectAsState()
    val read = rememberMusicGroups()
    val list = groupsFor(read.data, server, !token.isNullOrBlank()) ?: return

    var editing by remember(server) { mutableStateOf<Long?>(null) }   // NEW | group id | null
    var busy by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf<MusicGroup?>(null) }
    val groups = list.groups
    val roomIds = sats.map { it.room_id }.distinct().sorted()
    val api = remember(app) { MusicGroupsApi(app.api) }

    // The app's scope: a save already sent finishes and says how it went
    // even if the card scrolls away. Its answer is said only while the app
    // still talks to the server it went to (read-after-await).
    fun save(id: Long, name: String, rooms: List<String>) {
        if (busy) return
        val at = app.prefs.serverUrl.value
        busy = true
        app.scope.launch {
            try {
                runAction { if (id == NEW) api.create(name, rooms) else api.update(id, name, rooms) }
                    .onSuccess {
                        if (app.prefs.serverUrl.value != at) return@onSuccess
                        toast(groupSavedToast(name, made = id == NEW))
                        editing = null
                    }
                    .onFailure { if (app.prefs.serverUrl.value == at) toast(groupSaveFailure(it)) }
            } finally {
                busy = false
                read.refresh()
            }
        }
    }

    fun remove(g: MusicGroup) {
        if (busy) return
        val at = app.prefs.serverUrl.value
        busy = true
        app.scope.launch {
            try {
                runAction { api.delete(g.id) }
                    .onSuccess { if (app.prefs.serverUrl.value == at) toast(groupDeletedToast(g.name)) }
                    .onFailure {
                        if (app.prefs.serverUrl.value == at) toast("delete the group failed: ${apiErrorText(it)}")
                    }
            } finally {
                busy = false
                read.refresh()
            }
        }
    }

    DomovoiCard(Modifier.fillMaxWidth(), padding = 0) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp).heightIn(min = 48.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Icon(Icons.Filled.Speaker, contentDescription = null, tint = Domovoi.colors.fgMuted, modifier = Modifier.size(16.dp))
            Text(
                "Groups",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = Domovoi.colors.fg,
                modifier = Modifier.weight(1f),
            )
            if (editing == null && roomIds.size >= 2) {
                OutlinedButton(onClick = { editing = NEW }, enabled = !busy, modifier = Modifier.heightIn(min = 48.dp)) {
                    Icon(Icons.Filled.Add, contentDescription = null, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("new group")
                }
            }
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        Text(
            GROUPS_EXPLAINER,
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgMuted,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 10.dp),
        )
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        if (editing == NEW) {
            GroupForm(null, groupFormRooms(roomIds, null), busy, onSave = { n, r -> save(NEW, n, r) }) { editing = null }
        }
        if (groups.isEmpty() && editing != NEW) {
            Text(
                groupsEmptyText(roomIds.size),
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fgFaint,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 14.dp),
            )
        }
        groups.forEach { g ->
            if (editing == g.id) {
                GroupForm(g, groupFormRooms(roomIds, g), busy, onSave = { n, r -> save(g.id, n, r) }) { editing = null }
            } else {
                GroupRow(g, enabled = !busy && editing == null, onEdit = { editing = g.id }, onDelete = { confirmDelete = g })
            }
        }
    }

    confirmDelete?.let { g ->
        ConfirmDialog(
            title = groupDeleteTitle(g.name),
            body = GROUP_DELETE_BODY,
            confirmLabel = "delete",
            destructive = true,
            onConfirm = { remove(g) },
            onDismiss = { confirmDelete = null },
        )
    }
}

@Composable
private fun GroupRow(g: MusicGroup, enabled: Boolean, onEdit: () -> Unit, onDelete: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(start = 16.dp, end = 6.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(g.name, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, color = Domovoi.colors.fg)
            Text(g.rooms.joinToString(", "), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
        }
        IconButton(onClick = onEdit, enabled = enabled) {
            Icon(Icons.Filled.Edit, contentDescription = "edit ${g.name}", tint = Domovoi.colors.fgMuted)
        }
        IconButton(onClick = onDelete, enabled = enabled) {
            Icon(Icons.Filled.Delete, contentDescription = "delete ${g.name}", tint = Domovoi.colors.fgMuted)
        }
    }
    HorizontalDivider(color = Domovoi.colors.borderSoft)
}

/** Name the group and pick its rooms (two or more). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun GroupForm(
    initial: MusicGroup?,
    roomIds: List<String>,
    busy: Boolean,
    onSave: (String, List<String>) -> Unit,
    onCancel: () -> Unit,
) {
    var name by remember(initial?.id) { mutableStateOf(initial?.name.orEmpty()) }
    var picked by remember(initial?.id) { mutableStateOf(initial?.rooms.orEmpty().toSet()) }
    val ready = groupFormReady(name, picked, busy)
    Column(
        Modifier.fillMaxWidth().background(Domovoi.colors.sunken).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        OutlinedTextField(
            value = name,
            onValueChange = { name = it.take(GROUP_NAME_MAX) },
            label = { Text("name") },
            placeholder = { Text("upstairs", color = Domovoi.colors.fgSubtle) },
            singleLine = true,
            textStyle = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.fillMaxWidth().semantics { contentDescription = "group name" },
        )
        Text("rooms (two or more)", style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            roomIds.forEach { r ->
                FilterChip(
                    selected = r in picked,
                    onClick = { picked = if (r in picked) picked - r else picked + r },
                    label = { Text(r, style = MaterialTheme.typography.labelMedium) },
                    leadingIcon = if (r in picked) {
                        { Icon(Icons.Filled.Check, contentDescription = null, modifier = Modifier.size(16.dp)) }
                    } else null,
                    modifier = Modifier.heightIn(min = 48.dp),
                )
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End)) {
            TextButton(onClick = onCancel, enabled = !busy, modifier = Modifier.heightIn(min = 48.dp)) { Text("cancel") }
            Button(
                onClick = { if (ready) onSave(name.trim(), picked.toList().sorted()) },
                enabled = ready,
                modifier = Modifier.heightIn(min = 48.dp),
            ) {
                Icon(Icons.Filled.Check, contentDescription = null, modifier = Modifier.size(16.dp))
                Spacer(Modifier.width(6.dp))
                Text("save")
            }
        }
    }
    HorizontalDivider(color = Domovoi.colors.borderSoft)
}
