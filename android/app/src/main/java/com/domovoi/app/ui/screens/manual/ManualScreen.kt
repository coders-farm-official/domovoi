package com.domovoi.app.ui.screens.manual

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.KeyboardArrowDown
import androidx.compose.material.icons.outlined.KeyboardArrowUp
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily

private enum class ManualTab(val label: String) {
    Features("features"),
    Tech("tech stack"),
    Trouble("troubleshooting"),
    Faq("faq"),
    Howto("how to"),
}

/**
 * Static "how Domovoi works" manual — core features only; plugin features
 * are documented live in the web dashboard's manual (see ManualContent.kt).
 * The web's interactive SVG topology becomes a plain card list of system
 * nodes; the five manual tabs keep their copy.
 */
@Composable
fun ManualScreen() {
    var tab by remember { mutableStateOf(ManualTab.Features) }

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            PageHeader("How Domovoi works", "the whole system, at a glance")
        }
        item {
            DomovoiCard(modifier = Modifier.fillMaxWidth()) {
                Text(
                    "Domovoi is your household guardian: a Pi in each room hears you, the Domovoi " +
                        "server does the thinking, and the answer plays back through that room's " +
                        "speakers. Everything runs on your own hardware — local-first, no cloud " +
                        "account — and network features degrade gracefully instead of breaking.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fgMuted,
                )
            }
        }

        item { SectionLabel("the pieces", Modifier.padding(top = 6.dp)) }
        items(MANUAL_NODES) { node -> NodeCard(node) }

        item {
            Row(
                Modifier.fillMaxWidth()
                    .padding(top = 10.dp)
                    .horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                ManualTab.entries.forEach { t ->
                    TabPill(t.label, t == tab) { tab = t }
                }
            }
        }

        when (tab) {
            ManualTab.Features -> item { FeaturesCard() }
            ManualTab.Tech -> item { TechCard() }
            ManualTab.Trouble -> item { TroubleCard() }
            ManualTab.Faq -> item { FaqCard() }
            ManualTab.Howto -> items(MANUAL_HOWTO) { HowtoCard(it) }
        }
    }
}

@Composable
private fun NodeCard(node: ManualNode) {
    DomovoiCard(modifier = Modifier.fillMaxWidth(), padding = 12) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                node.glyph,
                style = MaterialTheme.typography.titleSmall.copy(fontFamily = MonoFamily),
                color = Domovoi.colors.brand,
            )
            Text(
                node.name,
                style = MaterialTheme.typography.titleSmall.copy(
                    fontFamily = MonoFamily,
                    fontWeight = FontWeight.SemiBold,
                ),
                color = Domovoi.colors.fg,
                modifier = Modifier.weight(1f),
            )
            if (node.live) StatusDot(Tone.Ok, live = true)
            if (node.idle) StatusDot(Tone.Idle)
        }
        Text(
            node.role,
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgMuted,
            modifier = Modifier.padding(top = 4.dp),
        )
        Text(
            node.tech,
            style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
            color = Domovoi.colors.fgFaint,
            modifier = Modifier.padding(top = 4.dp),
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TabPill(label: String, active: Boolean, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        shape = RoundedCornerShape(999.dp),
        color = if (active) Domovoi.colors.brandSoft else Domovoi.colors.card,
        border = BorderStroke(1.dp, if (active) Domovoi.colors.brand else Domovoi.colors.border),
    ) {
        Text(
            label,
            style = MaterialTheme.typography.labelMedium,
            color = Domovoi.colors.fg,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 5.dp),
        )
    }
}

@Composable
private fun KvRow(k: String, kColor: androidx.compose.ui.graphics.Color, content: @Composable () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
        Text(
            k,
            style = MaterialTheme.typography.labelMedium.copy(
                fontFamily = MonoFamily,
                fontWeight = FontWeight.SemiBold,
            ),
            color = kColor,
        )
        Spacer(Modifier.height(2.dp))
        content()
    }
}

