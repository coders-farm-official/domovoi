package com.domovoi.app.assistant

/** What a model does in the assistant. */
enum class ModelKind(val label: String) {
    LLM("language model"),
    STT("speech to text"),
    VAD("voice detector"),
    TTS("voice"),
}

/** One file of a model: where it comes from, pinned to an exact revision,
 *  and what it must hash to. [path] is relative to the model's directory. */
data class ModelFile(
    val path: String,
    val url: String,
    val size: Long,
    val sha256: String,
)

data class CatalogModel(
    val id: String,
    val name: String,
    val kind: ModelKind,
    val files: List<ModelFile>,
    /** SPDX id(s), or a short phrase when the terms are not an SPDX licence. */
    val license: String,
    /** Where the licence was verified (the upstream LICENSE / model card). */
    val licenseUrl: String,
    /** True for a model the benchmark may use to measure timing but that
     *  nothing may ship with (its licence does not allow it). */
    val timingOnly: Boolean = false,
    val note: String? = null,
) {
    val totalBytes: Long get() = files.sumOf { it.size }
}

/**
 * Every model file the on-device assistant can download, compiled in: no
 * remote catalog, so the app never checks in with anyone. Each URL is pinned
 * to an exact Hugging Face commit (`resolve/<commit>/`), never `main`, and
 * each file's SHA-256 is checked before it is used. Sizes and hashes were
 * taken from the Hugging Face API (LFS files) or the downloaded file
 * (small files) on 2026-10-12.
 *
 * M0 (benchmark) catalog. The voice here is for timing only — see
 * [piperTimingVoice]; the product voice is a separate decision.
 */
object ModelCatalog {
    private const val GEMMA_REV = "b3ca0d2f076785a8f4b2219ddbd2bdb99954eae1"
    private const val GEMMA = "https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm/resolve/$GEMMA_REV"

    /** Gemma 4 E2B, instruction-tuned, in LiteRT-LM's format (the generic
     *  CPU/GPU build, not a vendor NPU one). */
    val gemma4E2b = CatalogModel(
        id = "gemma-4-e2b-it",
        name = "Gemma 4 E2B (instruction-tuned)",
        kind = ModelKind.LLM,
        files = listOf(
            ModelFile(
                "gemma-4-E2B-it.litertlm", "$GEMMA/gemma-4-E2B-it.litertlm",
                2_588_147_712, "181938105e0eefd105961417e8da75903eacda102c4fce9ce90f50b97139a63c",
            ),
        ),
        license = "Apache-2.0",
        licenseUrl = "https://huggingface.co/google/gemma-4-E2B-it",
    )

    private const val TINY_REV = "d1e6c30921780b8508d04b492dfb3ce8a51605d4"
    private const val TINY = "https://huggingface.co/csukuangfj2/sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27/resolve/$TINY_REV"

    /** Moonshine (v2) tiny, English, as packaged for sherpa-onnx. */
    val moonshineTinyEn = CatalogModel(
        id = "moonshine-tiny-en",
        name = "Moonshine tiny (English)",
        kind = ModelKind.STT,
        files = listOf(
            ModelFile("encoder_model.ort", "$TINY/encoder_model.ort", 13_281_600,
                "94e90a4654fc45cdfedb77c4c08e1739f48862998e58fada384b25118134f221"),
            ModelFile("decoder_model_merged.ort", "$TINY/decoder_model_merged.ort", 30_412_256,
                "cf524c4862d36e9e5ab032eddc73637efd822d70e868ac575cf1a46e1e4708a0"),
            ModelFile("tokens.txt", "$TINY/tokens.txt", 549_350,
                "2870d843e14c1e187bf1913a521562a63b53933814bd7f2145120468f494a049"),
        ),
        license = "MIT",
        licenseUrl = "https://github.com/moonshine-ai/moonshine/blob/main/LICENSE",
    )

    private const val BASE_REV = "8f4d6c58c03d40bcea40043bb7120a878f2bbef6"
    private const val BASE = "https://huggingface.co/csukuangfj2/sherpa-onnx-moonshine-base-en-quantized-2026-02-27/resolve/$BASE_REV"

    /** Moonshine (v2) base, English: slower and larger than tiny, more accurate. */
    val moonshineBaseEn = CatalogModel(
        id = "moonshine-base-en",
        name = "Moonshine base (English)",
        kind = ModelKind.STT,
        files = listOf(
            ModelFile("encoder_model.ort", "$BASE/encoder_model.ort", 31_326_816,
                "7c66495948d0d08ec1af454cd4b5514862ae6511e94712a60e6d83eaec8dc8cf"),
            ModelFile("decoder_model_merged.ort", "$BASE/decoder_model_merged.ort", 109_424_400,
                "d9d7b333af34bc552580576ddcf248a1c6c839e0d3b43b09afb9376ed009899d"),
            ModelFile("tokens.txt", "$BASE/tokens.txt", 549_350,
                "2870d843e14c1e187bf1913a521562a63b53933814bd7f2145120468f494a049"),
        ),
        license = "MIT",
        licenseUrl = "https://github.com/moonshine-ai/moonshine/blob/main/LICENSE",
    )

