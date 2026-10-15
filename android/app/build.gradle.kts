import java.security.MessageDigest

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

/*
 * The processors (ABIs) the app carries native code for. Every native
 * library has all four except LiteRT-LM, the on-device assistant's engine,
 * which ships only 64-bit ones; so a 32-bit APK is the whole app without the
 * on-device assistant, which hides itself there (assistant/AssistantSupport.kt).
 *
 *   arm64-v8a    64-bit phones: everything
 *   armeabi-v7a  32-bit phones (old, budget, Android Go): no on-device assistant
 *   x86_64       the emulator, x86 Chromebooks: everything
 *   x86          the 32-bit emulator image: as armeabi-v7a
 *
 * - Default: one APK for arm64-v8a + x86_64, a phone and the emulator.
 * - `-Pdomovoi.abis=<list>|all`: one APK for exactly those ABIs
 *   (`-Pdomovoi.abis=arm64-v8a` is a phone-only APK about half the size; the
 *   libraries are stored uncompressed).
 * - `-Pdomovoi.apkPerAbi`: one APK per ABI, all four unless domovoi.abis
 *   narrows them, as app-<abi>-<buildType>.apk; each carries only its own
 *   ABI's libraries. What a GitHub release attaches.
 * - An App Bundle (any `bundle*` task on the command line) carries all four
 *   unless domovoi.abis says otherwise; Play splits it per ABI, so each phone
 *   downloads only its own ABI's libraries (android/README.md, "ABIs and APKs").
 * See docs/ANDROID_COMPATIBILITY.md.
 */
val allAbis = listOf("arm64-v8a", "armeabi-v7a", "x86_64", "x86")
val apkPerAbi = (findProperty("domovoi.apkPerAbi") as String?)?.let { it.trim() != "false" } ?: false
val bundling = gradle.startParameter.taskNames.any { it.substringAfterLast(':').startsWith("bundle") }
val appAbis: List<String> = (findProperty("domovoi.abis") as String?)?.trim()?.let { p ->
    if (p == "all") allAbis else p.split(',').map { it.trim() }.filter { it.isNotEmpty() }
} ?: if (apkPerAbi || bundling) allAbis else listOf("arm64-v8a", "x86_64")
appAbis.forEach { abi ->
    if (abi !in allAbis) throw GradleException("domovoi.abis: unknown ABI '$abi' (one of ${allAbis.joinToString()})")
}

android {
    namespace = "com.domovoi.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.domovoi.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
        // The ABIs above. One APK per ABI filters by splits (below)
        // instead of here.
        if (!apkPerAbi) {
            ndk { abiFilters += appAbis }
        }
    }

    splits {
        abi {
            isEnable = apkPerAbi
            reset()
            include(*appAbis.toTypedArray())
            isUniversalApk = false
        }
    }
    bundle {
        // Play makes one APK per ABI from the bundle (the default, stated):
        // a 32-bit phone gets armeabi-v7a's libraries, which hold no
        // LiteRT-LM, and a 64-bit phone arm64-v8a's.
        abi { enableSplit = true }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
    }
    // Speech recognition and voice detection: sherpa-onnx's JNI library,
    // built from source WITHOUT its text to speech (espeak-ng, GPL-3.0) by
    // android/native/sherpa-onnx/build.sh, with the Kotlin API it binds to
    // from the same commit. Its one native dependency, libonnxruntime.so,
    // comes from the onnxruntime-android dependency below.
    sourceSets["main"].jniLibs.srcDir("../native/sherpa-onnx/jniLibs")
    sourceSets["main"].java.srcDir("../native/sherpa-onnx/kotlin")
    // ONNX Runtime's Java API (and so its JNI bridge, libonnxruntime4j_jni.so)
    // is used since the phone's own voice: the phonemizer's out-of-dictionary
    // model and the Piper voice run on it (phonemizer/, assistant/PiperVoice.kt).
    testOptions {
        // JVM unit tests: stub android.* so pure-Kotlin helpers that touch
        // framework classes indirectly (icons, Log) don't throw.
        unitTests.isReturnDefaultValues = true
        // The phonemizer's golden test reads domovoi/phonemizer/ itself
        // (golden.json and data/), the same files the server tests read.
        unitTests.all { test ->
            val phonemizer = rootProject.file("../domovoi/phonemizer")
            test.systemProperty("domovoi.phonemizer.dir", phonemizer.absolutePath)
            test.inputs.file(File(phonemizer, "golden.json")).withPathSensitivity(PathSensitivity.RELATIVE)
            test.inputs.dir(File(phonemizer, "data")).withPathSensitivity(PathSensitivity.RELATIVE)
            // The built-in sample clips the app ships (VoiceClipsTest).
            val clips = rootProject.file("../domovoi/voice_clips")
            test.systemProperty("domovoi.voice_clips.dir", clips.absolutePath)
            test.inputs.dir(clips).withPathSensitivity(PathSensitivity.RELATIVE)
            // GET /api/voice as the server serves it (docs/uml/voice-api-fixture.json),
            // read by voice/VoiceApiContractTest.kt.
            val voiceApi = rootProject.file("../docs/uml/voice-api-fixture.json")
            test.systemProperty("domovoi.voice_api.fixture", voiceApi.absolutePath)
            test.inputs.file(voiceApi).withPathSensitivity(PathSensitivity.RELATIVE)
        }
    }
}

