package com.domovoi.app.assistant

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import com.domovoi.app.data.LocalMedia
import com.domovoi.app.net.PageRequest

/**
 * The app's [PhoneContext]: what a phone chat reads, all of it already on
 * the phone. AppContainer hands in the pieces as plain functions (the
 * player's now-playing and sleep timer, whether the connection is up, the
 * cached room and listener names, the timer mirror, the saved books), so
 * this class holds no server client and can't reach one: a phone chat
 * never fetches from the home server (NoNetworkTest checks it).
 */
class AppPhoneContext(
    context: Context,
    private val settings: AssistantSettings,
    private val nowPlaying: () -> NowPlayingFact?,
    private val sleepRemainingSec: () -> Int?,
    /** A home server is set up on this phone. */
    private val serverSet: () -> Boolean,
    /** ...and connected now. */
    private val connected: () -> Boolean,
    /** The cached name of the listener picked in Settings. */
    private val listener: () -> String?,
    private val savedBooks: () -> List<FoundItem>,
    override val reader: PhoneReader = AndroidPhoneReader(context),
) : PhoneContext {
    private val ctx = context.applicationContext

    override fun facts(): PromptFacts {
        val set = serverSet()
        val up = set && connected()
        return PromptFacts(
            playing = nowPlaying(),
            sleepRemainingSec = sleepRemainingSec(),
            server = when {
                !set -> ServerLink.NONE
                up -> ServerLink.CONNECTED
                else -> ServerLink.NOT_CONNECTED
            },
            listener = listener(),
        )
    }

    override fun serverKnown(): Boolean = serverSet()

    override val library: PhoneLibrary = object : PhoneLibrary {
        override fun canFindMusic(): Boolean = LocalMedia.hasPermission(ctx, LocalMedia.audioPermission())
        override fun canFindVideos(): Boolean = LocalMedia.hasPermission(ctx, LocalMedia.videoPermission())
        override fun canFindBooks(): Boolean = savedBooks().isNotEmpty()

        override fun findMusic(query: String, limit: Int): List<FoundItem> =
            LocalMedia.pageTracks(ctx, query, PageRequest(null, 0, limit)).items.map {
                FoundItem(it.title, it.artist, it.album, it.durationSec)
            }

        override fun findVideos(query: String, limit: Int): List<FoundItem> =
            LocalMedia.pageVideos(ctx, PageRequest(null, 0, limit), query).items.map {
                FoundItem(it.name, durationSec = it.durationSec)
            }

        override fun findBooks(query: String, limit: Int): List<FoundItem> {
            val q = query.trim().lowercase()
            return savedBooks().filter { b ->
                q.isEmpty() || b.title.lowercase().contains(q) || b.by?.lowercase()?.contains(q) == true
            }.take(limit)
        }
    }

    override fun permission(p: PhonePermission): PermissionState = when {
        // The first is the one that matters: fine for the Wi-Fi name (coarse
        // alone doesn't name it), coarse for "where am I".
        granted(androidPermissions(p).first()) -> PermissionState.GRANTED
        p.name in settings.declinedPermissions -> PermissionState.DECLINED
        else -> PermissionState.ASKABLE
    }

    override fun declined(p: PhonePermission) {
        settings.declinedPermissions = settings.declinedPermissions + p.name
    }

    override fun canHandOff(kind: HandOffKind): Boolean = HandOffLauncher.available(ctx, kind)

    private fun granted(permission: String): Boolean =
        ContextCompat.checkSelfPermission(ctx, permission) == PackageManager.PERMISSION_GRANTED

    companion object {
        /** What Android's dialog asks for, per permission; the first is the
         *  one the feature needs. */
        fun androidPermissions(p: PhonePermission): List<String> = when (p) {
            // Approximate only for "where am I".
            PhonePermission.LOCATION -> listOf(Manifest.permission.ACCESS_COARSE_LOCATION)
            // Android 12+ asks for fine together with coarse; the person may
            // still pick approximate, which leaves the Wi-Fi name unread.
            PhonePermission.PRECISE_LOCATION -> listOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION)
            PhonePermission.CONTACTS -> listOf(Manifest.permission.READ_CONTACTS)
            PhonePermission.CALENDAR -> listOf(Manifest.permission.READ_CALENDAR)
        }
    }
}
