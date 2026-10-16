package com.domovoi.app.ui.screens.settings

import android.content.Context
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json

/** A licence as a library's POM declares it. */
@Serializable
internal data class PomLicence(val name: String? = null, val url: String? = null)

/**
 * One library in the APK, as the build lists it from the dependency graph
 * (app/build.gradle.kts OpenSourceTask, `licences/maven.json` in the
 * assets): its coordinates, its POM's name, address and licences, and the
 * licence or notice files it carries itself (asset paths).
 */
@Serializable
internal data class MavenLibrary(
    val group: String = "",
    val name: String = "",
    val version: String = "",
    val pomName: String? = null,
    val url: String? = null,
    val licences: List<PomLicence> = emptyList(),
    val notices: List<String> = emptyList(),
) {
    val coordinate: String get() = "$group:$name:$version"
}

/**
 * Which licence a library is under, decided by hand from its source (the
 * project's own LICENSE, not only its POM) and held to the POMs by
 * OpenSourceLicencesTest: every library in the build must match a rule,
 * every rule must match a library, and what a POM declares must agree.
 *
 * [group] matches that group and its sub-groups ("androidx" is every
 * androidx.*); [name] narrows it to one library. [texts] are the licence
 * texts the app carries for it (assets), beyond the library's own files.
 */
internal data class LicenceRule(
    val title: String,
    val group: String,
    val name: String? = null,
    /** SPDX ids, joined with " AND " when several apply. */
    val licence: String,
    val source: String,
    val note: String? = null,
    val texts: List<String> = emptyList(),
) {
    fun matches(lib: MavenLibrary): Boolean =
        (lib.group == group || lib.group.startsWith("$group.")) && (name == null || lib.name == name)

    /** Its SPDX ids one by one. */
    val ids: List<String> get() = licence.split(" AND ").map { it.trim() }
}

internal object OpenSourceLicences {
    const val MAVEN_LIST = "licences/maven.json"
    const val APACHE_TEXT = "licences/texts/Apache-2.0.txt"
    const val ORT_LICENCE = "licences/texts/onnxruntime/LICENSE"
    const val ORT_NOTICES = "licences/texts/onnxruntime/ThirdPartyNotices.txt"
    const val LIBYUV_LICENCE = "licences/texts/libyuv/LICENSE"

