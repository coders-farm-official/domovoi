package com.domovoi.app.ui.screens.images

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.Cancel
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.SubcomposeAsyncImage
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

// ---------------------------------------------------------------------------
// Models — /api/plugins/imagegen rows (the Image Generation plugin's web
// surface; this screen is capability-gated on "imagegen"). Browsing images
// is the Files tab's job — this screen is generate + history only. Engine
// install/start/stop stays on the web dashboard.
// ---------------------------------------------------------------------------

private const val IG_API = "/api/plugins/imagegen"

@Serializable
private data class GenModel(
    val id: String = "",
    val label: String = "",
    val installed: Boolean = false,
    val recommended: Boolean = false,
)

@Serializable
private data class GenState(
    val installed: Boolean = false,
    val engine_online: Boolean = false,
    val models: List<GenModel> = emptyList(),
)

@Serializable
private data class JobResult(val images: List<String> = emptyList())

@Serializable
private data class ImageJob(
    val id: Long = 0,
    val kind: String = "",
    val model: String = "",
    val prompt: String? = null,
    val params: JobParams? = null,
    val status: String = "",
    val pct: Int? = null,
    val status_text: String? = null,
    val error: String? = null,
    val result: JobResult? = null,
    val completed_at: String? = null,
)

@Serializable
private data class JobParams(
    val width: Int? = null,
    val height: Int? = null,
    val steps: Int? = null,
    val seed: Long? = null,
)

@Serializable
private data class JobList(val jobs: List<ImageJob> = emptyList())

@Serializable
private data class HistoryList(val history: List<ImageJob> = emptyList())

private fun filePath(name: String): String = "$IG_API/file/${Uri.encode(name)}"

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

@Composable
fun ImagesScreen() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    val state = rememberApi("imagegen-models", eventTypes = setOf("imagegen.jobs.changed")) {
        it.api.get("$IG_API/models").decode<GenState>()
    }
    val jobs = rememberApi("imagegen-jobs", eventTypes = setOf("imagegen.jobs.changed")) {
        it.api.get("$IG_API/jobs").decode<JobList>().jobs
    }
    val history = rememberApi("imagegen-history", eventTypes = setOf("imagegen.jobs.changed")) {
        it.api.get("$IG_API/history").decode<HistoryList>().history
    }

    val installedModels = state.data?.models.orEmpty().filter { it.installed }
    var prompt by remember { mutableStateOf("") }
    var modelId by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf<ImageJob?>(null) }
    val active = installedModels.firstOrNull { it.id == modelId } ?: installedModels.firstOrNull()
    val ready = state.data?.installed == true && state.data?.engine_online == true

    fun generate() {
        val m = active ?: return
        if (prompt.isBlank() || busy) return
        busy = true
        scope.launch {
            runCatching {
                app.api.post("$IG_API/generate", buildJsonObject {
                    put("model", m.id)
                    put("prompt", prompt.trim())
                })
            }.onSuccess { jobs.refresh() }
                .onFailure { toast(it.message ?: "generation failed to start") }
            busy = false
        }
    }

    fun deleteHistory(job: ImageJob) {
        scope.launch {
            runCatching { app.api.delete("$IG_API/history/${job.id}") }
                .onSuccess { toast("deleted"); history.refresh() }
                .onFailure { toast("delete failed: ${it.message}") }
        }
    }

    val laneJobs = jobs.data.orEmpty().filter {
        it.status == "pending" || it.status == "running" || it.status == "failed"
    }

    LazyColumn(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item {
            PageHeader("Images", "generate locally on your own GPU · browse in Files")
        }

        item {
            when {
                state.data == null && state.loading -> LoadingState()
                state.data == null -> EmptyState(
                    "image generation unavailable",
                    state.error ?: "the Image Generation plugin didn't answer",
                )
                !state.data!!.installed -> EngineNote(
                    "engine not installed",
                    "set it up from the web dashboard's Images page (one click)",
                )
                !state.data!!.engine_online -> EngineNote(
                    "engine offline",
                    "start it from the web dashboard's Images page",
                )
                else -> Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    StatusDot(Tone.Ok, live = true)
                    Text(
                        "engine running",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                    )
                }
            }
        }

        if (state.data?.installed == true) {
            item {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = prompt, onValueChange = { prompt = it },
                        placeholder = { Text("describe the image…") },
                        minLines = 3,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        ModelPicker(installedModels, active?.id, Modifier.weight(1f)) { modelId = it }
                        Button(
                            onClick = { generate() },
                            enabled = ready && active != null && prompt.isNotBlank() && !busy,
                        ) {
                            Icon(
                                Icons.Outlined.AutoAwesome, contentDescription = null,
                                modifier = Modifier.size(16.dp),
                            )
                            Spacer(Modifier.size(6.dp))
                            Text("generate")
                        }
                    }
                }
            }
        }

        items(laneJobs, key = { "job-${it.id}" }) { job -> JobCard(job) }

        if (history.data.orEmpty().isNotEmpty()) {
            item {
                Text(
                    "history",
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                    color = Domovoi.colors.fgMuted,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }
            items(history.data.orEmpty(), key = { "hist-${it.id}" }) { h ->
                HistoryCard(
                    h,
                    onOpen = { name ->
                        context.startActivity(
                            Intent(Intent.ACTION_VIEW, Uri.parse(app.api.absolute(filePath(name)))),
                        )
                    },
                    onDelete = { confirmDelete = h },
                )
            }
        }
    }

    confirmDelete?.let { job ->
        val n = job.result?.images?.size ?: 0
        ConfirmDialog(
            title = "delete generation",
            body = "Delete this generation and its $n image${if (n == 1) "" else "s"}? This can't be undone.",
            confirmLabel = "delete",
            destructive = true,
            onConfirm = { deleteHistory(job); confirmDelete = null },
            onDismiss = { confirmDelete = null },
        )
    }
}

