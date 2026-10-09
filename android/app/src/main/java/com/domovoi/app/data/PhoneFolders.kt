package com.domovoi.app.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json

/**
 * A folder the person added to the phone's Files tab through Android's folder
 * picker. [uri] is the tree URI the picker returned (a persisted read+write
 * grant is held on it); [name] is what the picker called it, shown as the
 * library's label.
 */
@Serializable
data class PhoneFolder(val uri: String, val name: String)

/**
 * The list logic behind Prefs' `phone_folders` value, kept free of Android so
 * it is unit-tested on the JVM.
 *
 * The list is what the person chose; Android's own list of persisted grants
 * is what the app may still read. A folder whose grant is gone (revoked in
 * system settings, the app's data cleared, the folder's volume removed) drops
 * out through [stillGranted]. Taking a folder off the list never touches the
 * files in it.
 */
object PhoneFolders {
    private val json = Json { ignoreUnknownKeys = true }
    private val serializer = ListSerializer(PhoneFolder.serializer())

    fun decode(raw: String?): List<PhoneFolder> =
        if (raw.isNullOrBlank()) emptyList()
        else runCatching { json.decodeFromString(serializer, raw) }.getOrDefault(emptyList())
            .filter { it.uri.isNotBlank() }
            .distinctBy { it.uri }

    fun encode(list: List<PhoneFolder>): String = json.encodeToString(serializer, list)

    /** Add [folder] at the end, or rename it in place when it is already listed. */
    fun withFolder(list: List<PhoneFolder>, folder: PhoneFolder): List<PhoneFolder> =
        if (list.any { it.uri == folder.uri }) list.map { if (it.uri == folder.uri) folder else it }
        else list + folder

    fun without(list: List<PhoneFolder>, uri: String): List<PhoneFolder> = list.filter { it.uri != uri }

    /** Only the folders Android still holds a persisted read grant for. */
    fun stillGranted(list: List<PhoneFolder>, grantedReadUris: Set<String>): List<PhoneFolder> =
        list.filter { it.uri in grantedReadUris }

    /** The Files library id for a folder, and back. */
    const val FOLDER_PREFIX = "phone:folder:"
    const val PHOTOS_ID = "phone:photos"

    fun libraryId(folder: PhoneFolder): String = FOLDER_PREFIX + folder.uri

    fun uriOf(libraryId: String): String? =
        if (libraryId.startsWith(FOLDER_PREFIX)) libraryId.removePrefix(FOLDER_PREFIX) else null
}
