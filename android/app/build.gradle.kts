import java.security.MessageDigest

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
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
        // The on-device assistant's native libraries: LiteRT-LM ships only
        // arm64-v8a and x86_64, so a 32-bit build could not load it.
        // arm64-v8a is every phone it targets; x86_64 is the emulator.
        // `-Pdomovoi.abis=arm64-v8a` builds a phone-only APK about half
        // the size (the libraries are stored uncompressed).
        ndk {
            abiFilters += (findProperty("domovoi.abis") as String?)?.split(',')?.map { it.trim() }
                ?: listOf("arm64-v8a", "x86_64")
        }
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
 * - `licences/sherpa-onnx/`: sherpa-onnx's licence and those of everything
 *   compiled into it (android/native/sherpa-onnx/), for the licences card.
 */
abstract class VoiceAssetsTask : DefaultTask() {
    @get:InputDirectory
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val phonemizerData: DirectoryProperty

    @get:InputFile
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val sherpaLicense: RegularFileProperty

    @get:InputDirectory
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val sherpaLicenses: DirectoryProperty

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @TaskAction
    fun copy() {
        val src = phonemizerData.get().asFile
        val out = outputDir.get().asFile
        out.deleteRecursively()
        val phonemizer = File(out, "phonemizer").apply { mkdirs() }
        val sums = File(src, "SHA256SUMS")
        val listed = sums.readLines().map { it.trim() }.filter { it.isNotEmpty() && !it.startsWith("#") }
        if (listed.isEmpty()) throw GradleException("$sums lists no files")
        for (line in listed) {
            val parts = line.split(Regex("""\s+"""), limit = 2)
            if (parts.size != 2) throw GradleException("$sums: bad line '$line'")
            val want = parts[0].lowercase()
            val path = parts[1].removePrefix("*")
            val file = File(src, path)
            if (!file.isFile) throw GradleException("phonemizer data: $path is listed in SHA256SUMS but missing")
            val got = MessageDigest.getInstance("SHA-256").digest(file.readBytes()).joinToString("") { "%02x".format(it) }
            if (got != want) throw GradleException("phonemizer data: $path is $got, SHA256SUMS says $want")
            file.copyTo(File(phonemizer, path), overwrite = true)
        }
        sums.copyTo(File(phonemizer, "SHA256SUMS"), overwrite = true)
        val notices = File(out, "licences/sherpa-onnx").apply { mkdirs() }
        sherpaLicense.get().asFile.copyTo(File(notices, "LICENSE"), overwrite = true)
        sherpaLicenses.get().asFile.copyRecursively(notices, overwrite = true)
    }
}

val voiceAssets = tasks.register<VoiceAssetsTask>("voiceAssets") {
    phonemizerData.set(rootProject.layout.projectDirectory.dir("../domovoi/phonemizer/data"))
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
