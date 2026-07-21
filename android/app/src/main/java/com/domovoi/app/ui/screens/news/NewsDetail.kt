package com.domovoi.app.ui.screens.news

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.KeyboardArrowDown
import androidx.compose.material.icons.outlined.KeyboardArrowUp
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.StarBorder
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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

/** The selected person's briefing + topic manager + saved feed. */
@Composable
internal fun PersonNewsDetail(person: NewsPerson, categories: List<String>) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val pid = person.id

    val topics = rememberApi(pid, "topics", eventTypes = setOf("news.changed")) {
        it.api.get("/api/news/people/$pid/topics").decode<List<NewsTopic>>()
    }
    val items = rememberApi(pid, "items", eventTypes = setOf("news.changed")) {
        it.api.get("/api/news/people/$pid/items").decode<List<NewsItemRow>>()
    }
    val briefing = rememberApi(pid, "briefing", eventTypes = setOf("news.changed")) {
        it.api.get("/api/news/people/$pid/briefing").decode<NewsBriefing>()
    }
    var polling by remember { mutableStateOf(false) }

    fun pollNow() {
        if (polling) return
        polling = true
        scope.launch {
            runCatching { app.api.post("/api/news/poll?person_id=$pid").jsonObject }
                .onSuccess { r ->
                    val house = r["house"]?.jsonPrimitive?.intOrNull ?: 0
                    val fromTopics = r["topics"]?.jsonPrimitive?.intOrNull ?: 0
                    val n = house + fromTopics
                    toast("polled — $n new item${if (n == 1) "" else "s"}")
                    topics.refresh()
                    items.refresh()
                    briefing.refresh()
                }
                .onFailure { toast("poll failed (offline?)") }
            polling = false
        }
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    "${person.name ?: "?"}'s news",
                    style = MaterialTheme.typography.headlineMedium,
                    color = Domovoi.colors.fg,
                )
                Text(
                    "topics of interest, discovered feeds, and their saved stories",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
            OutlinedButton(onClick = { pollNow() }, enabled = !polling) {
                Text(if (polling) "polling…" else "poll now")
            }
        }

        BriefingCard(briefing.data)
        TopicsCard(
            pid = pid,
            topics = topics.data ?: emptyList(),
            categories = categories,
            onChanged = {
                topics.refresh()
                items.refresh()
            },
        )
        SavedFeedCard(items.data ?: emptyList(), onChanged = items.refresh)
    }
}

@Composable
private fun BriefingCard(b: NewsBriefing?) {
    DomovoiCard(modifier = Modifier.fillMaxWidth()) {
        SectionLabel("current briefing")
        Text(
            if (b?.generated_at != null) "generated ${relTime(b.generated_at)}"
            else "the latest spoken digest",
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgSubtle,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            b?.briefing ?: "No briefing yet. Turn on auto-fetch (or poll now) to generate one.",
            style = MaterialTheme.typography.bodyMedium,
            color = if (b?.briefing != null) Domovoi.colors.fg else Domovoi.colors.fgMuted,
        )
    }
}

