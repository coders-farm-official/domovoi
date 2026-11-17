package com.domovoi.app.calendarshare

import android.Manifest
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.provider.CalendarContract
import androidx.core.content.ContextCompat
import java.io.IOException

/**
 * Reads the phone's own calendars (every account on it: Google, Microsoft,
 * Samsung, a local one) through CalendarContract, with READ_CALENDAR. Only
 * the columns the house needs; never a description, a guest or a reminder
 * (the access level and the person's own answer are read only to leave
 * things out: a private event goes as "busy", a declined one not at all).
 * The rows go to [SharePayload], which is where every rule lives.
 */
class PhoneCalendarReader(private val context: Context) {

    fun allowed(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CALENDAR) == PackageManager.PERMISSION_GRANTED

    /** The calendars on the phone the calendar app shows; null without the
     *  permission. A provider that fails is an [IOException] (tried again
     *  later), never an empty list, which would untick every calendar. */
    fun calendars(): List<PhoneCalendar>? {
        if (!allowed()) return null
        val proj = arrayOf(
            CalendarContract.Calendars._ID,
            CalendarContract.Calendars.CALENDAR_DISPLAY_NAME,
            // The kind of account only ("com.google"), never ACCOUNT_NAME
            // (the person's address): the picker says "Google account".
            CalendarContract.Calendars.ACCOUNT_TYPE,
            CalendarContract.Calendars.CALENDAR_COLOR,
        )
        val out = mutableListOf<PhoneCalendar>()
        return runCatching {
            context.contentResolver.query(
                CalendarContract.Calendars.CONTENT_URI, proj,
                "${CalendarContract.Calendars.VISIBLE} = 1", null,
                // Sorted by account in the provider (keeps one account's
                // calendars together); the address itself is never read.
                "${CalendarContract.Calendars.ACCOUNT_NAME} ASC, ${CalendarContract.Calendars.CALENDAR_DISPLAY_NAME} ASC",
            )?.use { c ->
                while (c.moveToNext()) {
                    out += PhoneCalendar(
                        id = c.getLong(0),
                        name = c.getString(1)?.ifBlank { null } ?: "Calendar",
                        accountType = c.getString(2)?.ifBlank { null },
                        color = if (c.isNull(3)) null else c.getInt(3),
                    )
                }
            }
            out.toList()
        }.getOrElse { if (it is SecurityException) null else throw IOException("calendar provider", it) }
    }

    /** How many occurrences each visible calendar has in [window] (the
     *  window the house is sent), by calendar id; a calendar with none is
     *  absent. Reads the CALENDAR_ID column only, of every visible
     *  calendar (ticked or not), so the picker can show the person what
     *  each holds; the counts stay on the phone and are never sent
     *  (android/play/DATA_SAFETY.md says so). Null without the
     *  permission or when the provider fails: the picker then shows no
     *  counts. Call it off the main thread. */
    fun counts(window: ShareWindow): Map<Long, Int>? {
        if (!allowed()) return null
        val uri = CalendarContract.Instances.CONTENT_URI.buildUpon().also {
            ContentUris.appendId(it, window.startMs)
            ContentUris.appendId(it, window.endMs)
        }.build()
        return runCatching {
            val out = HashMap<Long, Int>()
            context.contentResolver.query(
                uri, arrayOf(CalendarContract.Instances.CALENDAR_ID),
                "${CalendarContract.Instances.VISIBLE} = 1", null, null,
            )?.use { c ->
                while (c.moveToNext()) {
                    val id = c.getLong(0)
                    out[id] = (out[id] ?: 0) + 1
                }
            }
            out as Map<Long, Int>
        }.getOrNull()
    }

    /** Every occurrence in [window] for the calendars [ids] (repeating
     *  events expanded by the provider); null without the permission. */
    fun instances(window: ShareWindow, ids: Set<Long>): List<InstanceRow>? {
        if (!allowed()) return null
        if (ids.isEmpty()) return emptyList()
        val uri = CalendarContract.Instances.CONTENT_URI.buildUpon().also {
            ContentUris.appendId(it, window.startMs)
            ContentUris.appendId(it, window.endMs)
        }.build()
        val proj = arrayOf(
            CalendarContract.Instances.EVENT_ID,
            CalendarContract.Instances.CALENDAR_ID,
            CalendarContract.Instances.BEGIN,
            CalendarContract.Instances.END,
            CalendarContract.Instances.ALL_DAY,
            CalendarContract.Instances.TITLE,
            CalendarContract.Instances.EVENT_LOCATION,
            CalendarContract.Instances.STATUS,
            CalendarContract.Instances.ACCESS_LEVEL,
            CalendarContract.Instances.SELF_ATTENDEE_STATUS,
            CalendarContract.Instances.UID_2445,
        )
        val idList = ids.joinToString(",")
        val out = mutableListOf<InstanceRow>()
        return runCatching {
            context.contentResolver.query(
                uri, proj,
                "${CalendarContract.Instances.VISIBLE} = 1 AND ${CalendarContract.Instances.CALENDAR_ID} IN ($idList)",
                null, "${CalendarContract.Instances.BEGIN} ASC",
            )?.use { c ->
                while (c.moveToNext()) {
                    out += InstanceRow(
                        eventId = c.getLong(0),
                        calendarId = c.getLong(1),
                        begin = c.getLong(2),
                        end = if (c.isNull(3)) null else c.getLong(3),
                        allDay = !c.isNull(4) && c.getInt(4) == 1,
                        title = c.getString(5),
                        location = c.getString(6),
                        status = if (c.isNull(7)) null else c.getInt(7),
                        accessLevel = if (c.isNull(8)) null else c.getInt(8),
                        selfStatus = if (c.isNull(9)) null else c.getInt(9),
                        icalUid = c.getString(10)?.ifBlank { null },
                    )
                }
            }
            out.toList()
        }.getOrElse { if (it is SecurityException) null else throw IOException("calendar provider", it) }
    }
}