    private const val VAD_REV = "fba88cd2e921609e7675c3aaf51e0b9b295da4bc"

    /** Silero VAD v4 (the file sherpa-onnx's own examples use). */
    val sileroVad = CatalogModel(
        id = "silero-vad-v4",
        name = "Silero VAD v4",
        kind = ModelKind.VAD,
        files = listOf(
            ModelFile("silero_vad.onnx",
                "https://huggingface.co/csukuangfj/vad/resolve/$VAD_REV/silero_vad.onnx", 1_807_522,
                "a35ebf52fd3ce5f1469b2a36158dba761bc47b973ea3382b3186ca15b1f5af28"),
        ),
        license = "MIT",
        licenseUrl = "https://github.com/snakers4/silero-vad/blob/master/LICENSE",
    )

    private const val PIPER_REV = "83f7470750b36549037551ef7007fa3b4b8697e0"
    private const val PIPER = "https://huggingface.co/csukuangfj/vits-piper-en_US-lessac-medium/resolve/$PIPER_REV"

    /**
     * A medium Piper voice, for TIMING ONLY. Its training data (Blizzard
     * 2013, Lessac) is licensed for research, not commercial use, so the app
     * must never ship with it; a separate task picks a permissively licensed
     * voice. The espeak-ng data it phonemizes with is GPL-3.0-or-later.
     * Only the English espeak-ng files are fetched.
     */
    val piperTimingVoice = CatalogModel(
        id = "piper-timing-only-lessac-medium",
        name = "Piper medium voice (timing only)",
        kind = ModelKind.TTS,
        files = listOf(
            ModelFile("en_US-lessac-medium.onnx", "$PIPER/en_US-lessac-medium.onnx", 63_201_425,
                "ea793a648ce0370665f99fbf891eae3b3a973565c72b08534bb5b2c04e8f2332"),
            ModelFile("tokens.txt", "$PIPER/tokens.txt", 921,
                "87c8ef66eae5473ed0cc0366b3964c736ca6c5f676c979522ea31234e47430b9"),
            ModelFile("espeak-ng-data/phontab", "$PIPER/espeak-ng-data/phontab", 55_796,
                "886f3fa402cb0ba73d483aa8ad000af47a6b7cc06293c75a97913fba68a530f6"),
            ModelFile("espeak-ng-data/phonindex", "$PIPER/espeak-ng-data/phonindex", 39_074,
                "3ca7b8fa3b42624e4b0f152707e7a39245fce569aa99ea47c055d9e622fcf0c4"),
            ModelFile("espeak-ng-data/phondata", "$PIPER/espeak-ng-data/phondata", 550_424,
                "4e0288957874029a8c3c9f41a8f517ad4bf18127046decbdd4b9d1d6807ce3a3"),
            ModelFile("espeak-ng-data/intonations", "$PIPER/espeak-ng-data/intonations", 2_040,
                "3f8af65fd3eda9759a10f021d61361c120871f463515229c925995c7f90918cc"),
            ModelFile("espeak-ng-data/en_dict", "$PIPER/espeak-ng-data/en_dict", 166_944,
                "71bd330ba8a2e3e8076e631508208ef49449d6147c17b7bd2b4b1e1468292e35"),
            ModelFile("espeak-ng-data/lang/gmw/en", "$PIPER/espeak-ng-data/lang/gmw/en", 140,
                "4605d5330801de3641c6e366d15f129ea1f5ffbce8722642aba01ace07ab9c83"),
            ModelFile("espeak-ng-data/lang/gmw/en-US", "$PIPER/espeak-ng-data/lang/gmw/en-US", 257,
                "41534c2a22df5dd4f1052ff9e1a33a3ea7bff5a26b5c02bdad5ba8ddb7524704"),
        ),
        license = "voice: research-only data (Blizzard 2013); espeak-ng data: GPL-3.0-or-later",
        licenseUrl = "https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html",
        timingOnly = true,
        note = "timing only, never shipped",
    )

    val speechModels: List<CatalogModel> = listOf(moonshineTinyEn, moonshineBaseEn)

    val all: List<CatalogModel> = listOf(gemma4E2b, moonshineTinyEn, moonshineBaseEn, sileroVad, piperTimingVoice)
}