    /** The licences the app may contain (docs/CONTRIBUTING.md: permissive
     *  only). MPL-2.0 only for unmodified third-party code or data, and only
     *  as a rule's note (Eigen, the Public Suffix List), never as one. */
    val PERMISSIVE = setOf("Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "ISC", "public domain")

    /** Exact rules first: the first rule that matches decides. */
    val RULES: List<LicenceRule> = listOf(
        LicenceRule(
            "CameraX camera-core (room mode's QR scanner)", "androidx.camera", "camera-core",
            "Apache-2.0 AND BSD-3-Clause", "android.googlesource.com/platform/frameworks/support",
            "Builds in libyuv (BSD-3-Clause, the LibYuv Project Authors) for its image conversion.",
            listOf(APACHE_TEXT, LIBYUV_LICENCE),
        ),
        LicenceRule(
            "AndroidX and Jetpack Compose", "androidx", licence = "Apache-2.0",
            source = "android.googlesource.com/platform/frameworks/support",
            note = "Copyright The Android Open Source Project. Media3, CameraX, WorkManager, DataStore and the rest.",
            texts = listOf(APACHE_TEXT),
        ),
        LicenceRule(
            "LiteRT-LM", "com.google.ai.edge.litertlm", licence = "Apache-2.0", source = "github.com/google-ai-edge/LiteRT-LM",
            note = "Runs the on-device language model. Its third-party notice lists what is built into it; its one " +
                "GPL-2.0 entry (a Google runtime environment) is notice text only, nothing of it is in this app.",
            texts = listOf(APACHE_TEXT),
        ),
        LicenceRule(
            "ONNX Runtime", "com.microsoft.onnxruntime", licence = "MIT", source = "github.com/microsoft/onnxruntime",
            note = "Copyright (c) Microsoft Corporation. Runs speech recognition, the voice and the phonemizer's model; " +
                "its third-party notices list what it builds in (Eigen under MPL-2.0, unmodified, among them).",
            texts = listOf(ORT_LICENCE, ORT_NOTICES),
        ),
        LicenceRule("ZXing (room mode's QR decoder)", "com.google.zxing", licence = "Apache-2.0", source = "github.com/zxing/zxing",
            texts = listOf(APACHE_TEXT)),
        LicenceRule(
            "OkHttp", "com.squareup.okhttp3", licence = "Apache-2.0", source = "github.com/square/okhttp",
            note = "Copyright Square, Inc. Carries the Public Suffix List (publicsuffix.org) as data, unmodified, under MPL-2.0.",
            texts = listOf(APACHE_TEXT),
        ),
        LicenceRule("Okio", "com.squareup.okio", licence = "Apache-2.0", source = "github.com/square/okio", texts = listOf(APACHE_TEXT)),
        LicenceRule("Coil", "io.coil-kt", licence = "Apache-2.0", source = "github.com/coil-kt/coil", texts = listOf(APACHE_TEXT)),
        LicenceRule("Kotlin", "org.jetbrains.kotlin", licence = "Apache-2.0", source = "github.com/JetBrains/kotlin",
            note = "Copyright JetBrains s.r.o. and Kotlin Programming Language contributors.", texts = listOf(APACHE_TEXT)),
        LicenceRule("kotlinx.coroutines and kotlinx.serialization", "org.jetbrains.kotlinx", licence = "Apache-2.0",
            source = "github.com/Kotlin", texts = listOf(APACHE_TEXT)),
        LicenceRule("JetBrains Java annotations", "org.jetbrains", "annotations", "Apache-2.0", "github.com/JetBrains/java-annotations",
            texts = listOf(APACHE_TEXT)),
        LicenceRule("Guava", "com.google.guava", licence = "Apache-2.0", source = "github.com/google/guava", texts = listOf(APACHE_TEXT)),
        LicenceRule("Gson", "com.google.code.gson", licence = "Apache-2.0", source = "github.com/google/gson", texts = listOf(APACHE_TEXT)),
        LicenceRule("Error Prone annotations", "com.google.errorprone", licence = "Apache-2.0", source = "github.com/google/error-prone",
            texts = listOf(APACHE_TEXT)),
        LicenceRule("AutoValue annotations", "com.google.auto.value", licence = "Apache-2.0", source = "github.com/google/auto",
            texts = listOf(APACHE_TEXT)),
        LicenceRule("Accompanist", "com.google.accompanist", licence = "Apache-2.0", source = "github.com/google/accompanist",
            texts = listOf(APACHE_TEXT)),
        LicenceRule("JSpecify", "org.jspecify", licence = "Apache-2.0", source = "jspecify.dev", texts = listOf(APACHE_TEXT)),
    )

    /** Libraries no Domovoi build may contain: analytics, advertising and
     *  crash reporting (the privacy card says there are none). By group. */
    val NEVER = listOf(
        "com.google.firebase", "com.google.android.gms", "com.crashlytics", "io.fabric", "io.sentry", "com.bugsnag",
        "com.appsflyer", "com.adjust", "com.facebook", "com.amplitude", "com.mixpanel", "com.segment", "com.flurry",
        "com.google.ads", "com.applovin", "com.unity3d.ads", "com.datadoghq", "com.newrelic", "io.branch",
    )

    fun ruleFor(lib: MavenLibrary): LicenceRule? = RULES.firstOrNull { it.matches(lib) }

    /** A POM's licence as an SPDX id: "BSD" when it says BSD without which;
     *  null when it is none this list knows. */
    fun spdx(l: PomLicence): String? {
        val n = l.name.orEmpty().lowercase()
        val u = l.url.orEmpty().lowercase()
        return when {
            "apache" in n || "apache.org/licenses/license-2.0" in u -> "Apache-2.0"
            n == "mit" || "mit license" in n || "opensource.org/licenses/mit" in u -> "MIT"
            "bsd" in n && ("3" in n || "new" in n || "revised" in n) -> "BSD-3-Clause"
            "bsd" in n && ("2" in n || "simplified" in n) -> "BSD-2-Clause"
            "bsd" in n -> "BSD"
            "isc" in n -> "ISC"
            else -> null
        }
    }

    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    fun parse(text: String): List<MavenLibrary> = json.decodeFromString(ListSerializer(MavenLibrary.serializer()), text)

    /** The libraries in this APK, from its assets; empty if the list is missing. */
    fun read(context: Context): List<MavenLibrary> =
        runCatching { context.assets.open(MAVEN_LIST).use { parse(it.readBytes().toString(Charsets.UTF_8)) } }.getOrDefault(emptyList())

    /** The libraries under each rule, in the rules' order; libraries no rule
     *  matches (the test forbids them) are left out. */
    fun grouped(libs: List<MavenLibrary>): List<Pair<LicenceRule, List<MavenLibrary>>> {
        val byRule = libs.groupBy { ruleFor(it) }
        return RULES.mapNotNull { r -> byRule[r]?.let { r to it.sortedBy { l -> l.coordinate } } }
    }

    /** "137 libraries: 135 under Apache-2.0, 1 MIT, 1 Apache-2.0 AND BSD-3-Clause". */
    fun summary(libs: List<MavenLibrary>): String {
        if (libs.isEmpty()) return "The list of libraries is not in this build."
        val counts = libs.groupingBy { ruleFor(it)?.licence ?: "unknown" }.eachCount().entries
            .sortedByDescending { it.value }.joinToString(", ") { "${it.value} ${it.key}" }
        return "${libs.size} libraries, from the app's build: $counts."
    }
}
