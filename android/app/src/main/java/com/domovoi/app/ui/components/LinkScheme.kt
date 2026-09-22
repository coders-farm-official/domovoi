package com.domovoi.app.ui.components

import android.content.Context
import android.content.Intent
import android.net.Uri

/**
 * Links that arrive from outside the app (feed articles, a provider's
 * now-playing source) are handed to the system browser only when they are
 * web links. The scheme check is pure Kotlin so it is unit-tested on the JVM;
 * [openWebLink] is the single place a data-supplied string becomes an
 * ACTION_VIEW.
 */

/** The trimmed link when its scheme is http or https (any case), else null. */
fun webLinkOrNull(url: String?): String? {
    val link = url?.trim().orEmpty()
    if (link.isEmpty()) return null
    val scheme = link.substringBefore("://", missingDelimiterValue = "").lowercase()
    if (scheme != "http" && scheme != "https") return null
    // Something must follow the scheme — "https://" alone is not a link.
    return link.takeIf { it.length > scheme.length + "://".length }
}

/** True when [url] is a link the app may open in a browser. */
fun isWebLink(url: String?): Boolean = webLinkOrNull(url) != null

/**
 * Open [url] in the system browser. Does nothing and returns false unless the
 * scheme is http or https; returns false too when no activity could take the
 * intent (that failure is swallowed, as before).
 */
fun openWebLink(context: Context, url: String?): Boolean {
    val link = webLinkOrNull(url) ?: return false
    return runCatching {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(link)))
    }.isSuccess
}
