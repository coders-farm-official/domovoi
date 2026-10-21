import com.android.build.api.artifact.SingleArtifact
import org.gradle.api.artifacts.component.ModuleComponentIdentifier
import org.gradle.api.artifacts.result.ResolvedArtifactResult
import org.gradle.maven.MavenModule
import org.gradle.maven.MavenPomArtifact
import org.gradle.process.CommandLineArgumentProvider
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.util.zip.ZipFile
import java.util.zip.ZipInputStream
import javax.xml.parsers.DocumentBuilderFactory
import org.w3c.dom.Element

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
        // One sequence for GitHub releases and Google Play: YYMMDDNN, the
        // build's date and that day's number (the assistant plan's M4).
        // Play refuses an upload whose versionCode is not above the last
        // one, and a phone refuses an update that goes down, so it only
        // ever goes up; release.ps1 and play-bundle.ps1 both read it here.
        versionCode = 26102001
        versionName = "1.0.0-rc3"
        // The ABIs above. One APK per ABI filters by splits (below)
        // instead of here.
        if (!apkPerAbi) {
            ndk { abiFilters += appAbis }
        }
        // The launcher's name (AndroidManifest.xml); a bench build says so.
        manifestPlaceholders["appLabel"] = "domovoi"
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
        // `-Pdomovoi.idSuffix=.bench`: a debug build that installs BESIDE the
        // release app (its own package, data and model download), named
        // "domovoi bench", so a phone keeps its real install while it runs
        // the benchmark card. Without the property nothing changes.
        debug {
            (findProperty("domovoi.idSuffix") as String?)?.trim()?.takeIf { it.isNotEmpty() }?.let { suffix ->
                if (!Regex("""^\.[a-z][a-z0-9_]*$""").matches(suffix)) {
                    throw GradleException("domovoi.idSuffix: '$suffix' is not a package suffix like .bench")
                }
                applicationIdSuffix = suffix
                versionNameSuffix = "-${suffix.removePrefix(".")}"
                manifestPlaceholders["appLabel"] = "domovoi ${suffix.removePrefix(".")}"
            }
        }
        release {
            // R8 shrinks, optimises and renames the code, and drops the
            // resources nothing uses (Google Play checks a release is
            // optimised). What must keep its name (JNI, reflection) is in
            // proguard-rules.pro. Each build writes
            // app/build/outputs/mapping/release/mapping.txt, what turns a
            // release's stack traces back into names: the App Bundle carries
            // it for Play, and release.ps1 / play-bundle.ps1 keep a copy
            // beside the signed files.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // No ndk.debugSymbolLevel: every native library arrives built and
            // stripped (the AARs', and native/sherpa-onnx's), with no symbols
            // beyond the dynamic ones Play reads from the libraries
            // themselves, and extracting even those needs the NDK, which this
            // build otherwise never uses. Play's "no debug symbols" note on a
            // bundle is advice, not a requirement.
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
        // BuildConfig.DEBUG: logcat carries spoken words in debug builds only
        // (diagnostics/SpokenLog.kt, delta review N7).
        buildConfig = true
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
            // The variant's open-source list (openSource, below), as the APK
            // carries it: OpenSourceLicencesTest holds it to the licences
            // decided by hand.
            val variant = test.name.removePrefix("test").removeSuffix("UnitTest").replaceFirstChar { it.lowercase() }
            val list = tasks.named<OpenSourceTask>("${variant}OpenSource")
            test.inputs.files(list).withPathSensitivity(PathSensitivity.RELATIVE)
            test.jvmArgumentProviders.add(CommandLineArgumentProvider {
                listOf("-Ddomovoi.licences.dir=${list.get().outputDir.get().asFile.absolutePath}")
            })
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

/**
 * The user manual (web/static/manual-content.json, the dashboard's own
 * file) into the assets as `manual/manual-content.json`: a phone chat's
 * help desk answers questions about Domovoi from it, offline and without
 * asking a server (assistant/LocalHelp.kt). The Manual screen still reads
 * the connected server's copy, so it matches that server's version.
 * With it, the chat's weak-spots table (domovoi/doc_qa/chat-weak-spots.json)
 * as `docs/chat-weak-spots.json`: the phone's heads-up rules read the same
 * measured table the server's do (assistant/docs/DocHeadsUp.kt).
 */
abstract class ManualAssetsTask : DefaultTask() {
    @get:InputFile
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val manual: RegularFileProperty

    @get:InputFile
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val weakSpots: RegularFileProperty

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @TaskAction
    fun copy() {
        val out = outputDir.get().asFile
        out.deleteRecursively()
        manual.get().asFile.copyTo(File(out, "manual/manual-content.json"), overwrite = true)
        weakSpots.get().asFile.copyTo(File(out, "docs/chat-weak-spots.json"), overwrite = true)
    }
}

val manualAssets = tasks.register<ManualAssetsTask>("manualAssets") {
    manual.set(rootProject.layout.projectDirectory.file("../web/static/manual-content.json"))
    weakSpots.set(rootProject.layout.projectDirectory.file("../domovoi/doc_qa/chat-weak-spots.json"))
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

/**
 * The open-source libraries in a variant's APK, from its dependency graph
 * (`variant.runtimeConfiguration`: what gets packaged), for the licences
 * screen (Settings → About) and OpenSourceLicencesTest. Into the assets:
 *
 * - `licences/maven.json`: each library (group, name, version), its POM's
 *   name and address, and the licences its POM declares (else its parent
 *   POM's, up the chain);
 * - `licences/maven/<group>/<name>/…`: the licence and notice files the
 *   library carries itself (LICENSE, NOTICE, THIRD_PARTY_NOTICE…, anywhere
 *   in its jar, or in its AAR and the jars inside it); most carry none;
 * - `licences/texts/`: android/licences/ (the Apache-2.0 text, ONNX
 *   Runtime's licence and third-party notices), checked against its
 *   SHA256SUMS first.
 *
 * Which licence each library is under is decided by hand
 * (ui/screens/settings/OpenSourceLicences.kt) and held to these POMs by the
 * unit tests, so a new library, or a changed licence in an upgrade, fails
 * them until someone has looked.
 */
abstract class OpenSourceTask : DefaultTask() {
    /** "group:name:version" -> the library's artifact (its AAR or jar). */
    @get:Input
    abstract val artifacts: MapProperty<String, String>

    /** "group:name:version" -> its POM, then each parent POM in turn. */
    @get:Input
    abstract val poms: MapProperty<String, List<String>>

    @get:InputFiles
    @get:PathSensitive(PathSensitivity.NONE)
    abstract val files: ConfigurableFileCollection

    @get:InputDirectory
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val texts: DirectoryProperty

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    private val notice = Regex("""(?i)^(licen[cs]e|notice|third[_-]?party[_-]?notices?|copying)([._-][^/]*)?$""")

    private fun json(s: String?): String = if (s == null) "null" else buildString {
        append('"')
        for (c in s) when {
            c == '"' -> append("\\\"")
            c == '\\' -> append("\\\\")
            c == '\n' -> append("\\n")
            c == '\r' -> append("\\r")
            c == '\t' -> append("\\t")
            c < ' ' -> append("\\u%04x".format(c.code))
            else -> append(c)
        }
        append('"')
    }

    private fun pom(file: File): Element =
        DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(file).documentElement

    private fun Element.child(name: String): Element? =
        (0 until childNodes.length).map { childNodes.item(it) }.filterIsInstance<Element>()
            .firstOrNull { it.tagName == name }

    private fun Element.text(name: String): String? = child(name)?.textContent?.trim()?.ifBlank { null }

    /** Every licence or notice file in a zip (and the zips inside it):
     *  entry path -> bytes. */
    private fun notices(bytes: ByteArray, prefix: String = ""): Map<String, ByteArray> {
        val out = linkedMapOf<String, ByteArray>()
        ZipInputStream(bytes.inputStream()).use { zip ->
            while (true) {
                val e = zip.nextEntry ?: break
                if (e.isDirectory) continue
                val base = e.name.substringAfterLast('/')
                when {
                    e.name.endsWith(".jar") -> out += notices(zip.readBytes(), "$prefix${e.name}!")
                    notice.matches(base) && !base.endsWith(".class") -> out["$prefix${e.name}"] = zip.readBytes()
                }
            }
        }
        return out
    }

    @TaskAction
    fun write() {
        val out = outputDir.get().asFile
        out.deleteRecursively()
        val root = File(out, "licences").apply { mkdirs() }

        // The checked-in texts, hash-checked as the voice data is.
        val src = texts.get().asFile
        val sums = File(src, "SHA256SUMS").readLines().map { it.trim() }.filter { it.isNotEmpty() && !it.startsWith("#") }
        if (sums.isEmpty()) throw GradleException("android/licences/SHA256SUMS lists no files")
        for (line in sums) {
            val (want, path) = line.split(Regex("""\s+"""), limit = 2).let { it[0].lowercase() to it[1].removePrefix("*") }
            val f = File(src, path)
            if (!f.isFile) throw GradleException("android/licences: $path is listed in SHA256SUMS but missing")
            val got = MessageDigest.getInstance("SHA-256").digest(f.readBytes()).joinToString("") { "%02x".format(it) }
            if (got != want) throw GradleException("android/licences: $path is $got, SHA256SUMS says $want")
            f.copyTo(File(root, "texts/$path"), overwrite = true)
        }

        // A notice is kept once across all libraries: AndroidX's per-library
        // LICENSE.txt files are the Apache-2.0 text, which texts/ already has.
        fun norm(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
            .digest(bytes.toString(Charsets.UTF_8).replace(Regex("""\s+"""), " ").trim().toByteArray())
            .joinToString("") { "%02x".format(it) }
        val seen = File(root, "texts").walkTopDown().filter { it.isFile }.map { norm(it.readBytes()) }.toMutableSet()
        val rows = artifacts.get().toSortedMap().map { (coord, artifact) ->
            val (group, name, version) = coord.split(':')
            val chain = poms.get()[coord].orEmpty().map { pom(File(it)) }
            val own = chain.firstOrNull()
            val licences = chain.firstNotNullOfOrNull { p ->
                p.child("licenses")?.let { ls ->
                    (0 until ls.childNodes.length).map { ls.childNodes.item(it) }.filterIsInstance<Element>()
                        .filter { it.tagName == "license" }.map { it.text("name") to it.text("url") }
                }?.takeIf { it.isNotEmpty() }
            }.orEmpty()
            // The library's own notice files, unless the same text is kept already.
            val kept = notices(File(artifact).readBytes()).mapNotNull { (entry, bytes) ->
                if (!seen.add(norm(bytes))) return@mapNotNull null
                val rel = "maven/$group/$name/" + entry.replace('!', '_').replace('/', '_')
                File(root, rel).apply { parentFile.mkdirs() }.writeBytes(bytes)
                "licences/$rel"
            }
            "{\"group\":${json(group)},\"name\":${json(name)},\"version\":${json(version)}," +
                "\"pomName\":${json(own?.text("name"))},\"url\":${json(own?.text("url"))}," +
                "\"licences\":[" + licences.joinToString(",") { (n, u) -> "{\"name\":${json(n)},\"url\":${json(u)}}" } + "]," +
                "\"notices\":[" + kept.joinToString(",") { json(it) } + "]}"
        }
        File(root, "maven.json").writeText(rows.joinToString(",\n", "[\n", "\n]\n"))
    }
}

/** POM files, own first, then each parent, for each "group:name:version":
 *  resolved one by one (offline, from the cache the build already filled)
 *  so no version is traded for another. */
fun pomChains(coords: Collection<String>): Map<String, List<String>> {
    // Gradle's own way to a module's POM (MavenPomArtifact), which works for
    // libraries published with Gradle module metadata too; offline, before
    // any online build asked for them, the POMs Gradle keeps in its module
    // cache from resolving the dependencies.
    fun resolve(coord: String): File? {
        val (g, a, v) = coord.split(':')
        val viaGradle = runCatching {
            dependencies.createArtifactResolutionQuery().forModule(g, a, v)
                .withArtifacts(MavenModule::class.java, MavenPomArtifact::class.java).execute()
                .resolvedComponents.flatMap { it.getArtifacts(MavenPomArtifact::class.java) }
                .filterIsInstance<ResolvedArtifactResult>().firstOrNull()?.file
        }.getOrNull()
        return viaGradle ?: File(gradle.gradleUserHomeDir, "caches/modules-2/files-2.1/$g/$a/$v").listFiles().orEmpty()
            .map { File(it, "$a-$v.pom") }.firstOrNull { it.isFile }
    }
    fun parentOf(pom: File): String? = runCatching {
        val root = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(pom).documentElement
        val parent = (0 until root.childNodes.length).map { root.childNodes.item(it) }
            .filterIsInstance<Element>().firstOrNull { it.tagName == "parent" } ?: return null
        fun part(n: String) = (0 until parent.childNodes.length).map { parent.childNodes.item(it) }
            .filterIsInstance<Element>().firstOrNull { it.tagName == n }?.textContent?.trim()
        listOf(part("groupId"), part("artifactId"), part("version")).takeIf { p -> p.all { !it.isNullOrBlank() } }?.joinToString(":")
    }.getOrNull()
    return coords.associateWith { coord ->
        val chain = mutableListOf<String>()
        var next: String? = coord
        while (next != null && chain.size < 6) {
            val pom = resolve(next) ?: break
            chain += pom.absolutePath
            next = parentOf(pom)
        }
        chain
    }
}

androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(voiceAssets, VoiceAssetsTask::outputDir)
        variant.sources.assets?.addGeneratedSourceDirectory(manualAssets, ManualAssetsTask::outputDir)
        val openSource = tasks.register<OpenSourceTask>("${variant.name}OpenSource") {
            val resolved = variant.runtimeConfiguration.incoming.artifactView { lenient(false) }.artifacts.resolvedArtifacts
            artifacts.set(resolved.map { set ->
                set.mapNotNull { a ->
                    (a.id.componentIdentifier as? ModuleComponentIdentifier)?.let { id -> "${id.group}:${id.module}:${id.version}" to a.file.absolutePath }
                }.toMap()
            })
            poms.set(artifacts.map { pomChains(it.keys) })
            files.from(artifacts.map { m -> m.values.map(::File) }, poms.map { m -> m.values.flatten().map(::File) })
            texts.set(rootProject.layout.projectDirectory.dir("licences"))
        }
        variant.sources.assets?.addGeneratedSourceDirectory(openSource, OpenSourceTask::outputDir)
        val cap = variant.name.replaceFirstChar { it.uppercase() }
        val alignment = tasks.register<NativeAlignmentTask>("check${cap}NativeAlignment") {
            apkDir.set(variant.artifacts.get(SingleArtifact.APK))
            report.set(layout.buildDirectory.file("reports/native-alignment/${variant.name}.txt"))
        }
        tasks.matching { it.name == "assemble$cap" }.configureEach { dependsOn(alignment) }
    }
}

// The Compose compiler plugin adds Compose's group-key mapping to R8's
// mapping.txt in a minified build, with a tool it downloads for that
// (org.jetbrains.kotlin:compose-group-mapping). The mapping serves
// Compose's composition stack traces, which Compose 1.10 brought; this app's
// Compose (BOM 2024.12.01, Compose 1.7) has none, so the tool stays out of
// the build and out of gradle/verification-metadata.xml.
composeCompiler {
    includeComposeMappingFile.set(false)
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
