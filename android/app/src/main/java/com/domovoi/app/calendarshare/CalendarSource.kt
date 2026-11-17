package com.domovoi.app.calendarshare

/*
 * Where a phone calendar comes from, said in plain words under its name in
 * Settings → Household → Phone calendars. Read from
 * CalendarContract.Calendars.ACCOUNT_TYPE only (which kind of account, e.g.
 * "com.google"): never ACCOUNT_NAME, the person's address. Display only:
 * nothing here is sent to the house.
 *
 * Why: Samsung phones carry an empty local "My calendar" next to the
 * Google one, and a Google calendar named after its address shows as
 * "Main calendar", so the two were easy to mix up.
 *
 * Pure (no Android classes), so the JVM tests cover it.
 */

/** The kinds of account a calendar can belong to, in the order the picker
 *  lists them. */
enum class CalendarSource(val label: String) {
    Google("Google account"),
    Samsung("Samsung account"),
    Microsoft("Microsoft / Outlook"),
    Other("Other account"),
    PhoneOnly("On this phone only"),
    ;

    /** Lower sorts first: Google and Samsung accounts share the top group. */
    val group: Int get() = when (this) {
        Google, Samsung -> 0
        Microsoft -> 1
        Other -> 2
        PhoneOnly -> 3
    }

    companion object {
        /** CalendarContract.ACCOUNT_TYPE_LOCAL (kept here so the pure code
         *  needs no Android class). */
        const val ACCOUNT_TYPE_LOCAL = "LOCAL"

        /**
         * ACCOUNT_TYPE → where it comes from.
         *
         * - "com.google" → Google.
         * - "com.osp.app.signin" (a Samsung account) → Samsung.
         * - "com.microsoft.*" (Outlook) or any Exchange type
         *   ("com.android.exchange", "com.google.android.gm.exchange",
         *   "com.samsung.android.exchange") → Microsoft.
         * - Local, on this phone only: ACCOUNT_TYPE_LOCAL ("LOCAL", any
         *   case), "com.android.local*", "com.samsung.android.local*", or
         *   any type ending ".local". Samsung's "My calendar" is a local
         *   calendar that is not synced anywhere; current Samsung Calendar
         *   builds are reported to file it under "LOCAL", older or other
         *   builds under "com.android.local" or
         *   "com.samsung.android.local", so all are matched. Not checked
         *   on a Samsung phone yet.
         * - Anything else, a blank type included → Other.
         */
        fun of(accountType: String?): CalendarSource {
            val t = accountType?.trim()?.lowercase().orEmpty()
            return when {
                t.isEmpty() -> Other
                t == "com.google" -> Google
                t == "com.osp.app.signin" -> Samsung
                t.startsWith("com.microsoft") || "exchange" in t -> Microsoft
                t == ACCOUNT_TYPE_LOCAL.lowercase() || t.startsWith("com.android.local") ||
                    t.startsWith("com.samsung.android.local") || t.endsWith(".local") -> PhoneOnly
                else -> Other
            }
        }

        /** The picker's order: Google and Samsung accounts, then Microsoft,
         *  then other accounts, then this phone only; the order the reader
         *  gave is kept within each group (a stable sort). */
        fun sorted(calendars: List<PhoneCalendar>): List<PhoneCalendar> =
            calendars.sortedBy { of(it.accountType).group }
    }
}
