package com.domovoi.app.net

/**
 * While this phone is a room (roommode/), the household side is shut: the
 * household device token goes on NO request ([DeviceAuthInterceptor] and
 * [ApiClient.tokenForDownload] ask here first), so a phone that is also a
 * household device keeps its token sealed and unused until the person
 * leaves room mode with the phone's unlock.
 *
 * Process-wide and set early: DomovoiApplication.onCreate reads it from the
 * room's store before anything can make a request, and the room's store
 * sets it the moment room mode starts or ends (RoomStore.setActive).
 */
object HouseholdLock {
    @Volatile
    var roomModeActive: Boolean = false
}