/**
 * The phone's voice data, from its one source in the repo into the app's
 * assets at build time, so the app never carries a hand-made copy:
 *
 * - `phonemizer/`: every file `domovoi/phonemizer/data/SHA256SUMS` lists
 *   (CMUdict and its licence, the corrections, weak forms, the default
 *   "say it like" list, the voices' id map, the out-of-dictionary model),
 *   each checked against that hash first; a mismatch fails the build.
 * - `voice_clips/`: the shipped voices' built-in sample clips
 *   (`domovoi/voice_clips/`, what "play a sample" plays before a voice is
 *   downloaded), checked against their SHA256SUMS the same way.
 * - `licences/sherpa-onnx/`: sherpa-onnx's licence and those of everything
 *   compiled into it (android/native/sherpa-onnx/), for the licences card.
 */
abstract class VoiceAssetsTask : DefaultTask() {
    @get:InputDirectory
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val phonemizerData: DirectoryProperty

    @get:InputDirectory
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val voiceClips: DirectoryProperty

    @get:InputFile
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val sherpaLicense: RegularFileProperty

    @get:InputDirectory
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val sherpaLicenses: DirectoryProperty

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    /** Every file [src]/SHA256SUMS lists, hash-checked, into [dest]. */
    private fun copyListed(src: File, dest: File, what: String) {
        dest.mkdirs()
        val sums = File(src, "SHA256SUMS")
        val listed = sums.readLines().map { it.trim() }.filter { it.isNotEmpty() && !it.startsWith("#") }
        if (listed.isEmpty()) throw GradleException("$sums lists no files")
        for (line in listed) {
            val parts = line.split(Regex("""\s+"""), limit = 2)
            if (parts.size != 2) throw GradleException("$sums: bad line '$line'")
            val want = parts[0].lowercase()
            val path = parts[1].removePrefix("*")
            val file = File(src, path)
            if (!file.isFile) throw GradleException("$what: $path is listed in SHA256SUMS but missing")
            val got = MessageDigest.getInstance("SHA-256").digest(file.readBytes()).joinToString("") { "%02x".format(it) }
            if (got != want) throw GradleException("$what: $path is $got, SHA256SUMS says $want")
            file.copyTo(File(dest, path), overwrite = true)
        }
        sums.copyTo(File(dest, "SHA256SUMS"), overwrite = true)
    }

    @TaskAction
    fun copy() {
        val out = outputDir.get().asFile
        out.deleteRecursively()
        copyListed(phonemizerData.get().asFile, File(out, "phonemizer"), "phonemizer data")
        copyListed(voiceClips.get().asFile, File(out, "voice_clips"), "voice clips")
        val notices = File(out, "licences/sherpa-onnx").apply { mkdirs() }
        sherpaLicense.get().asFile.copyTo(File(notices, "LICENSE"), overwrite = true)
        sherpaLicenses.get().asFile.copyRecursively(notices, overwrite = true)
    }
}

val voiceAssets = tasks.register<VoiceAssetsTask>("voiceAssets") {
    phonemizerData.set(rootProject.layout.projectDirectory.dir("../domovoi/phonemizer/data"))
    voiceClips.set(rootProject.layout.projectDirectory.dir("../domovoi/voice_clips"))
    sherpaLicense.set(rootProject.layout.projectDirectory.file("native/sherpa-onnx/LICENSE"))
    sherpaLicenses.set(rootProject.layout.projectDirectory.dir("native/sherpa-onnx/licenses"))
}

androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(voiceAssets, VoiceAssetsTask::outputDir)
    }
}

// Kotlin 2.4 makes the old `android { kotlinOptions { jvmTarget } }` block an
// error; this is the same setting in the compilerOptions DSL.
kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons)
    implementation(libs.androidx.compose.adaptive)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.okhttp)
    implementation(libs.coil.compose)
    implementation(libs.media3.exoplayer)
    implementation(libs.media3.session)
    implementation(libs.media3.datasource.okhttp)
    implementation(libs.media3.ui)
    implementation(libs.coil.video)
    // On-device assistant: LiteRT-LM runs the language model; sherpa-onnx
    // (android/native/sherpa-onnx, above) listens, on ONNX Runtime; the
    // phone's own TextToSpeech speaks.
    implementation(libs.litertlm.android)
    implementation(libs.onnxruntime.android)
    // The model download runs as a WorkManager job, so it survives leaving
    // the app, an app restart and a dropped Wi-Fi connection.
    implementation(libs.androidx.work.runtime.ktx)
    debugImplementation(libs.androidx.compose.ui.tooling)
    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.onnxruntime.jvm)
}
