package com.domovoi.app.update

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * "A newer Domovoi app is available from your server" (v1.0.0 plan,
 * owner decision 8, 2026-10-16).
 *
 * A household server hands out the signed app that `android/release.json`
 * in its checkout pins (domovoi/android_app.py). This decides, from the
 * server's answer and from facts about this install only, whether this
 * phone should hear about a newer one. The phone asks its own household
 * server and nothing else: never the internet (local-first).
 *
 * - The main app reads the dashboard's open `GET /api/devices/android-app`
 *   (web/backend/api/devices.py: the core's public view plus `join_url`);
 *   a room-mode phone reads the core's public `GET /v1/android-app` on the
 *   core it proved, with `join_url` = that core + `/join`.
 * - Never for a Google Play install (Play updates it itself), never for a
 *   debug build, and never when the release is signed by a key this install
 *   isn't (it couldn't install over it anyway: a self-built or Play-signed
 *   copy).
 * - Newer is the release's `version_code` above this install's.
 * - The APK offered is the first of this phone's own ABIs, in Android's
 *   order of preference, that the release carries; never a lesser one (a
 *   64-bit phone isn't moved to the 32-bit app, which has no on-device
 *   assistant).
 * - "Get the update" opens the server's join page (domovoi/join_page/) in
 *   the browser with `?abi=<that ABI>&update=1`, which puts that APK first.
 *   When the server knows the release but holds no checked copy for this
 *   phone, the notice says an admin can press "Download the app to this
 *   server", with no button (the phone can't do that for them).
 */

/** The release a server offers (`release` in its answer). */
@Serializable
data class OfferedRelease(
    @SerialName("version_name") val versionName: String = "",
    @SerialName("version_code") val versionCode: Long = 0,
    @SerialName("cert_sha256") val certSha256: String? = null,
)

/** One APK of it, and whether the server holds a checked copy. */
@Serializable
data class OfferedApk(
    val abi: String = "",
    val file: String = "",
    val size: Long = 0,
    val ready: Boolean = false,
)

/** A server's answer: the dashboard's `/api/devices/android-app`, or the
 *  core's `/v1/android-app` with [joinUrl] filled in by the caller. */
@Serializable
data class AppOffer(
    @SerialName("join_url") val joinUrl: String? = null,
    val release: OfferedRelease? = null,
    val apks: List<OfferedApk> = emptyList(),
)

/** What this install is, read once from the package manager ([InstallFacts]). */
data class ThisInstall(
    val versionCode: Long,
    val versionName: String,
    /** A debug build (FLAG_DEBUGGABLE): signed with a debug key, so a
     *  release can't install over it. */
    val debuggable: Boolean,
    /** The package that installed this app, if Android says. */
    val installer: String?,
    /** SHA-256 (lower-case hex) of each certificate this install is signed
     *  with, rotation history included. Empty when it couldn't be read. */
    val signerSha256: Set<String>,
    /** Build.SUPPORTED_ABIS: this phone's ABIs, most preferred first. */
    val supportedAbis: List<String>,
)

/** What the app has to say about updates. Only [Available] and
 *  [NotOnServer] ever show outside Settings → About & updates. */
sealed interface UpdateState {
    /** Installed by Google Play, which updates it itself. */
    data object PlayStore : UpdateState

    /** A debug build: never compared with release builds. */
    data object DebugBuild : UpdateState

    /** The server didn't answer, or answered something unreadable. */
    data object Unknown : UpdateState

    /** The server has no app release to offer. */
    data object NoRelease : UpdateState

    /** Nothing newer than this install ([installed]) on the server ([offered]). */
    data class UpToDate(val offered: String, val installed: String) : UpdateState

    /** A newer release, signed by a key this install is not. */
    data class OtherKey(val version: String) : UpdateState

    /** A newer release with nothing for this phone's processor. */
    data class NoApkForThisPhone(val version: String) : UpdateState

    /** A newer release this server holds for this phone. [link] opens its
     *  join page for [abi]; null when the server gave no address a phone
     *  can open. */
    data class Available(val version: String, val versionCode: Long, val abi: String, val link: String?) : UpdateState

    /** A newer release the server knows of but holds no copy of for this
     *  phone yet: an admin downloads it on the dashboard. */
    data class NotOnServer(val version: String, val versionCode: Long) : UpdateState
}

/** A notice dismissed: hidden until a higher [versionCode], or, for a
 *  release that wasn't on the server yet ([ready] false), until it is. */
data class Dismissal(val versionCode: Long, val ready: Boolean)

object AppUpdate {
    /** Google Play's installer package. */
    const val PLAY_STORE = "com.android.vending"

    /** The ABIs a release carries (domovoi/android_app.py ABIS). */
    val RELEASE_ABIS = listOf("arm64-v8a", "armeabi-v7a")

    /** The join page's path (domovoi/main.py GET /join). */
    const val JOIN_PATH = "/join"