@Composable
private fun EngineNote(title: String, sub: String) {
    DomovoiCard(Modifier.fillMaxWidth()) {
        Column {
            Text(title, style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg)
            Text(sub, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
        }
    }
}

@androidx.annotation.OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
private fun ModelPicker(
    installed: List<GenModel>,
    activeId: String?,
    modifier: Modifier = Modifier,
    onPick: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val label = installed.firstOrNull { it.id == activeId }?.label
        ?: if (installed.isEmpty()) "no models installed" else "pick a model"
    ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { expanded = it }, modifier = modifier) {
        OutlinedTextField(
            value = label, onValueChange = {}, readOnly = true, singleLine = true,
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            modifier = Modifier.menuAnchor().fillMaxWidth(),
        )
        ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            installed.forEach { m ->
                DropdownMenuItem(
                    text = { Text(m.label + if (m.recommended) "  ·  recommended" else "") },
                    onClick = { onPick(m.id); expanded = false },
                )
            }
        }
    }
}

@Composable
private fun JobCard(job: ImageJob) {
    val app = LocalApp.current
    val activeJob = job.status == "pending" || job.status == "running"
    DomovoiCard(Modifier.fillMaxWidth()) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Pill(
                    job.status,
                    when (job.status) {
                        "failed" -> Tone.Err
                        else -> if (activeJob) Tone.Brand else Tone.Idle
                    },
                )
                Text(
                    job.prompt ?: job.model,
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fg,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                if (activeJob) {
                    IconButton(onClick = {
                        app.scope.launch {
                            runCatching { app.api.post("$IG_API/jobs/${job.id}/cancel") }
                        }
                    }) {
                        Icon(Icons.Outlined.Cancel, contentDescription = "cancel", tint = Domovoi.colors.fgMuted)
                    }
                }
            }
            if (activeJob) {
                Spacer(Modifier.height(6.dp))
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                    Text(
                        (job.status_text ?: "working…") +
                            (job.pct?.let { " · $it%" } ?: ""),
                        style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                        color = Domovoi.colors.fgFaint,
                    )
                }
            }
            job.error?.let {
                Spacer(Modifier.height(6.dp))
                Text(
                    it, style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                    color = Domovoi.colors.err,
                )
            }
        }
    }
}

@Composable
private fun HistoryCard(h: ImageJob, onOpen: (String) -> Unit, onDelete: () -> Unit) {
    val app = LocalApp.current
    val names = h.result?.images.orEmpty()
    val p = h.params
    val meta = listOfNotNull(
        h.model,
        p?.let { if (it.width != null && it.height != null) "${it.width} × ${it.height}" else null },
        p?.steps?.let { "$it steps" },
        p?.seed?.let { "seed $it" },
    ).joinToString(" · ")

    DomovoiCard(Modifier.fillMaxWidth()) {
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            LazyRow(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                modifier = Modifier.weight(1f, fill = false),
            ) {
                items(names.take(3), key = { it }) { name ->
                    Box(
                        Modifier.size(84.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(8.dp))
                            .clickable { onOpen(name) },
                    ) {
                        SubcomposeAsyncImage(
                            model = app.api.absolute(filePath(name)),
                            contentDescription = name,
                            contentScale = ContentScale.Crop,
                            modifier = Modifier.fillMaxSize(),
                            loading = { TileFallback() },
                            error = { TileFallback() },
                        )
                    }
                }
            }
            Column(Modifier.weight(1.4f)) {
                Text(
                    h.prompt ?: "",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fg,
                    maxLines = 3, overflow = TextOverflow.Ellipsis,
                )
                Text(
                    meta,
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                    color = Domovoi.colors.fgFaint,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
            IconButton(onClick = onDelete) {
                Icon(Icons.Outlined.DeleteOutline, contentDescription = "delete", tint = Domovoi.colors.fgMuted)
            }
        }
    }
}

@Composable
private fun TileFallback() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Icon(
            Icons.Outlined.Image, contentDescription = null,
            tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(22.dp),
        )
    }
}
