package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.domovoi.app.assistant.ModelCatalog
import com.domovoi.app.ui.theme.Domovoi

/** One open-source part of the app and its licence. [notices]: asset
 *  paths of the licence texts the app carries for it (shown on tap). */
internal data class OpenSourcePart(
    val name: String,
    val licence: String,
    val source: String,
    val note: String? = null,
    val notices: List<String> = emptyList(),
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
        "Piper voices Norman, Kristin and LJ Speech", "public domain", "brycebeattie.com/files/tts",
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
    OpenSourcePart("LiteRT-LM 0.18.0", "Apache-2.0", "github.com/google-ai-edge/LiteRT-LM", "Runs the on-device language model."),
) + ModelCatalog.assistant.map { m ->
    OpenSourcePart(m.name, m.license, m.licenseUrl.removePrefix("https://"), "Downloaded by the on-device assistant's benchmark.")
}

/** Settings → About: the open-source licences, a notice text on tap. */
@Composable
internal fun LicencesCard() {
    val context = LocalContext.current
    var open by remember { mutableStateOf<String?>(null) }
    PanelCard("Open-source licences", "What this app is built from and what it downloads, with each licence.") {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            OPEN_SOURCE_PARTS.forEach { part ->
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(part.name, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                    Text("${part.licence} · ${part.source}", style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted)
                    part.note?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted) }
                    part.notices.forEach { path ->
                        Text(
                            (if (open == path) "hide " else "show ") + path.substringAfter('/'),
                            style = MaterialTheme.typography.labelMedium,
                            color = Domovoi.colors.brand,
                            modifier = Modifier.clickable { open = if (open == path) null else path }.padding(vertical = 2.dp),
                        )
                        if (open == path) {
                            val text = remember(path) {
                                runCatching { context.assets.open(path).use { it.readBytes().toString(Charsets.UTF_8) } }
                                    .getOrElse { "(not in this build: ${it.message})" }
                            }
                            Text(
                                text,
                                style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                                color = Domovoi.colors.fgMuted,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .heightIn(max = 320.dp)
                                    .background(Domovoi.colors.sunken, RoundedCornerShape(8.dp))
                                    .verticalScroll(rememberScrollState())
                                    .padding(10.dp),
                            )
                        }
                    }
                }
            }
        }
    }
}
