import com.android.build.api.artifact.SingleArtifact
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.util.zip.ZipFile

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
    compileSdk = 36

    defaultConfig {
        applicationId = "com.domovoi.app"
        minSdk = 26
        targetSdk = 36
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
            // Room mode (roommode/): the satellite protocol fixtures the core's
            // and the Pi's tests read too, and the shared enrolment payload
            // cases (docs/uml/spare-device-enrol-fixture.json, also read by
            // satellite/enrolment.py's and the dashboard's tests).
            val satFixtures = rootProject.file("../docs/uml/satellite-fixtures.json")
            test.systemProperty("domovoi.satellite.fixtures", satFixtures.absolutePath)
            test.inputs.file(satFixtures).withPathSensitivity(PathSensitivity.RELATIVE)
            val enrolFixture = rootProject.file("../docs/uml/spare-device-enrol-fixture.json")
            test.systemProperty("domovoi.enrol.fixture", enrolFixture.absolutePath)
            test.inputs.file(enrolFixture).withPathSensitivity(PathSensitivity.RELATIVE)
            // The pinned openWakeWord models (domovoi/satellite_media/fetchers.py)
            // and the room's wake-word models are not in the repo (their
            // licence); the parity tests run when -Pdomovoi.oww.dir points at
            // a folder holding them (e.g. the openwakeword package's
            // resources/models) and skip otherwise.
            (findProperty("domovoi.oww.dir") as String?)?.let { test.systemProperty("domovoi.oww.dir", it) }
            (findProperty("domovoi.room.clips") as String?)?.let { test.systemProperty("domovoi.room.clips", it) }
            test.systemProperty("domovoi.repo.dir", rootProject.file("..").absolutePath)
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

/**
 * Every 64-bit native library in the built APKs loads on a phone with 16 KB
 * memory pages (Android 15+ phones may use them, Google Play requires it of
 * apps targeting Android 15 and later, and Android 16 runs an app that is not
 * in a page-size compatibility mode with a warning to the user): each ELF
 * LOAD segment aligned to 16 KB or more, and the library stored uncompressed
 * at a 16 KB offset in the APK, so it maps straight from there. Checked on
 * the APKs themselves, so a library that arrives with a dependency bump (as
 * CameraX 1.3's libimage_processing_util_jni.so did, 4 KB) fails the build.
 * 32-bit libraries are exempt: no 32-bit process runs with 16 KB pages.
 */
abstract class NativeAlignmentTask : DefaultTask() {
    @get:InputFiles
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val apkDir: DirectoryProperty

    @get:OutputFile
    abstract val report: RegularFileProperty

    private val page = 16_384L
    private val checked = Regex("""^lib/(arm64-v8a|x86_64)/[^/]+\.so$""")

    /** The p_align of each PT_LOAD segment of a 64-bit ELF file; null when
     *  [b] is not one. */
    private fun loadAligns(b: ByteArray): List<Long>? {
        if (b.size < 64 || b[0] != 0x7f.toByte() || b[1] != 'E'.code.toByte() || b[4].toInt() != 2) return null
        val bb = ByteBuffer.wrap(b).order(if (b[5].toInt() == 1) ByteOrder.LITTLE_ENDIAN else ByteOrder.BIG_ENDIAN)
        val phoff = bb.getLong(0x20)
        val size = bb.getShort(0x36).toInt() and 0xffff
        val count = bb.getShort(0x38).toInt() and 0xffff
        return (0 until count).mapNotNull { i ->
            val at = (phoff + i.toLong() * size).toInt()
            if (bb.getInt(at) == 1) bb.getLong(at + 0x30) else null
        }
    }

    /** name -> (stored uncompressed, offset of its data in the file), from
     *  the zip's central directory and each entry's local header. */
    private fun entryOffsets(apk: File): Map<String, Pair<Boolean, Long>> {
        val raf = RandomAccessFile(apk, "r")
        return raf.use {
            val tail = minOf(raf.length(), 65_557L).toInt()
            val end = ByteArray(tail).also { buf -> raf.seek(raf.length() - tail); raf.readFully(buf) }
            val eocd = (tail - 22 downTo 0).firstOrNull { i ->
                end[i] == 0x50.toByte() && end[i + 1] == 0x4b.toByte() && end[i + 2] == 0x05.toByte() && end[i + 3] == 0x06.toByte()
            } ?: throw GradleException("$apk: no end of central directory")
            val e = ByteBuffer.wrap(end).order(ByteOrder.LITTLE_ENDIAN)
            val entries = e.getShort(eocd + 10).toInt() and 0xffff
            val cdSize = e.getInt(eocd + 12).toLong() and 0xffffffffL
            val cdOffset = e.getInt(eocd + 16).toLong() and 0xffffffffL
            val cd = ByteArray(cdSize.toInt()).also { buf -> raf.seek(cdOffset); raf.readFully(buf) }
            val c = ByteBuffer.wrap(cd).order(ByteOrder.LITTLE_ENDIAN)
            val out = mutableMapOf<String, Pair<Boolean, Long>>()
            var p = 0
            repeat(entries) {
                val method = c.getShort(p + 10).toInt() and 0xffff
                val nameLen = c.getShort(p + 28).toInt() and 0xffff
                val extraLen = c.getShort(p + 30).toInt() and 0xffff
                val commentLen = c.getShort(p + 32).toInt() and 0xffff
                val local = c.getInt(p + 42).toLong() and 0xffffffffL
                val name = String(cd, p + 46, nameLen, Charsets.UTF_8)
                val lh = ByteArray(30).also { buf -> raf.seek(local); raf.readFully(buf) }
                val l = ByteBuffer.wrap(lh).order(ByteOrder.LITTLE_ENDIAN)
                val data = local + 30 + (l.getShort(26).toInt() and 0xffff) + (l.getShort(28).toInt() and 0xffff)
                out[name] = (method == 0) to data
                p += 46 + nameLen + extraLen + commentLen
            }
            out
        }
    }

    @TaskAction
    fun check() {
        val apks = apkDir.get().asFile.walkTopDown().filter { it.isFile && it.name.endsWith(".apk") }.sortedBy { it.name }.toList()
        if (apks.isEmpty()) throw GradleException("no APK in ${apkDir.get().asFile}")
        val lines = mutableListOf<String>()
        val bad = mutableListOf<String>()
        for (apk in apks) {
            val offsets = entryOffsets(apk)
            ZipFile(apk).use { zip ->
                for (entry in zip.entries().asSequence().filter { checked.matches(it.name) }) {
                    val aligns = loadAligns(zip.getInputStream(entry).readBytes())
                    val (stored, at) = offsets[entry.name] ?: (false to -1L)
                    val problems = buildList {
                        if (aligns == null) add("not a 64-bit ELF library")
                        else if (aligns.isEmpty() || aligns.any { it < page }) add("LOAD segments aligned to ${aligns.distinct()}, not 16 KB")
                        if (!stored) add("compressed in the APK")
                        else if (at % page != 0L) add("at offset $at, not 16 KB aligned in the APK")
                    }
                    val line = "${apk.name} ${entry.name}: " + if (problems.isEmpty()) "16 KB ok (${aligns!!.distinct().joinToString()})" else problems.joinToString("; ")
                    lines += line
                    if (problems.isNotEmpty()) bad += line
                }
            }
        }
        report.get().asFile.apply { parentFile.mkdirs() }.writeText(lines.joinToString("\n", postfix = "\n"))
        if (bad.isNotEmpty()) {
            throw GradleException("native libraries not ready for 16 KB pages:\n" + bad.joinToString("\n"))
        }
    }
}

androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(voiceAssets, VoiceAssetsTask::outputDir)
        val cap = variant.name.replaceFirstChar { it.uppercase() }
        val alignment = tasks.register<NativeAlignmentTask>("check${cap}NativeAlignment") {
            apkDir.set(variant.artifacts.get(SingleArtifact.APK))
            report.set(layout.buildDirectory.file("reports/native-alignment/${variant.name}.txt"))
        }
        tasks.matching { it.name == "assemble$cap" }.configureEach { dependsOn(alignment) }
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
    // Room mode's QR scanner (roommode/RoomScanner.kt): CameraX for the
    // preview and its frames, ZXing's core decoder for the code. Apache-2.0.
    implementation(libs.androidx.camera.camera2)
    implementation(libs.androidx.camera.lifecycle)
    implementation(libs.androidx.camera.view)
    implementation(libs.zxing.core)
    debugImplementation(libs.androidx.compose.ui.tooling)
    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.onnxruntime.jvm)
}
