package com.domovoi.app.ui.screens.images

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
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
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
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.serialization.Serializable

// ---------------------------------------------------------------------------
// Models — /api/plugins/imagegen rows (the Image Generation plugin's web
// surface; this screen is capability-gated on "imagegen"). Browsing images
// is the Files tab's job — this screen is the engine's state, the lane and
// the history. Engine install/start/stop stays on the web dashboard.
//
// It is a READ-ONLY screen from a phone (2026-11-03, the owner's decision 67:
// "image generation is dashboard only"). What is not here, and why there is
// no control for it:
//
//   * generate      POST   $IG_API/generate
//   * cancel a job  POST   $IG_API/jobs/{id}/cancel
//   * delete a past DELETE $IG_API/history/{id}
//
// All three are `dependencies=ADMIN_ONLY` in the plugin
// (domovoi-plugin-imagegen/domovoi_plugin_imagegen/web.py:149, applied at
// :360 generate, :339 cancel, :651 delete), which is Depends(admin_required)
// — a Bearer admin session or the dashboard's cookie, and never
// X-Device-Token. This app sends only the device token, so all three
// answered 401 "admin session required" from every phone, every time: the
// prompt field and `generate` were drawn and refused, and the bin opened a
// destructive confirmation that could only ever fail at the end of it.
// The owner's no-misleading-controls rule says a control that cannot do what it
// says is not drawn, so they are not. The iPhone drew them for one round and
// removed them the same way (ios/Domovoi/Images/ImagesView.swift's header
// carries the long form of this argument).
//
// The four reads — /models, /jobs, /history, /file/{name} — are open, and
// they are the whole screen. Giving the plugin's three routes the household
// tier (`enforce_route_tier`, domovoi/webkit.py:333-360) is the one change
// that would bring the controls back; it is in a repository this branch does
// not edit. The day it lands, this screen draws them again.
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
    // Still decoded, because the shape is the server's, and no longer drawn:
    // the model picker went with `generate`. The two fields this screen does
    // read are `installed` and `engine_online`, which is what the engine note
    // is made of.
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

    val state = rememberApi("imagegen-models", eventTypes = setOf("imagegen.jobs.changed")) {
        it.api.get("$IG_API/models").decode<GenState>()
    }
    val jobs = rememberApi("imagegen-jobs", eventTypes = setOf("imagegen.jobs.changed")) {
        it.api.get("$IG_API/jobs").decode<JobList>().jobs
    }
    val history = rememberApi("imagegen-history", eventTypes = setOf("imagegen.jobs.changed")) {
        it.api.get("$IG_API/history").decode<HistoryList>().history
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

        // The prompt field, the model picker and `generate` used to be here,
        // behind `state.data?.installed == true`. They are gone with the
        // route behind them — this file's header says why.

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
                    // Fetched by the app (it carries the device token) and
                    // handed on as a file: a bare server address opened in
                    // the browser would be refused without the token.
                    onOpen = { name ->
                        com.domovoi.app.net.ServerFileTransfer.open(
                            context, app, filePath(name), name,
                            com.domovoi.app.ui.screens.files.mimeFor(name), toast,
                        )
                    },
                )
            }
        }
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

/**
 * One generation that is happening now, or one that failed: a report, not a
 * control. The cancel that used to sit at the end of its top row is gone —
 * `POST …/jobs/{id}/cancel` is admin-only (this file's header), so from a
 * phone it answered 401 every time.
 *
 * With nothing to tap, this card takes no callback and needs no
 * `LocalApp` — a card that held a coroutine of its own in a LazyColumn row
 * was also a write that could be torn down mid-flight as the row scrolled
 * out.
 */
@Composable
private fun JobCard(job: ImageJob) {
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

/**
 * One past generation: its first three pictures, the prompt, and the
 * settings it was made with. A picture opens.
 *
 * The bin at the end of the row is gone, and so is the confirmation it
 * opened — `DELETE …/history/{id}` is admin-only (this file's header), so
 * the dialog could only ever end in a 401. The pictures keep the whole
 * width the bin used to take.
 */
@Composable
private fun HistoryCard(h: ImageJob, onOpen: (String) -> Unit) {
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
