package com.domovoi.app.ui.screens.news

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.window.core.layout.WindowWidthSizeClass
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.AvatarBubble
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily

/**
 * News page — per-person topics, feeds, saved items and briefing
 * (web/static/news.jsx). Left people rail on expanded width; a horizontal
 * people chip row on compact.
 */
@Composable
fun NewsScreen() {
    val compact = currentWindowAdaptiveInfo()
        .windowSizeClass.windowWidthSizeClass == WindowWidthSizeClass.COMPACT

    val people = rememberApi("news-people", eventTypes = setOf("people.last_seen.changed")) {
        it.api.get("/api/people").decode<List<NewsPerson>>()
    }
    val categories = rememberApi("news-categories") {
        it.api.get("/api/news/categories").decode<List<String>>()
    }

    var selectedId by remember { mutableStateOf<Long?>(null) }
    // Default-select the most-recently-seen person once the roster loads.
    LaunchedEffect(people.data) {
        if (selectedId == null) people.data?.firstOrNull()?.let { selectedId = it.id }
    }
    val selectedPerson = people.data?.firstOrNull { it.id == selectedId }

    val detail: @Composable () -> Unit = {
        when {
            people.data == null && people.loading -> LoadingState()
            people.data == null && people.error != null ->
                ErrorState(people.error ?: "request failed", people.refresh)
            people.data.isNullOrEmpty() -> EmptyState(
                "no people enrolled yet",
                "enroll a speaker on the people page first",
            )
            selectedPerson == null -> EmptyState(
                "pick a person",
                "select someone to manage their news",
            )
            else -> key(selectedPerson.id) {
                PersonNewsDetail(selectedPerson, categories.data ?: emptyList())
            }
        }
    }

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        PageHeader("News", "topics of interest, discovered feeds, and saved stories")
        Spacer(Modifier.height(12.dp))
        if (compact) {
            PeopleChipRow(people.data ?: emptyList(), selectedId) { selectedId = it }
            Spacer(Modifier.height(12.dp))
            Box(Modifier.weight(1f)) { detail() }
        } else {
            Row(
                Modifier.weight(1f),
                horizontalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                Column(Modifier.width(240.dp).verticalScroll(rememberScrollState())) {
                    PeoplePane(people.data ?: emptyList(), selectedId) { selectedId = it }
                }
                Box(Modifier.weight(1f)) { detail() }
            }
        }
    }
}

/** Compact-width people picker — a horizontal chip row. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PeopleChipRow(
    people: List<NewsPerson>,
    selectedId: Long?,
    onSelect: (Long) -> Unit,
) {
    LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        items(people, key = { it.id }) { p ->
            val on = p.id == selectedId
            Surface(
                onClick = { onSelect(p.id) },
                shape = RoundedCornerShape(999.dp),
                color = if (on) Domovoi.colors.brandSoft else Domovoi.colors.card,
                border = BorderStroke(1.dp, if (on) Domovoi.colors.brand else Domovoi.colors.border),
            ) {
                Row(
                    Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    AvatarBubble(p.name, 20)
                    Text(
                        p.name ?: "?",
                        style = MaterialTheme.typography.labelLarge,
                        color = Domovoi.colors.fg,
                        maxLines = 1,
                    )
                }
            }
        }
    }
}

/** Expanded-width people rail (web left rail: avatar, name, heard-when). */
@Composable
private fun PeoplePane(
    people: List<NewsPerson>,
    selectedId: Long?,
    onSelect: (Long) -> Unit,
) {
    DomovoiCard(modifier = Modifier.fillMaxWidth(), padding = 12) {
        SectionLabel("people")
        Text(
            "whose news?",
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgSubtle,
        )
        Spacer(Modifier.height(8.dp))
        if (people.isEmpty()) {
            Text(
                "No people enrolled yet. Enroll a speaker on the People page first.",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
            )
        }
        people.forEach { p ->
            val on = p.id == selectedId
            Row(
                Modifier.fillMaxWidth()
                    .clip(RoundedCornerShape(8.dp))
                    .background(if (on) Domovoi.colors.sunken else Color.Transparent)
                    .clickable { onSelect(p.id) }
                    .padding(horizontal = 8.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                AvatarBubble(p.name, 32)
                Column(Modifier.weight(1f)) {
                    Text(
                        p.name ?: "?",
                        style = MaterialTheme.typography.titleSmall,
                        color = Domovoi.colors.fg,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        "heard ${relTime(p.last_seen_at)}",
                        style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                        color = Domovoi.colors.fgMuted,
                    )
                }
            }
        }
    }
}
