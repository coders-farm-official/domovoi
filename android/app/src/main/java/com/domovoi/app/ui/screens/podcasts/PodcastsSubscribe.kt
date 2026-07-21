package com.domovoi.app.ui.screens.podcasts

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.net.URLEncoder

/** "Subscribe to a podcast" modal — search (iTunes discovery) or by RSS URL. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun SubscribeDialog(onClose: () -> Unit, onDone: () -> Unit) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()

    var tab by remember { mutableStateOf("search") }
    var q by remember { mutableStateOf("") }
    var results by remember { mutableStateOf<List<DiscoverRow>>(emptyList()) }
    var feedUrl by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }

    fun search() {
        if (q.isBlank() || busy) return
        busy = true
        scope.launch {
            runCatching {
                app.api.get("/api/podcasts/discover?q=${URLEncoder.encode(q.trim(), "UTF-8")}")
                    .decode<List<DiscoverRow>>()
            }
                .onSuccess { results = it }
                .onFailure { toast("Discovery needs internet") }
            busy = false
        }
    }

    fun subscribe(url: String?) {
        val feed = url?.trim().orEmpty()
        if (feed.isEmpty() || busy) return
        busy = true
        scope.launch {
            runCatching {
                app.api.post(
                    "/api/podcasts/subscriptions",
                    buildJsonObject { put("feed_url", feed) },
                )
            }
                .onSuccess {
                    toast("Subscribed")
                    onDone()
                }
                .onFailure { toast("Subscribe failed") }
            busy = false
        }
    }

    Dialog(onDismissRequest = onClose) {
        Surface(
            shape = RoundedCornerShape(10.dp),
            color = Domovoi.colors.card,
            border = BorderStroke(1.dp, Domovoi.colors.border),
        ) {
            Column(Modifier.padding(16.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "Subscribe to a podcast",
                        style = MaterialTheme.typography.titleMedium,
                        color = Domovoi.colors.fg,
                        modifier = Modifier.weight(1f),
                    )
                    IconButton(onClick = onClose) {
                        Icon(Icons.Outlined.Close, "close", tint = Domovoi.colors.fgMuted)
                    }
                }
                Row(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    modifier = Modifier.padding(bottom = 12.dp),
                ) {
                    TabPill("search", tab == "search") { tab = "search" }
                    TabPill("by rss url", tab == "url") { tab = "url" }
                }

                if (tab == "search") {
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        OutlinedTextField(
                            value = q,
                            onValueChange = { q = it },
                            placeholder = { Text("show name…", color = Domovoi.colors.fgSubtle) },
                            singleLine = true,
                            modifier = Modifier.weight(1f),
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                            keyboardActions = KeyboardActions(onSearch = { search() }),
                        )
                        Button(onClick = { search() }, enabled = !busy) { Text("search") }
                    }
                    LazyColumn(Modifier.heightIn(max = 320.dp).padding(top = 12.dp)) {
                        items(results) { r ->
                            Column {
                                Row(
                                    Modifier.fillMaxWidth().padding(vertical = 8.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                                ) {
                                    PodArt(r.artwork, 40)
                                    Column(Modifier.weight(1f)) {
                                        Text(
                                            r.title ?: "podcast",
                                            style = MaterialTheme.typography.titleSmall,
                                            color = Domovoi.colors.fg,
                                            maxLines = 1,
                                            overflow = TextOverflow.Ellipsis,
                                        )
                                        r.author?.let {
                                            Text(
                                                it,
                                                style = MaterialTheme.typography.bodySmall,
                                                color = Domovoi.colors.fgMuted,
                                                maxLines = 1,
                                                overflow = TextOverflow.Ellipsis,
                                            )
                                        }
                                    }
                                    OutlinedButton(
                                        onClick = { subscribe(r.feed_url) },
                                        enabled = !busy,
                                    ) { Text("add") }
                                }
                                HorizontalDivider(color = Domovoi.colors.borderSoft)
                            }
                        }
                    }
                } else {
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        OutlinedTextField(
                            value = feedUrl,
                            onValueChange = { feedUrl = it },
                            placeholder = { Text("https://…/feed.xml", color = Domovoi.colors.fgSubtle) },
                            singleLine = true,
                            modifier = Modifier.weight(1f),
                        )
                        Button(
                            onClick = { subscribe(feedUrl) },
                            enabled = !busy && feedUrl.isNotBlank(),
                        ) { Text("subscribe") }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TabPill(label: String, active: Boolean, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        shape = RoundedCornerShape(999.dp),
        color = if (active) Domovoi.colors.brandSoft else Domovoi.colors.card,
        border = BorderStroke(1.dp, Domovoi.colors.border),
    ) {
        Text(
            label,
            style = MaterialTheme.typography.labelMedium,
            color = Domovoi.colors.fg,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 5.dp),
        )
    }
}
