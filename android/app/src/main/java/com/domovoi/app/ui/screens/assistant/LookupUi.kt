package com.domovoi.app.ui.screens.assistant

import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Public
import androidx.compose.material.icons.outlined.PublicOff
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.domovoi.app.assistant.AssistantUi
import com.domovoi.app.assistant.StoredConversation
import com.domovoi.app.assistant.lookup.LookupCard
import com.domovoi.app.assistant.lookup.OwnSearch
import com.domovoi.app.assistant.lookup.Proposal
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch

/** The chip's words for a phone chat (part 5, W1): "internet always on",
 *  "internet on for this chat" or "internet off" (it asks first). */
internal fun phoneInternetChip(ui: AssistantUi, c: StoredConversation): Pair<String, Boolean> = when {
    ui.alwaysAllowInternet -> "internet always on" to true
    c.internetAllowed -> "internet on for this chat" to true
    else -> "internet off" to false
}

internal const val CHIP_HINT_OFF = "Domovoi asks before it looks anything up online. Tap to allow it in this chat."
internal const val CHIP_HINT_ON = "This chat looks things up without asking. Tap to have it ask first again."
internal const val CHIP_HINT_ALWAYS = "Phone chats look things up without asking (model settings)."

@Composable
internal fun InternetChip(ui: AssistantUi, c: StoredConversation, onToggle: (Boolean) -> Unit) {
    val (label, on) = phoneInternetChip(ui, c)
    val hint = when {
        ui.alwaysAllowInternet -> CHIP_HINT_ALWAYS
        on -> CHIP_HINT_ON
        else -> CHIP_HINT_OFF
    }
    Row(
        Modifier
            .border(1.dp, if (on) Domovoi.colors.brand else Domovoi.colors.border, RoundedCornerShape(999.dp))
            .clickable(enabled = !ui.alwaysAllowInternet, role = Role.Switch, onClickLabel = hint) { onToggle(!c.internetAllowed) }
            .semantics { contentDescription = "$label. $hint" }
            .padding(horizontal = 10.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(5.dp),
    ) {
        Icon(if (on) Icons.Outlined.Public else Icons.Outlined.PublicOff, contentDescription = null,
            tint = if (on) Domovoi.colors.brand else Domovoi.colors.fgMuted, modifier = Modifier.size(14.dp))
        Text(label, style = MaterialTheme.typography.labelMedium, color = if (on) Domovoi.colors.fg else Domovoi.colors.fgMuted)
    }
}

internal fun cardStateText(state: String): String = when (state) {
    "allowed_once" -> "allowed once"
    "allowed_chat" -> "allowed in this chat"
    "declined" -> "not looked up"
    else -> state
}

/** The permission card: the exact words, where they go, and three answers. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun LookupCardView(card: LookupCard, enabled: Boolean, onDecide: (String) -> Unit) {
    val page = card.kind == Proposal.PAGE
    Column(
        Modifier.widthIn(max = 480.dp).fillMaxWidth()
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(10.dp))
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(Icons.Outlined.Public, contentDescription = null, tint = Domovoi.colors.fg, modifier = Modifier.size(16.dp))
            Text(if (page) "May I read this page?" else "May I look this up online?",
                style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg)
        }
        Text(if (page) card.query else "“${card.query}”", style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
        Text("goes to ${card.destination}", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
        if (card.state == LookupCard.PENDING) {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { onDecide("once") }, enabled = enabled) { Text("Allow once") }
                OutlinedButton(onClick = { onDecide("chat") }, enabled = enabled) { Text("Allow in this chat") }
                TextButton(onClick = { onDecide("no") }, enabled = enabled) { Text("Not now", color = Domovoi.colors.fgMuted) }
            }
        } else {
            Text(cardStateText(card.state), style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
        }
    }
}

/** The model settings' "Looking things up" card: Always allow internet,
 *  and "Use your own search" (kept sealed in the Android keystore). */
@Composable
internal fun LookupSettingsCard(ui: AssistantUi, actions: AssistantActions) {
    if (!ui.lookups) return
    val scope = rememberCoroutineScope()
    var choice by rememberSaveable { mutableStateOf(ui.ownSearchKind ?: "") }
    var url by rememberSaveable { mutableStateOf(ui.ownSearchUrl) }
    var key by remember { mutableStateOf("") }
    var result by remember { mutableStateOf<String?>(null) }
    DomovoiCard(Modifier.fillMaxWidth()) {
        Text("Looking things up", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
        Text(
            "A phone chat can look things up when you allow it: Wikipedia, or your home server's search while " +
                "you're at home. Nothing goes online without your allow, and nothing goes to Coders Farm.",
            style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
        )
        Spacer(Modifier.height(6.dp))
        Row(
            Modifier.toggleable(value = ui.alwaysAllowInternet, role = Role.Switch, onValueChange = actions.setAlwaysAllowInternet),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Switch(checked = ui.alwaysAllowInternet, onCheckedChange = null)
            Column(Modifier.padding(start = 10.dp)) {
                Text("Always allow internet", style = MaterialTheme.typography.labelLarge, color = Domovoi.colors.fg)
                Text("Off: each lookup shows the exact words and where they go, and asks first.",
                    style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            }
        }
        Spacer(Modifier.height(8.dp))
        Text("Use your own search (away from home)", style = MaterialTheme.typography.labelLarge, color = Domovoi.colors.fg)
        listOf("" to "Wikipedia only", OwnSearch.SEARXNG to "Your own SearXNG", OwnSearch.BRAVE to "Brave Search, with your key")
            .forEach { (value, label) ->
                Row(
                    Modifier.fillMaxWidth().selectable(selected = choice == value, role = Role.RadioButton) { choice = value; result = null },
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    RadioButton(selected = choice == value, onClick = null)
                    Text(label, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg,
                        modifier = Modifier.padding(start = 6.dp))
                }
            }
        if (choice == OwnSearch.SEARXNG) {
            LocalComposerField.current(url, { url = it; result = null }, Modifier.fillMaxWidth())
            Text("Its address, with JSON output on (search.formats: json).", style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted)
        }
        if (choice == OwnSearch.BRAVE) {
            LocalComposerField.current(key, { key = it; result = null }, Modifier.fillMaxWidth())
            Text(
                (if (ui.ownSearchKind == OwnSearch.BRAVE) "A key is saved; paste a new one to replace it. " else "") +
                    "Brave charges for searches after a monthly free credit and asks for a card. It sells AI " +
                    "answers separately; Domovoi uses its plain web search, so check Brave's terms cover your use. " +
                    "Its terms don't let you share your key. The key stays in this phone's keystore and goes only to Brave.",
                style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = {
                val own = when (choice) {
                    OwnSearch.SEARXNG -> url.trim().takeIf { it.startsWith("http://") || it.startsWith("https://") }
                        ?.let { OwnSearch(OwnSearch.SEARXNG, url = it) }
                    OwnSearch.BRAVE -> key.trim().takeIf { it.isNotEmpty() }?.let { OwnSearch(OwnSearch.BRAVE, key = it) }
                    else -> null
                }
                if (own?.kind == OwnSearch.BRAVE && !com.domovoi.app.assistant.lookup.BRAVE_KEY.matches(own.key)) {
                    result = "That isn't a Brave Search key (letters, digits, - and _ only). Paste it again."
                } else if (choice.isNotEmpty() && own == null && !(choice == OwnSearch.BRAVE && ui.ownSearchKind == OwnSearch.BRAVE)) {
                    result = if (choice == OwnSearch.BRAVE) "Paste your Brave Search key first." else "Type an address starting with http:// or https://."
                } else if (own != null || choice.isEmpty()) {
                    actions.setOwnSearch(own)
                    key = ""
                }
            }) { Text("save", color = Domovoi.colors.brand) }
            if (choice.isNotEmpty()) {
                TextButton(onClick = {
                    val own = when (choice) {
                        OwnSearch.SEARXNG -> OwnSearch(OwnSearch.SEARXNG, url = url.trim())
                        OwnSearch.BRAVE -> key.trim().takeIf { it.isNotEmpty() }?.let { OwnSearch(OwnSearch.BRAVE, key = it) }
                        else -> null
                    }
                    scope.launch { result = actions.testOwnSearch(own) }
                }) { Text("test", color = Domovoi.colors.brand) }
            }
        }
        result?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fg) }
        Text("If your own search fails, the answer says so and comes from Wikipedia instead.",
            style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
    }
}