@Composable
private fun FeaturesCard() {
    DomovoiCard(modifier = Modifier.fillMaxWidth()) {
        SectionLabel("what domovoi can do")
        Text(
            "Every capability is a handler; most work with no internet at all. " +
                "Installed plugins add more — see the web dashboard's manual.",
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgSubtle,
        )
        Spacer(Modifier.height(6.dp))
        MANUAL_FEATURES.forEachIndexed { i, f ->
            if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
            KvRow(f.name, Domovoi.colors.fg) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Text(
                        f.desc,
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                    f.net?.let {
                        Pill(it, if (it == "offline") Tone.Ok else Tone.Warn)
                    }
                }
            }
        }
    }
}

@Composable
private fun TechCard() {
    DomovoiCard(modifier = Modifier.fillMaxWidth()) {
        SectionLabel("what it's built on")
        Text(
            "Runs entirely on your own hardware — one GPU-equipped server plus Pi satellites.",
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgSubtle,
        )
        Spacer(Modifier.height(6.dp))
        MANUAL_TECH.forEachIndexed { i, t ->
            if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
            KvRow(t.name, Domovoi.colors.fg) {
                Text(
                    t.desc,
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
        }
    }
}

@Composable
private fun TroubleCard() {
    DomovoiCard(modifier = Modifier.fillMaxWidth()) {
        SectionLabel("when something's off")
        Text(
            "The fixes that actually come up on this system, most-common first.",
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgSubtle,
        )
        Spacer(Modifier.height(6.dp))
        MANUAL_TROUBLE.forEachIndexed { i, t ->
            if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
            KvRow(t.symptom, Domovoi.colors.fg) {
                Text(
                    t.fix,
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
        }
    }
}

@Composable
private fun FaqCard() {
    var open by remember { mutableStateOf(setOf<Int>()) }
    DomovoiCard(modifier = Modifier.fillMaxWidth()) {
        SectionLabel("frequently asked")
        Spacer(Modifier.height(6.dp))
        MANUAL_FAQ.forEachIndexed { i, f ->
            if (i > 0) HorizontalDivider(color = Domovoi.colors.borderSoft)
            val isOpen = i in open
            Row(
                Modifier.fillMaxWidth()
                    .clickable { open = if (isOpen) open - i else open + i }
                    .padding(vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    f.q,
                    style = MaterialTheme.typography.titleSmall,
                    color = Domovoi.colors.fg,
                    modifier = Modifier.weight(1f),
                )
                Icon(
                    if (isOpen) Icons.Outlined.KeyboardArrowUp else Icons.Outlined.KeyboardArrowDown,
                    contentDescription = if (isOpen) "collapse" else "expand",
                    tint = Domovoi.colors.fgMuted,
                )
            }
            if (isOpen) {
                Text(
                    f.a,
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                    modifier = Modifier.padding(bottom = 10.dp),
                )
            }
        }
    }
}

@Composable
private fun HowtoCard(row: HowtoRow) {
    var open by remember { mutableStateOf(false) }
    DomovoiCard(modifier = Modifier.fillMaxWidth(), padding = 12) {
        Row(
            Modifier.fillMaxWidth().clickable { open = !open },
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                row.title,
                style = MaterialTheme.typography.titleSmall,
                color = Domovoi.colors.fg,
                modifier = Modifier.weight(1f),
            )
            Icon(
                if (open) Icons.Outlined.KeyboardArrowUp else Icons.Outlined.KeyboardArrowDown,
                contentDescription = if (open) "collapse" else "expand",
                tint = Domovoi.colors.fgMuted,
            )
        }
        if (open) {
            if (row.act.isNotEmpty()) {
                Text(
                    "you can",
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                    color = Domovoi.colors.fgSubtle,
                    modifier = Modifier.padding(top = 10.dp),
                )
                row.act.forEach { Bullet(it) }
            }
            Text(
                "diagnose",
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                color = Domovoi.colors.warn,
                modifier = Modifier.padding(top = 10.dp),
            )
            row.diag.forEach { Bullet(it) }
        }
    }
}

@Composable
private fun Bullet(text: String) {
    Row(Modifier.padding(top = 4.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Text("•", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgFaint)
        Text(text, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
    }
}
