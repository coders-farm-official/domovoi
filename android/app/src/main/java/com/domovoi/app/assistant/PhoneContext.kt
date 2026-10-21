package com.domovoi.app.assistant

// What a phone chat may read about the phone (N1, part 1 and the owner's
// "Phone data and hand-offs" list of 2026-10-21). Everything here is read on
// the phone, at the moment it is asked; nothing comes from the home server
// at answer time (each place answers from its own world). The Android
// readings are AndroidPhoneReader's; the tests give fakes.

data class BatteryReading(val percent: Int, val charging: Boolean, val full: Boolean = false)

data class StorageReading(val freeBytes: Long, val totalBytes: Long)

enum class NetworkKind { WIFI, MOBILE, ETHERNET, OTHER, NONE }

data class NetworkReading(val kind: NetworkKind, val airplaneMode: Boolean = false)

/** The phone's next alarm (AlarmManager.getNextAlarmClock): the Clock
 *  app's, or any app's that set one the system shows. */
data class AlarmReading(val triggerAtMs: Long)

enum class DndMode { OFF, PRIORITY, ALARMS, SILENT, UNKNOWN }

data class DeviceReading(
    val manufacturer: String,
    val model: String,
    val android: String,
    val api: Int,
    val totalRamBytes: Long,
)

data class LocationReading(val latitude: Double, val longitude: Double, val accuracyM: Float?, val ageMs: Long?)

data class CalendarEvent(val title: String, val beginMs: Long, val endMs: Long?, val allDay: Boolean, val location: String? = null)

data class ContactMatch(val name: String, val number: String?, val email: String?, val label: String? = null)

/** A timer or reminder in the phone's own copy of the household's
 *  (alerts/TimerAlarmMirror.kt). [masked]: a private reminder, shown without
 *  its words. */
data class PhoneTimer(
    val label: String?,
    val reminder: Boolean,
    val triggerAtMs: Long,
    val room: String? = null,
    val masked: Boolean = false,
)

/** A song, video or book a read tool found on the phone. */
data class FoundItem(
    val title: String,
    val by: String? = null,
    val album: String? = null,
    val durationSec: Double? = null,
    /** A saved book: the chapter the listener is on, and where in it. */
    val chapter: String? = null,
    val positionSec: Double? = null,
)

/** The phone's own readings. Each returns null when it can't be read (or,
 *  for the permission-gated ones, when the permission isn't held). */
interface PhoneReader {
    fun battery(): BatteryReading? = null
    fun storage(): StorageReading? = null
    fun network(): NetworkReading? = null
    fun nextAlarm(): AlarmReading? = null
    fun doNotDisturb(): DndMode? = null
    fun device(): DeviceReading? = null

    /** Location permission. */
    fun wifiName(): String? = null

    /** Location permission. The phone's last known fix, never a fresh
     *  request and never an address lookup (that would go online). */
    fun location(): LocationReading? = null

    /** The Bluetooth headphones and speakers the phone plays to now, by
     *  name (Android's audio outputs: no permission); null when they can't
     *  be read. */
    fun headphones(): List<String>? = null

    /** Calendar permission. The phone's own calendars, [fromMs] to [toMs]. */
    fun calendar(fromMs: Long, toMs: Long): List<CalendarEvent>? = null

    /** Contacts permission. People whose name matches [name]. */
    fun contacts(name: String): List<ContactMatch>? = null
}

/** What the read tools search: the phone's music and videos (MediaStore,
 *  under the media permissions local Music and Videos already ask for) and
 *  the audiobooks saved on the phone. Null: not offered. */
interface PhoneLibrary {
    fun canFindMusic(): Boolean = false
    fun canFindVideos(): Boolean = false
    fun canFindBooks(): Boolean = false
    fun findMusic(query: String, limit: Int): List<FoundItem> = emptyList()
    fun findVideos(query: String, limit: Int): List<FoundItem> = emptyList()
    fun findBooks(query: String, limit: Int): List<FoundItem> = emptyList()
}

/** The optional permissions a phone chat asks for on first use. */
enum class PhonePermission {
    /** Where am I, the Wi-Fi network's name. */
    LOCATION,

    /** Calling and texting someone by name, without the picker. */
    CONTACTS,

    /** Reading the phone's own calendar. */
    CALENDAR,
}

/** Whether a permission can be asked for, is held, or was turned down. */
enum class PermissionState {
    GRANTED,

    /** Never asked: the chat asks once, with its reason. */
    ASKABLE,

    /** Turned down (in the chat or in Android): the feature says it can't,
     *  and the chat never asks again. */
    DECLINED,
}

/**
 * Everything the phone chat reads, handed to the controller by the app
 * (AppContainer builds the real one from the player, the connection, the
 * timer mirror and the phone's own readings). [NONE]: a phone that says
 * nothing about itself (tests of everything else).
 */
interface PhoneContext {
    /** The prompt's facts, read now from the app's own state. */
    fun facts(): PromptFacts = PromptFacts.NONE

    /** The phone's copy of the household's timers and reminders, soonest
     *  first, only the ones still to come. */
    suspend fun timers(nowMs: Long): List<PhoneTimer> = emptyList()

    /** A home server is set up on this phone (for the one honest line that
     *  points to home-server chats; nothing is fetched). */
    fun serverKnown(): Boolean = false

    val reader: PhoneReader get() = NoReadings
    val library: PhoneLibrary get() = NoLibrary

    fun permission(p: PhonePermission): PermissionState = PermissionState.DECLINED

    /** The person turned [p] down in the chat (or in Android's dialog). */
    fun declined(p: PhonePermission) {}

    /** Some app on the phone takes this hand-off (resolveActivity, with the
     *  manifest's `<queries>`). A hand-off no app takes is never offered. */
    fun canHandOff(kind: HandOffKind): Boolean = false

    object NoReadings : PhoneReader
    object NoLibrary : PhoneLibrary

    companion object {
        val NONE: PhoneContext = object : PhoneContext {}
    }
}

/** What the chat says when it asks for a permission on first use: one
 *  line, why, and that it stays on the phone. */
object PhonePermissions {
    fun reason(p: PhonePermission): String = when (p) {
        PhonePermission.LOCATION -> "To say where you are or name the Wi-Fi network, Domovoi needs the location permission. " +
            "It's read on this phone only and never sent anywhere."
        PhonePermission.CONTACTS -> "To call or text people by name, Domovoi needs to read your contacts. They're read on this " +
            "phone only and never sent anywhere. Without it, you choose the person from your contacts each time."
        PhonePermission.CALENDAR -> "To read this phone's calendar, Domovoi needs the calendar permission. It's read on this " +
            "phone only and never sent anywhere."
    }

    /** The card's title. */
    fun title(p: PhonePermission): String = when (p) {
        PhonePermission.LOCATION -> "Allow location?"
        PhonePermission.CONTACTS -> "Allow contacts?"
        PhonePermission.CALENDAR -> "Allow the calendar?"
    }

    /** The card's second button. */
    fun notNow(p: PhonePermission): String = if (p == PhonePermission.CONTACTS) "use the picker" else "not now"
}
