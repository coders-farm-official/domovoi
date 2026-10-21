package com.domovoi.app.assistant

import java.util.concurrent.CopyOnWriteArrayList

/** A phone's readings set by the test; every read is counted. */
class FakeReader(
    var battery: BatteryReading? = BatteryReading(82, charging = true),
    var storage: StorageReading? = StorageReading(23_400_000_000, 128_000_000_000),
    var network: NetworkReading? = NetworkReading(NetworkKind.WIFI),
    var alarm: AlarmReading? = null,
    var dnd: DndMode? = DndMode.OFF,
    var ringer: RingerMode? = RingerMode.NORMAL,
    var device: DeviceReading? = DeviceReading("Google", "Pixel 7", "15", 35, 7_700_000_000),
    var wifi: String? = "HomeNet",
    var location: LocationReading? = LocationReading(47.6062, -122.3321, 20f, 60_000),
    var headphones: List<String>? = emptyList(),
    var events: List<CalendarEvent>? = emptyList(),
    var people: List<ContactMatch>? = emptyList(),
) : PhoneReader {
    val reads = CopyOnWriteArrayList<String>()
    override fun battery() = battery.also { reads += "battery" }
    override fun storage() = storage.also { reads += "storage" }
    override fun network() = network.also { reads += "network" }
    override fun nextAlarm() = alarm.also { reads += "alarm" }
    override fun doNotDisturb() = dnd.also { reads += "dnd" }
    override fun ringer() = ringer.also { reads += "ringer" }
    override fun device() = device.also { reads += "device" }
    override fun wifiName() = wifi.also { reads += "wifi" }
    override fun location() = location.also { reads += "location" }
    override fun headphones() = headphones.also { reads += "headphones" }
    override fun calendar(fromMs: Long, toMs: Long) = events?.filter { it.beginMs in fromMs until toMs || (it.endMs ?: it.beginMs) in (fromMs + 1)..toMs }
        .also { reads += "calendar" }
    override fun contacts(name: String) = people?.filter { it.name.contains(name, ignoreCase = true) }.also { reads += "contacts" }
}

class FakeLibrary(
    var music: List<FoundItem> = emptyList(),
    var videos: List<FoundItem> = emptyList(),
    var books: List<FoundItem> = emptyList(),
    var musicAllowed: Boolean = true,
    var videosAllowed: Boolean = true,
) : PhoneLibrary {
    val queries = CopyOnWriteArrayList<String>()
    override fun canFindMusic() = musicAllowed
    override fun canFindVideos() = videosAllowed
    override fun canFindBooks() = books.isNotEmpty()
    override fun findMusic(query: String, limit: Int) = music.filter { query.isBlank() || it.title.contains(query, true) || it.by?.contains(query, true) == true }
        .take(limit).also { queries += "music:$query" }
    override fun findVideos(query: String, limit: Int) = videos.filter { it.title.contains(query, true) }.take(limit).also { queries += "video:$query" }
    override fun findBooks(query: String, limit: Int) = books.filter { query.isBlank() || it.title.contains(query, true) }.take(limit).also { queries += "book:$query" }
}

/** A [PhoneContext] the test sets: facts, timers, permissions, hand-offs. */
class FakePhone(
    var facts: PromptFacts = PromptFacts.NONE,
    var timers: List<PhoneTimer> = emptyList(),
    var serverSet: Boolean = false,
    override val reader: FakeReader = FakeReader(),
    override val library: FakeLibrary = FakeLibrary(),
    val permissions: MutableMap<PhonePermission, PermissionState> =
        PhonePermission.entries.associateWith { PermissionState.ASKABLE }.toMutableMap(),
    var handOffs: Set<HandOffKind> = HandOffKind.entries.toSet(),
) : PhoneContext {
    val declinedList = CopyOnWriteArrayList<PhonePermission>()
    override fun facts() = facts
    override suspend fun timers(nowMs: Long) = timers.filter { it.triggerAtMs > nowMs }
    override fun serverKnown() = serverSet
    override fun permission(p: PhonePermission) = permissions.getValue(p)
    override fun declined(p: PhonePermission) {
        declinedList += p
        permissions[p] = PermissionState.DECLINED
    }
    override fun canHandOff(kind: HandOffKind) = kind in handOffs
}
