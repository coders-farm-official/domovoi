package com.domovoi.app.listens

/**
 * "Listening as" (Settings → Connection), the web's SpokenAudio rule
 * (web/static/player.jsx `getPerson` / `setPerson`), kept per server:
 *
 *  * nothing picked: the phone follows whose it is (the server's answer,
 *    "this is me"), unless it is a shared screen — then the household;
 *  * a person picked: that person, until changed;
 *  * "household" picked on a phone that IS someone's: the household on
 *    purpose (a claimed phone lent to a guest), remembered as [HOUSEHOLD];
 *  * picking whose the phone is again, or "household" on a phone that is
 *    nobody's, goes back to following the phone.
 *
 * Pure: Prefs stores what [toStore] says and shows what [effective] says.
 */
object ListenerPicks {
    /** The stored pick for "the household, on purpose". */
    const val HOUSEHOLD = "none"

    /** Who the phone listens as: a person id, or null for the household. */
    fun effective(pick: String?, owner: Long?, shared: Boolean): String? = when {
        pick == HOUSEHOLD -> null
        pick != null -> pick
        shared -> null
        else -> owner?.toString()
    }

    /** What to store when [personId] (null: household) is picked; null
     *  means "follow the phone" (nothing stored). */
    fun toStore(personId: String?, owner: Long?, shared: Boolean): String? {
        val device = if (shared) null else owner
        return when {
            personId == null -> if (device == null) null else HOUSEHOLD
            personId == device?.toString() -> null
            else -> personId
        }
    }
}
