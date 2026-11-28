package com.domovoi.app.away

import java.lang.ref.WeakReference
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Every [AwaySession] in the process, so the places that end the
 * household side at once (a server switch or forget, an unpair, the
 * server's identity changing, room mode: data/Prefs.kt, net/HouseholdLock.kt)
 * can stop away chat's link and its Tor with one call, as
 * net/AdminSession.kt's AdminSessions does for admin sign-ins.
 */
object AwayLinks {
    private val sessions = CopyOnWriteArrayList<WeakReference<AwaySession>>()

    fun register(s: AwaySession) {
        sessions.removeAll { it.get() == null }
        sessions += WeakReference(s)
    }

    fun stopAll() {
        for (ref in sessions) runCatching { ref.get()?.let { it.stopNow(); it.forgetHeld() } }
        sessions.removeAll { it.get() == null }
    }
}