    fun isPlayInstall(installer: String?): Boolean = installer == PLAY_STORE

    fun isNewer(offered: Long, installed: Long): Boolean = offered > installed

    /** The APK for this phone: the first of [supportedAbis] (Android's
     *  order of preference) that [apks] carries; null when none. */
    fun pickApk(supportedAbis: List<String>, apks: List<OfferedApk>): OfferedApk? =
        supportedAbis.firstNotNullOfOrNull { abi -> apks.firstOrNull { it.abi == abi && abi in RELEASE_ABIS } }

    /** The join page at [joinUrl] for [abi] as an update (`?abi=…&update=1`),
     *  or null unless [joinUrl] is an http(s) address of a join page. */
    fun joinLink(joinUrl: String?, abi: String): String? {
        if (abi !in RELEASE_ABIS) return null
        val url = joinUrl?.trim()?.toHttpUrlOrNull() ?: return null
        if (url.encodedPath.trimEnd('/') != JOIN_PATH) return null
        return url.newBuilder()
            .encodedPath(JOIN_PATH)
            .query(null)
            .fragment(null)
            .addQueryParameter("abi", abi)
            .addQueryParameter("update", "1")
            .build()
            .toString()
    }

    fun decide(install: ThisInstall, offer: AppOffer?): UpdateState {
        if (isPlayInstall(install.installer)) return UpdateState.PlayStore
        if (install.debuggable) return UpdateState.DebugBuild
        if (offer == null) return UpdateState.Unknown
        val rel = offer.release ?: return UpdateState.NoRelease
        if (rel.versionName.isBlank() || rel.versionCode <= 0) return UpdateState.Unknown
        if (!isNewer(rel.versionCode, install.versionCode)) {
            return UpdateState.UpToDate(rel.versionName, install.versionName)
        }
        val cert = rel.certSha256?.trim()?.lowercase()
        if (!cert.isNullOrEmpty() && install.signerSha256.isNotEmpty() && cert !in install.signerSha256) {
            return UpdateState.OtherKey(rel.versionName)
        }
        val apk = pickApk(install.supportedAbis, offer.apks) ?: return UpdateState.NoApkForThisPhone(rel.versionName)
        if (!apk.ready) return UpdateState.NotOnServer(rel.versionName, rel.versionCode)
        return UpdateState.Available(rel.versionName, rel.versionCode, apk.abi, joinLink(offer.joinUrl, apk.abi))
    }

    /** Whether [state] is a notice to show, given what was dismissed. */
    fun shows(state: UpdateState, dismissed: Dismissal?): Boolean = when (state) {
        is UpdateState.Available -> dismissed == null || state.versionCode > dismissed.versionCode ||
            (state.versionCode == dismissed.versionCode && !dismissed.ready)
        is UpdateState.NotOnServer -> dismissed == null || state.versionCode > dismissed.versionCode
        else -> false
    }

    /** What dismissing [state] remembers; null for a state with no notice. */
    fun dismissalFor(state: UpdateState): Dismissal? = when (state) {
        is UpdateState.Available -> Dismissal(state.versionCode, ready = true)
        is UpdateState.NotOnServer -> Dismissal(state.versionCode, ready = false)
        else -> null
    }

    /** The notice's words (Home, the room screen), for the two states that
     *  have one. */
    fun noticeText(state: UpdateState): String? = when (state) {
        is UpdateState.Available -> if (state.link != null) {
            "Domovoi ${state.version} is available from your server."
        } else {
            "Domovoi ${state.version} is available from your server: on the dashboard, Settings → Phones & browsers → " +
                "get the Domovoi app."
        }
        is UpdateState.NotOnServer -> "Domovoi ${state.version} is out, but your server doesn't hold it for this " +
            "phone yet. An admin can press “Download the app to this server” on the dashboard (Settings → Phones & browsers)."
        else -> null
    }

    /** Settings → About & updates's line, for every state. */
    fun aboutText(state: UpdateState): String = noticeText(state) ?: when (state) {
        UpdateState.PlayStore -> "Installed from Google Play, which keeps it up to date."
        UpdateState.DebugBuild -> "Debug build: it isn't compared with the release builds your server hands out."
        UpdateState.Unknown -> "Your server didn't say which app release it offers."
        UpdateState.NoRelease -> "Your server has no app release to offer yet."
        is UpdateState.UpToDate -> if (state.installed == state.offered || state.installed.isBlank()) {
            "Up to date: Domovoi ${state.offered}, the release your server offers."
        } else {
            "Up to date: this phone has Domovoi ${state.installed}; your server offers ${state.offered}."
        }
        is UpdateState.OtherKey -> "This copy of the app is signed with a different key from Domovoi's " +
            "releases, so Domovoi ${state.version} from your server can't install over it."
        is UpdateState.NoApkForThisPhone -> "Your server's Domovoi ${state.version} has no build for this " +
            "phone's processor."
        else -> ""
    }
}
