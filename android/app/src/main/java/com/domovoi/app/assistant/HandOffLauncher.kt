package com.domovoi.app.assistant

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.CalendarContract
import android.provider.ContactsContract
import androidx.activity.result.contract.ActivityResultContract

/**
 * The hand-offs' intents (HandOffs.kt): each opens another app with the
 * work filled in, and that app is where the person finishes it. None needs
 * a permission: the dialer (ACTION_DIAL, never CALL), Messages (SENDTO
 * smsto:), an email draft (SENDTO mailto:), a maps app (geo:), the calendar
 * app's own editor (ACTION_INSERT), a browser (VIEW https), the share sheet.
 *
 * [available] asks Android whether any app takes each one (the manifest's
 * `<queries>` makes them visible from API 30); a hand-off nothing takes is
 * never offered.
 */
object HandOffLauncher {

    /** The intent for [h]; null when it lacks what it needs (a call with no
     *  number yet: the contact picker comes first). */
    fun intent(h: HandOff): Intent? = when (h.kind) {
        HandOffKind.CALL -> h.number?.takeIf { it.isNotBlank() }?.let { Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + Uri.encode(it))) }
        HandOffKind.TEXT -> h.number?.takeIf { it.isNotBlank() }?.let { n -> Intent(Intent.ACTION_SENDTO, smsUri(n, h.body)).apply {
            // Google Messages reads the words from the link only; others
            // from the extra.
            h.body?.let { putExtra("sms_body", it) }
        } }
        HandOffKind.EMAIL -> Intent(Intent.ACTION_SENDTO, Uri.parse("mailto:" + (h.email?.let { Uri.encode(it, "@") } ?: ""))).apply {
            h.subject?.let { putExtra(Intent.EXTRA_SUBJECT, it) }
            h.body?.let { putExtra(Intent.EXTRA_TEXT, it) }
        }
        HandOffKind.DIRECTIONS -> h.place?.let { Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q=" + Uri.encode(it))) }
        HandOffKind.EVENT -> Intent(Intent.ACTION_INSERT, CalendarContract.Events.CONTENT_URI).apply {
            h.title?.let { putExtra(CalendarContract.Events.TITLE, it) }
            h.beginMs?.let { putExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, it) }
            h.endMs?.let { putExtra(CalendarContract.EXTRA_EVENT_END_TIME, it) }
            if (h.allDay) putExtra(CalendarContract.EXTRA_EVENT_ALL_DAY, true)
        }
        HandOffKind.LINK -> h.url?.takeIf { HandOffs.safeLink(it) }?.let { Intent(Intent.ACTION_VIEW, Uri.parse(it)).addCategory(Intent.CATEGORY_BROWSABLE) }
        HandOffKind.SHARE -> h.body?.let { text ->
            Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text), null)
        }
        HandOffKind.OTHER -> null
    }

    /** "smsto:5550123?body=On%20my%20way". */
    internal fun smsUri(number: String, body: String?): Uri = Uri.parse(HandOffs.smsLink(number, body))

    /** A stand-in for each kind, filled enough for Android to say which apps
     *  take it. */
    private fun probe(kind: HandOffKind): Intent? = when (kind) {
        HandOffKind.CALL -> Intent(Intent.ACTION_DIAL, Uri.parse("tel:5550100"))
        HandOffKind.TEXT -> Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:5550100"))
        HandOffKind.EMAIL -> Intent(Intent.ACTION_SENDTO, Uri.parse("mailto:"))
        HandOffKind.DIRECTIONS -> Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q=library"))
        HandOffKind.EVENT -> Intent(Intent.ACTION_INSERT, CalendarContract.Events.CONTENT_URI)
        HandOffKind.LINK -> Intent(Intent.ACTION_VIEW, Uri.parse("https://example.org")).addCategory(Intent.CATEGORY_BROWSABLE)
        HandOffKind.SHARE -> Intent(Intent.ACTION_SEND).setType("text/plain")
        HandOffKind.OTHER -> null
    }

    /** Some app on the phone takes [kind]. */
    fun available(context: Context, kind: HandOffKind): Boolean = runCatching {
        val probe = probe(kind) ?: return false
        context.packageManager.resolveActivity(probe, PackageManager.MATCH_DEFAULT_ONLY) != null
    }.getOrDefault(false)

    /** The contacts app's picker is there (for a call or text by name). */
    fun canPickContact(context: Context): Boolean = runCatching {
        context.packageManager.resolveActivity(PickPhone().createIntent(context, Unit), PackageManager.MATCH_DEFAULT_ONLY) != null
    }.getOrDefault(false)

    /** Opens [h]'s app; false when it could not be opened. */
    fun launch(context: Context, h: HandOff): Boolean {
        val intent = intent(h) ?: return false
        if (context !is Activity) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try {
            context.startActivity(intent)
            true
        } catch (_: ActivityNotFoundException) {
            false
        } catch (_: SecurityException) {
            false
        }
    }

    /**
     * The contacts app's own picker, for one phone number: the person picks,
     * and Android lets the app read just that row (no contacts permission).
     * Gives the name and number, or null when nothing was picked.
     */
    class PickPhone : ActivityResultContract<Unit, ContactMatch?>() {
        private var resolver: android.content.ContentResolver? = null

        override fun createIntent(context: Context, input: Unit): Intent {
            resolver = context.contentResolver
            return Intent(Intent.ACTION_PICK, ContactsContract.CommonDataKinds.Phone.CONTENT_URI)
        }

        override fun parseResult(resultCode: Int, intent: Intent?): ContactMatch? {
            val uri = intent?.data?.takeIf { resultCode == Activity.RESULT_OK } ?: return null
            val cr = resolver ?: return null
            return runCatching {
                cr.query(
                    uri,
                    arrayOf(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER),
                    null, null, null,
                )?.use { c -> if (c.moveToFirst()) ContactMatch(c.getString(0) ?: "", c.getString(1), null) else null }
            }.getOrNull()
        }
    }
}
