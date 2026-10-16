package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.domovoi.app.assistant.AssistantSupport
import com.domovoi.app.assistant.ModelCatalog
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** One open-source part of the app and its licence. [notices]: asset
 *  paths of the licence texts the app carries for it (shown on tap).
 *  [assistantOnly]: downloaded by the on-device assistant, so not listed
 *  where the assistant is not in this install. */
internal data class OpenSourcePart(
    val name: String,
    val licence: String,
    val source: String,
    val note: String? = null,
    val notices: List<String> = emptyList(),
    val assistantOnly: Boolean = false,
)

private const val SHERPA = "licences/sherpa-onnx"

/**
 * Everything open source in the app or downloaded by it, with its licence.
 * The notice texts come from their sources at build time (app/build.gradle.kts
 * voiceAssets: CMUdict's from domovoi/phonemizer/data, sherpa-onnx's and its
 * dependencies' from android/native/sherpa-onnx).
 */
internal val OPEN_SOURCE_PARTS: List<OpenSourcePart> = listOf(
    OpenSourcePart(
        "Domovoi's text-to-phoneme converter", "MIT", "github.com/coders-farm-official/domovoi",
        "How the voice reads text; the same converter as the server's.",
    ),
    OpenSourcePart(
        "CMUdict, the Carnegie Mellon Pronouncing Dictionary", "BSD-2-Clause style", "github.com/cmusphinx/cmudict",
        "Copyright (C) 1993-2015 Carnegie Mellon University. The converter's dictionary.",
        listOf("phonemizer/cmudict/LICENSE"),
    ),
    OpenSourcePart(
        "Piper voices Norman, Kristin and Linda (LJ Speech)", "public domain", "brycebeattie.com/files/tts",
        "By Bryce Beattie, trained from scratch on public-domain recordings (LibriVox; the LJ Speech " +
            "dataset) and released into the public domain; distributed through rhasspy/piper-voices (MIT). " +
            "Downloaded, one at a time, from Settings → Voice.",
    ),
    OpenSourcePart("ONNX Runtime 1.28.0", "MIT", "github.com/microsoft/onnxruntime", "Copyright (c) Microsoft Corporation."),
    OpenSourcePart(
        "sherpa-onnx 1.13.8, built without text to speech", "Apache-2.0", "github.com/k2-fsa/sherpa-onnx",
        "Speech recognition and voice detection. Compiled in: Eigen (MPL-2.0, unmodified), nlohmann/json " +
            "(MIT), kaldi-decoder, kaldi-native-fbank, kaldifst, OpenFst, simple-sentencepiece (Apache-2.0), " +
            "KISS FFT (BSD-3-Clause).",
        listOf(
            "$SHERPA/LICENSE", "$SHERPA/eigen/COPYING.README", "$SHERPA/eigen/COPYING.MPL2", "$SHERPA/eigen/COPYING.BSD",
            "$SHERPA/eigen/COPYING.MINPACK", "$SHERPA/json/LICENSE.MIT", "$SHERPA/kaldi_decoder/LICENSE",
            "$SHERPA/kaldi_native_fbank/LICENSE", "$SHERPA/kaldifst/LICENSE", "$SHERPA/openfst/COPYING",
            "$SHERPA/simple-sentencepiece/LICENSE", "$SHERPA/kissfft/COPYING",
        ),
    ),
    OpenSourcePart(LITERT_LM, "Apache-2.0", "github.com/google-ai-edge/LiteRT-LM", "Runs the on-device language model."),
    OpenSourcePart(
        "CameraX and ZXing", "Apache-2.0", "android.googlesource.com/platform/frameworks/support · github.com/zxing/zxing",
        "Room mode's QR scanner: the camera's picture and the code's decoder. CameraX builds in libyuv (BSD-3-Clause).",
        listOf(OpenSourceLicences.LIBYUV_LICENCE),
    ),
) + ModelCatalog.assistant.map { m ->
    OpenSourcePart(
        m.name, m.license, m.licenseUrl.removePrefix("https://"),
        if (m in ModelCatalog.chat) "Downloaded by the on-device assistant (the assistant tab)."
        else "Downloaded by the on-device assistant's benchmark.",
        assistantOnly = true,
    )
}

private const val LITERT_LM = "LiteRT-LM 0.18.0"

/** The card's list for this install. Without the on-device assistant (a
 *  32-bit phone or APK, [AssistantSupport]) its downloads are left out, as
 *  nothing downloads them; LiteRT-LM stays, as its Java part is in every
 *  APK, and says it is not used here. */
internal fun openSourceParts(assistant: Boolean): List<OpenSourcePart> =
    if (assistant) {
        OPEN_SOURCE_PARTS
    } else {
        OPEN_SOURCE_PARTS.filterNot { it.assistantOnly }.map { part ->
            if (part.name != LITERT_LM) part else part.copy(
                note = "Runs the on-device language model, on 64-bit phones only: not used on this one.",
            )
        }
    }

