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
 * The assistant's models ([assistant], the benchmark's list) and the
 * phone's voices ([voices]): the three public-domain Piper voices the server
 * speaks with, of which only the one in use is downloaded (Settings ->
 * Voice). Until it is, the phone speaks with Android's own TextToSpeech.
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

    val speechModels: List<CatalogModel> = listOf(moonshineTinyEn, moonshineBaseEn)

    /** What the assistant's benchmark downloads and runs. */
    val assistant: List<CatalogModel> = listOf(gemma4E2b, moonshineTinyEn, moonshineBaseEn, sileroVad)

    /** What the assistant listens with: Moonshine tiny for speech to text
     *  (M0: 21-121 ms per question on the owner's phone) and Silero VAD for
     *  the end of speech. */
    val listening: List<CatalogModel> = listOf(moonshineTinyEn, sileroVad)

    /** Everything the assistant downloads in its setup: the language model,
     *  for text chat, and what it listens with, for voice. A phone set up
     *  before voice (M1) has only the first; the mic offers the rest. */
    val chat: List<CatalogModel> = listOf(gemma4E2b) + listening

    /** The language model the assistant answers with. */
    val chatModel: CatalogModel get() = gemma4E2b

    private const val PIPER_REV = "c10ece1aade47bb51c153c893d14e5bf8e5b7117"
    private const val PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/$PIPER_REV/en/en_US"

    /** One of Bryce Beattie's Piper voices (medium, 22,050 Hz): the model and
     *  its config, pinned to the revision the server pins. Trained from
     *  scratch on public-domain recordings (no lessac data); the author
     *  releases them into the public domain (brycebeattie.com/files/tts/,
     *  checked 2026-10-12). Sizes and hashes from the downloaded files; the
     *  model's SHA-256 is its Hugging Face LFS id. */
    private fun piperVoice(speaker: String, name: String, onnxSha: String, jsonSize: Long, jsonSha: String) =
        CatalogModel(
            id = "en_US-$speaker-medium",
            name = name,
            kind = ModelKind.TTS,
            files = listOf(
                ModelFile("en_US-$speaker-medium.onnx", "$PIPER/$speaker/medium/en_US-$speaker-medium.onnx",
                    63_531_379, onnxSha),
                ModelFile("en_US-$speaker-medium.onnx.json", "$PIPER/$speaker/medium/en_US-$speaker-medium.onnx.json",
                    jsonSize, jsonSha),
            ),
            license = "public domain",
            licenseUrl = "https://brycebeattie.com/files/tts/",
        )

    val norman = piperVoice("norman", "Norman", "b9739443232a80a59c7d18810dd856899bf16a7964725f5ab81ea49b1351cb71",
        4_968, "6c2db7f558a4a8deb9fe822583c1c5105f6c4e834dd0f9de8ad17a888ee9fe1d")
    val kristin = piperVoice("kristin", "Kristin", "5849957f929cbf720c258f8458692d6103fff2f0e3d3b19c8259474bb06a18d4",
        4_968, "5681426d4aead22195de70531eeeeddb46493cfaffc5764b2ea3db73428b651c")
    val ljspeech = piperVoice("ljspeech", "Linda", "6f52a751e2349abe7a76735eb09dc1875298c77ea2342ffd2fef79ff81b87f22",
        4_972, "141d612cc0a95ed7efc1ca936b845c2364967f2e9217c5dbfcf69fc4d6c65860")

    /** The phone's voices, the server's three; Kristin is the default
     *  (the server's DEFAULT_PIPER_VOICE since 2026-10-12, Norman before). */
    val voices: List<CatalogModel> = listOf(norman, kristin, ljspeech)

    val defaultVoice: CatalogModel get() = kristin

    fun voice(id: String?): CatalogModel? = voices.firstOrNull { it.id == id }

    /** Everything the download job may fetch. */
    val all: List<CatalogModel> = assistant + voices
}
