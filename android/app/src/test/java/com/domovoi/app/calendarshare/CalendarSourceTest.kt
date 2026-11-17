package com.domovoi.app.calendarshare

import com.domovoi.app.ui.screens.settings.phoneCalendarLabel
import org.junit.Assert.assertEquals
import org.junit.Test

/** Where a phone calendar comes from, as the picker says it, and the
 *  picker's order. */
class CalendarSourceTest {

    @Test fun eachAccountTypeHasItsPlainLabel() {
        val cases = mapOf(
            "com.google" to "Google account",
            "com.osp.app.signin" to "Samsung account",
            "com.microsoft.office.outlook.USER_ACCOUNT" to "Microsoft / Outlook",
            "com.microsoft.exchange" to "Microsoft / Outlook",
            "com.android.exchange" to "Microsoft / Outlook",
            "com.google.android.gm.exchange" to "Microsoft / Outlook",
            "com.samsung.android.exchange" to "Microsoft / Outlook",
            "LOCAL" to "On this phone only",
            "local" to "On this phone only",
            "com.android.local" to "On this phone only",
            "com.android.localphone" to "On this phone only",
            "com.samsung.android.local" to "On this phone only",
            "com.samsung.android.calendar.local" to "On this phone only",
            "org.dmfs.caldav.account" to "Other account",
            "at.bitfire.davdroid" to "Other account",
            "com.google.something" to "Other account",
        )
        cases.forEach { (type, label) -> assertEquals(type, label, CalendarSource.of(type).label) }
    }

    @Test fun noTypeIsAnOtherAccount() {
        assertEquals(CalendarSource.Other, CalendarSource.of(null))
        assertEquals(CalendarSource.Other, CalendarSource.of(""))
        assertEquals(CalendarSource.Other, CalendarSource.of("   "))
        assertEquals(CalendarSource.Google, CalendarSource.of(" com.google "))
    }

    @Test fun googleAndSamsungFirstThenMicrosoftThenOtherThenThisPhone() {
        val cals = listOf(
            PhoneCalendar(1, "My calendar", "LOCAL"),
            PhoneCalendar(2, "Work", "com.microsoft.office.outlook.USER_ACCOUNT"),
            PhoneCalendar(3, "kim@example.org", "com.google"),
            PhoneCalendar(4, "Club", "at.bitfire.davdroid"),
            PhoneCalendar(5, "Samsung calendar", "com.osp.app.signin"),
            PhoneCalendar(6, "Birthdays", "com.google"),
            PhoneCalendar(7, "Phone", "com.android.local"),
            PhoneCalendar(8, "Mystery", null),
            PhoneCalendar(9, "Exchange", "com.android.exchange"),
        )
        // Within a group the reader's own order stays.
        assertEquals(listOf(3L, 5L, 6L, 2L, 9L, 4L, 8L, 1L, 7L), CalendarSource.sorted(cals).map { it.id })
        assertEquals(emptyList<PhoneCalendar>(), CalendarSource.sorted(emptyList()))
    }

    /** The owner's mix-up: Samsung's empty local calendar and a Google one
     *  named after its address. Now each says where it comes from, and the
     *  address never shows. */
    @Test fun thePickerSaysWhereEachCalendarComesFrom() {
        assertEquals(
            "Main calendar" to "Google account · 14 events",
            phoneCalendarLabel(PhoneCalendar(3, "kim@example.org", "com.google"), 14),
        )
        assertEquals(
            "My calendar" to "On this phone only · no events",
            phoneCalendarLabel(PhoneCalendar(1, "My calendar", "LOCAL"), 0),
        )
        assertEquals("Work" to "Microsoft / Outlook · 1 event",
            phoneCalendarLabel(PhoneCalendar(2, "Work", "com.android.exchange"), 1))
        // Counts unknown: the source alone.
        assertEquals("Club" to "Other account", phoneCalendarLabel(PhoneCalendar(4, "Club", null)))
    }
}
