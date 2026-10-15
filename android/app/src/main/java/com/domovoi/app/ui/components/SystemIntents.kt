package com.domovoi.app.ui.components

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings

/**
 * Opens this app's page in Android's settings, where a permission refused
 * for good ("don't ask again", or refused twice) is turned back on: Android
 * shows no permission dialog for it any more. False when nothing could open
 * it.
 */
fun openAppSettings(context: Context): Boolean = runCatching {
    context.startActivity(
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
    )
}.isSuccess

/**
 * Android's share menu with [text] as plain text (and [subject], which an
 * email app uses as the subject). False when no app on the phone takes it.
 */
fun shareText(context: Context, subject: String, text: String): Boolean = runCatching {
    val send = Intent(Intent.ACTION_SEND)
        .setType("text/plain")
        .putExtra(Intent.EXTRA_SUBJECT, subject)
        .putExtra(Intent.EXTRA_TEXT, text)
    context.startActivity(Intent.createChooser(send, subject).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
}.isSuccess