/**
 * Settings → About: the open-source licences. The main parts first (what
 * the app is built from and what it downloads, each with its licence and
 * notices), then every library in this build, from its dependency graph
 * ([OpenSourceLicences], `licences/maven.json`), grouped by who makes it,
 * then the licence texts. A notice text shows on tap.
 */
@Composable
internal fun LicencesCard() {
    val context = LocalContext.current
    var open by remember { mutableStateOf<String?>(null) }
    var showLibraries by remember { mutableStateOf(false) }
    val libraries = remember { OpenSourceLicences.read(context) }
    PanelCard("Open-source licences", "What this app is built from and what it downloads, with each licence.") {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            openSourceParts(AssistantSupport.available).forEach { part ->
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(part.name, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                    Text("${part.licence} · ${part.source}", style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted)
                    part.note?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted) }
                    part.notices.forEach { path -> NoticeToggle(path, open) { open = it } }
                }
            }
            SectionLabel("every library in this build")
            Text(OpenSourceLicences.summary(libraries), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            if (libraries.isNotEmpty()) {
                TextButton(onClick = { showLibraries = !showLibraries }) {
                    Text(if (showLibraries) "hide the libraries" else "show the libraries", color = Domovoi.colors.brand)
                }
            }
            if (showLibraries) LibraryGroups(OpenSourceLicences.grouped(libraries), open) { open = it }
        }
    }
}

/** The libraries under each rule: who, the licence, the source, each
 *  library with its version, and the texts. */
@Composable
internal fun LibraryGroups(groups: List<Pair<LicenceRule, List<MavenLibrary>>>, open: String?, onOpen: (String?) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        groups.forEach { (rule, libs) ->
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(rule.title, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                Text("${rule.licence} · ${rule.source}", style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
                rule.note?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted) }
                Text(
                    libs.joinToString(", ") { "${it.name} ${it.version}" },
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgSubtle,
                )
                (rule.texts + libs.flatMap { it.notices }).distinct().forEach { path -> NoticeToggle(path, open, onOpen) }
            }
        }
    }
}

/** "show LICENSE": a notice's text, opened on tap. */
@Composable
private fun NoticeToggle(path: String, open: String?, onOpen: (String?) -> Unit) {
    val context = LocalContext.current
    // 48 dp tall to touch (the accessibility minimum), however small it reads.
    Box(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp)
            .clickable(role = Role.Button) { onOpen(if (open == path) null else path) },
        contentAlignment = Alignment.CenterStart,
    ) {
        Text(
            (if (open == path) "hide " else "show ") + noticeLabel(path),
            style = MaterialTheme.typography.labelMedium,
            color = Domovoi.colors.brand,
        )
    }
    if (open == path) {
        // Read and cut up off the main thread: some run to megabytes.
        val chunks by produceState<List<String>?>(null, path) {
            value = withContext(Dispatchers.IO) {
                noticeChunks(
                    runCatching { context.assets.open(path).use { it.readBytes().toString(Charsets.UTF_8) } }
                        .getOrElse { "(not in this build: ${it.message})" },
                )
            }
        }
        chunks?.let { NoticeText(it) } ?: Text("loading…", style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
    }
}

/** A notice's text in a box of its own height: lazily, in chunks, as some
 *  run to megabytes (LiteRT-LM's third-party notice is 2 MB). */
@Composable
private fun NoticeText(chunks: List<String>) {
    LazyColumn(
        Modifier
            .fillMaxWidth()
            .height(if (chunks.size > 1) 360.dp else 240.dp)
            .background(Domovoi.colors.sunken, RoundedCornerShape(8.dp))
            .padding(10.dp),
    ) {
        items(chunks) { chunk ->
            Text(
                chunk,
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                color = Domovoi.colors.fgMuted,
            )
        }
    }
}

/** A notice's name on its toggle: "Apache-2.0.txt", "onnxruntime/LICENSE",
 *  "sherpa-onnx/eigen/COPYING.MPL2", "cmudict/LICENSE", "core: LICENSE.txt"
 *  (a library's own, with the path inside its jar flattened). */
internal fun noticeLabel(path: String): String = when {
    path.startsWith("licences/texts/") -> path.removePrefix("licences/texts/")
    path.startsWith("licences/maven/") -> path.removePrefix("licences/maven/").split('/').let { p ->
        val file = p.last()
        "${p.getOrElse(1) { "" }}: " + if (file.startsWith("META-INF_")) file.substringAfterLast('_') else file
    }
    else -> path.substringAfter('/')
}

/** A text cut into pieces of [lines] lines each. */
internal fun noticeChunks(text: String, lines: Int = 40): List<String> =
    text.lineSequence().chunked(lines).map { it.joinToString("\n") }.toList().ifEmpty { listOf("") }