// ---------------------------------------------------------------------------
// Topic manager — category chips + free-form add + expandable topic rows.
// ---------------------------------------------------------------------------

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TopicsCard(
    pid: Long,
    topics: List<NewsTopic>,
    categories: List<String>,
    onChanged: () -> Unit,
) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var freeform by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var confirmRemove by remember { mutableStateOf<NewsTopic?>(null) }
    val activeCats = topics.filter { it.kind == "category" }.mapNotNull { it.topic }.toSet()

    fun toggleCategory(cat: String) {
        scope.launch {
            runCatching {
                val existing = topics.firstOrNull { it.kind == "category" && it.topic == cat }
                if (existing != null) {
                    app.api.delete("/api/news/topics/${existing.id}")
                } else {
                    app.api.post(
                        "/api/news/people/$pid/topics",
                        buildJsonObject {
                            put("kind", "category")
                            put("topic", cat)
                        },
                    )
                }
            }
                .onSuccess { onChanged() }
                .onFailure { toast("couldn't update category") }
        }
    }

    fun addFreeform() {
        val topic = freeform.trim()
        if (topic.isEmpty() || busy) return
        busy = true
        scope.launch {
            runCatching {
                app.api.post(
                    "/api/news/people/$pid/topics",
                    buildJsonObject {
                        put("kind", "freeform")
                        put("topic", topic)
                    },
                )
            }
                .onSuccess {
                    freeform = ""
                    toast("topic added — discovering feeds…")
                    onChanged()
                }
                .onFailure { toast("couldn't add topic") }
            busy = false
        }
    }

    DomovoiCard(modifier = Modifier.fillMaxWidth()) {
        SectionLabel("topics of interest")
        Text(
            "Categories give broad coverage; free-form covers the niche.",
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgSubtle,
        )
        Spacer(Modifier.height(12.dp))

        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            categories.forEach { cat ->
                CategoryChip(cat, cat in activeCats) { toggleCategory(cat) }
            }
        }
        Spacer(Modifier.height(12.dp))

        Row(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = freeform,
                onValueChange = { freeform = it },
                placeholder = {
                    Text("add a free-form topic (e.g. Formula 1)…", color = Domovoi.colors.fgSubtle)
                },
                singleLine = true,
                modifier = Modifier.weight(1f),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { addFreeform() }),
            )
            Button(onClick = { addFreeform() }, enabled = !busy && freeform.isNotBlank()) {
                Text("add topic")
            }
        }
        Spacer(Modifier.height(12.dp))

        if (topics.isEmpty()) {
            Text(
                "No topics yet. Pick a category above or add a free-form topic.",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
            )
        } else {
            topics.forEach { t ->
                TopicRow(t, onRemove = { confirmRemove = t }, onChanged = onChanged)
                Spacer(Modifier.height(8.dp))
            }
        }
    }

    confirmRemove?.let { t ->
        ConfirmDialog(
            title = "remove topic",
            body = "Remove \"${t.topic}\" and its feeds?",
            confirmLabel = "remove",
            destructive = true,
            onConfirm = {
                scope.launch {
                    runCatching { app.api.delete("/api/news/topics/${t.id}") }
                        .onSuccess { onChanged() }
                        .onFailure { toast("couldn't remove topic") }
                }
            },
            onDismiss = { confirmRemove = null },
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CategoryChip(cat: String, on: Boolean, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        shape = RoundedCornerShape(999.dp),
        color = if (on) Domovoi.colors.brand else Domovoi.colors.card,
        border = if (on) null else BorderStroke(1.dp, Domovoi.colors.border),
    ) {
        Text(
            cat,
            style = MaterialTheme.typography.labelMedium,
            color = if (on) Domovoi.colors.brandFg else Domovoi.colors.fgMuted,
            modifier = Modifier.padding(horizontal = 11.dp, vertical = 5.dp),
        )
    }
}

/** One topic, expandable to its feeds (lazy-loaded on first open). */
@Composable
private fun TopicRow(topic: NewsTopic, onRemove: () -> Unit, onChanged: () -> Unit) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var open by remember(topic.id) { mutableStateOf(false) }
    var feeds by remember(topic.id) { mutableStateOf<List<NewsFeedRow>?>(null) }
    var newUrl by remember(topic.id) { mutableStateOf("") }
    var busy by remember(topic.id) { mutableStateOf(false) }

    suspend fun loadFeeds() {
        feeds = runCatching {
            app.api.get("/api/news/topics/${topic.id}/feeds").decode<List<NewsFeedRow>>()
        }.getOrDefault(emptyList())
    }

    LaunchedEffect(open) {
        if (open && feeds == null) loadFeeds()
    }

    fun addFeed() {
        val url = newUrl.trim()
        if (url.isEmpty() || busy) return
        busy = true
        scope.launch {
            runCatching {
                app.api.post(
                    "/api/news/topics/${topic.id}/feeds",
                    buildJsonObject { put("url", url) },
                )
            }
                .onSuccess {
                    newUrl = ""
                    loadFeeds()
                    toast("feed added")
                    onChanged()
                }
                .onFailure { toast("couldn't add that feed") }
            busy = false
        }
    }

    Column(
        Modifier.fillMaxWidth()
            .border(1.dp, Domovoi.colors.borderSoft, RoundedCornerShape(8.dp))
            .padding(horizontal = 10.dp, vertical = 6.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            IconButton(onClick = { open = !open }, modifier = Modifier.size(28.dp)) {
                Icon(
                    if (open) Icons.Outlined.KeyboardArrowUp else Icons.Outlined.KeyboardArrowDown,
                    contentDescription = if (open) "collapse" else "expand",
                    tint = Domovoi.colors.fgMuted,
                )
            }
            Pill(topic.kind ?: "topic", if (topic.kind == "category") Tone.Brand else Tone.Idle)
            Text(
                topic.topic ?: "—",
                style = MaterialTheme.typography.titleSmall,
                color = Domovoi.colors.fg,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Text(
                "${topic.feed_count} feed${if (topic.feed_count == 1) "" else "s"}",
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                color = Domovoi.colors.fgMuted,
            )
            IconButton(onClick = onRemove, modifier = Modifier.size(28.dp)) {
                Icon(Icons.Outlined.Close, "remove topic", tint = Domovoi.colors.fgMuted)
            }
        }

        if (open) {
            HorizontalDivider(color = Domovoi.colors.borderSoft)
            Column(Modifier.padding(start = 20.dp, top = 4.dp, bottom = 4.dp)) {
                when {
                    feeds == null -> Text(
                        "loading feeds…",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                        modifier = Modifier.padding(vertical = 6.dp),
                    )
                    feeds.orEmpty().isEmpty() -> Text(
                        if (topic.kind == "freeform") {
                            "No feeds yet — discovery found none; add one below."
                        } else {
                            "No feeds yet."
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                        modifier = Modifier.padding(vertical = 6.dp),
                    )
                    else -> feeds.orEmpty().forEach { f ->
                        FeedRow(
                            f,
                            onRevalidate = {
                                scope.launch {
                                    runCatching {
                                        app.api.post("/api/news/feeds/${f.id}/validate")
                                    }
                                        .onSuccess { loadFeeds() }
                                        .onFailure { toast("couldn't validate") }
                                }
                            },
                            onRemove = {
                                scope.launch {
                                    runCatching {
                                        app.api.delete("/api/news/topics/${topic.id}/feeds/${f.id}")
                                    }
                                        .onSuccess {
                                            loadFeeds()
                                            onChanged()
                                        }
                                        .onFailure { toast("couldn't remove feed") }
                                }
                            },
                        )
                    }
                }
                Row(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.padding(top = 8.dp),
                ) {
                    OutlinedTextField(
                        value = newUrl,
                        onValueChange = { newUrl = it },
                        placeholder = { Text("add RSS feed URL…", color = Domovoi.colors.fgSubtle) },
                        singleLine = true,
                        modifier = Modifier.weight(1f),
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                        keyboardActions = KeyboardActions(onDone = { addFeed() }),
                    )
                    OutlinedButton(onClick = { addFeed() }, enabled = !busy) { Text("add") }
                }
            }
        }
    }
}

@Composable
private fun FeedRow(f: NewsFeedRow, onRevalidate: () -> Unit, onRemove: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        StatusDot(if (f.valid) Tone.Ok else Tone.Idle, live = f.valid)
        Column(Modifier.weight(1f)) {
            Text(
                f.title ?: f.source ?: f.url ?: "feed",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fg,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            val meta = buildString {
                append(f.discovered_via ?: "—")
                f.scope?.let { append(" · $it") }
                f.url?.let { append(" · $it") }
            }
            Text(
                meta,
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                color = Domovoi.colors.fgMuted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        IconButton(onClick = onRevalidate, modifier = Modifier.size(28.dp)) {
            Icon(Icons.Outlined.Refresh, "re-check validity", tint = Domovoi.colors.fgMuted)
        }
        IconButton(onClick = onRemove, modifier = Modifier.size(28.dp)) {
            Icon(Icons.Outlined.Close, "remove feed", tint = Domovoi.colors.fgMuted)
        }
    }
}

// ---------------------------------------------------------------------------
// Saved feed (news_items) — newest first, favorited pinned.
// ---------------------------------------------------------------------------

@Composable
private fun SavedFeedCard(items: List<NewsItemRow>, onChanged: () -> Unit) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current

    DomovoiCard(modifier = Modifier.fillMaxWidth()) {
        SectionLabel("saved feed")
        Text(
            "Newest first. Favorited stories are pinned and never auto-deleted.",
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgSubtle,
        )
        Spacer(Modifier.height(4.dp))

        if (items.isEmpty()) {
            EmptyState(
                "no stories yet",
                "add topics, then poll now or wait for the morning fetch",
            )
        } else {
            items.forEach { story ->
                Row(
                    Modifier.fillMaxWidth().padding(vertical = 10.dp),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    IconButton(
                        onClick = {
                            scope.launch {
                                runCatching {
                                    app.api.post(
                                        "/api/news/items/${story.id}/favorite",
                                        buildJsonObject { put("favorited", !story.favorited) },
                                    )
                                }
                                    .onSuccess { onChanged() }
                                    .onFailure { toast("couldn't update favorite") }
                            }
                        },
                        modifier = Modifier.size(28.dp),
                    ) {
                        Icon(
                            if (story.favorited) Icons.Filled.Star else Icons.Outlined.StarBorder,
                            contentDescription = if (story.favorited) "unfavorite" else "favorite",
                            tint = if (story.favorited) Domovoi.colors.brand else Domovoi.colors.fgFaint,
                            modifier = Modifier.size(18.dp),
                        )
                    }
                    Column(Modifier.weight(1f)) {
                        Text(
                            story.title ?: "(untitled)",
                            style = MaterialTheme.typography.titleSmall,
                            color = Domovoi.colors.fg,
                            modifier = if (story.url != null) {
                                Modifier.clickable {
                                    runCatching {
                                        ctx.startActivity(
                                            Intent(Intent.ACTION_VIEW, Uri.parse(story.url)),
                                        )
                                    }
                                }
                            } else {
                                Modifier
                            },
                        )
                        story.summary?.let { s ->
                            Text(
                                s.replace(Regex("<[^>]+>"), ""),
                                style = MaterialTheme.typography.bodySmall,
                                color = Domovoi.colors.fgMuted,
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.padding(top = 2.dp),
                            )
                        }
                        val meta = buildString {
                            story.topic?.let { t -> append("$t · ") }
                            append(story.source ?: "—")
                            append(" · ${relTime(story.published_at ?: story.fetched_at)}")
                            append(if (story.read_at != null) " · read" else " · unread")
                        }
                        Text(
                            meta,
                            style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                            color = Domovoi.colors.fgFaint,
                            modifier = Modifier.padding(top = 4.dp),
                        )
                    }
                }
                HorizontalDivider(color = Domovoi.colors.borderSoft)
            }
        }
    }
}
