package com.domovoi.app.update

import android.content.Context
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.content.pm.Signature
import android.os.Build
import java.security.MessageDigest

/**
 * What [AppUpdate.decide] needs to know about this install, read from the
 * package manager once per process (none of it changes while the app runs:
 * an update starts a new process).
 */
object InstallFacts {
    @Volatile
    private var cached: ThisInstall? = null

    fun read(context: Context): ThisInstall = cached ?: runCatching { readNow(context.applicationContext ?: context) }
        .onSuccess { cached = it }
        // Unreadable: no ABIs, so no notice is ever shown from it.
        .getOrElse { ThisInstall(0, "", debuggable = false, installer = null, emptySet(), emptyList()) }

    private fun readNow(context: Context): ThisInstall {
        val pm = context.packageManager
        val pkg = context.packageName
        @Suppress("DEPRECATION")
        val info = runCatching { pm.getPackageInfo(pkg, 0) }.getOrNull()
        @Suppress("DEPRECATION")
        val code = when {
            info == null -> 0L
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.P -> info.longVersionCode
            else -> info.versionCode.toLong()
        }
        return ThisInstall(
            versionCode = code,
            versionName = info?.versionName.orEmpty(),
            debuggable = (context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0,
            installer = installer(pm, pkg),
            signerSha256 = runCatching { signerDigests(signingCertificates(pm, pkg)) }.getOrDefault(emptySet()),
            supportedAbis = Build.SUPPORTED_ABIS?.toList().orEmpty(),
        )
    }

    /** The package that installed this app: getInstallSourceInfo from
     *  Android 11, getInstallerPackageName before it. */
    private fun installer(pm: PackageManager, pkg: String): String? = runCatching {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            pm.getInstallSourceInfo(pkg).installingPackageName
        } else {
            @Suppress("DEPRECATION")
            pm.getInstallerPackageName(pkg)
        }
    }.getOrNull()

    /** The DER bytes of each certificate this install is signed with, the
     *  rotation history included (v3), so a release signed by a rotated
     *  key still counts as this install's key. */
    private fun signingCertificates(pm: PackageManager, pkg: String): List<ByteArray> =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            val signing = pm.getPackageInfo(pkg, PackageManager.GET_SIGNING_CERTIFICATES).signingInfo
            val certs: Array<out Signature>? = when {
                signing == null -> null
                signing.hasMultipleSigners() -> signing.apkContentsSigners
                else -> signing.signingCertificateHistory
            }
            certs?.map { it.toByteArray() }.orEmpty()
        } else {
            @Suppress("DEPRECATION")
            val certs: Array<out Signature>? = pm.getPackageInfo(pkg, PackageManager.GET_SIGNATURES).signatures
            certs?.map { it.toByteArray() }.orEmpty()
        }

    /** SHA-256 of each certificate, lower-case hex: the form release.json's
     *  `cert_sha256` takes (apksigner's "certificate SHA-256 digest"). */
    fun signerDigests(certs: List<ByteArray>): Set<String> = certs.mapTo(LinkedHashSet()) { der ->
        MessageDigest.getInstance("SHA-256").digest(der).joinToString("") { "%02x".format(it) }
    }
}
