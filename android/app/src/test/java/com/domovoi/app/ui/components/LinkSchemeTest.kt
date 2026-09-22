package com.domovoi.app.ui.components

import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Links that come from feed or server data open externally only when they
 * are http(s) (FE-1). The News story title and the now-playing source pill
 * both go through [openWebLink].
 */
class LinkSchemeTest {

    private val webLinks = listOf(
        "https://news.example/story/1",
        "http://news.example/story/1",
        "HTTPS://News.Example/Story",
        "Http://news.example/mixed-case",
        "https://192.168.0.117:6369/#news",
    )

    private val otherLinks = listOf(
        "javascript:void(0)",
        "JavaScript:void(0)",
        "data:text/html;base64,AAAA",
        "intent://scan/#Intent;scheme=zxing;end",
        "tel:+15551234567",
        "sms:+15551234567",
        "file:///etc/hostname",
        "content://com.android.contacts/contacts",
        "ftp://news.example/story",
        "mailto:desk@news.example",
        "market://details?id=com.example",
        "//news.example/protocol-relative",
        "/relative/path",
        "http:no-authority",
        "https://",
        "http://",
        "",
        "   ",
    )

    // ---- webLinkOrNull / isWebLink ------------------------------------------

    @Test fun httpAndHttpsLinksAreKept() {
        for (link in webLinks) {
            assertEquals(link, webLinkOrNull(link))
            assertTrue(link, isWebLink(link))
        }
    }

    @Test fun surroundingWhitespaceIsTrimmed() {
        assertEquals("https://news.example/x", webLinkOrNull("  https://news.example/x \n"))
    }

    @Test fun everyOtherSchemeIsNull() {
        for (link in otherLinks) {
            assertNull(link, webLinkOrNull(link))
            assertFalse(link, isWebLink(link))
        }
        assertNull(webLinkOrNull(null))
        assertFalse(isWebLink(null))
    }

    // ---- openWebLink --------------------------------------------------------

    /** Records what the app hands to startActivity; the stubbed android.jar
     *  makes the base ContextWrapper methods no-ops, so this is enough. */
    private class RecordingContext : ContextWrapper(null) {
        val launched = mutableListOf<Intent?>()
        override fun startActivity(intent: Intent?) { launched += intent }
    }

    @Test fun openWebLinkStartsAnActivityForAWebLink() {
        val ctx = RecordingContext()
        assertTrue(openWebLink(ctx, "https://news.example/story/1"))
        assertEquals(1, ctx.launched.size)
    }

    @Test fun openWebLinkDoesNothingForAnyOtherScheme() {
        val ctx = RecordingContext()
        for (link in otherLinks) assertFalse(link, openWebLink(ctx, link))
        assertFalse(openWebLink(ctx, null))
        assertTrue(ctx.launched.isEmpty())
    }

    @Test fun openWebLinkReportsFalseWhenNoActivityTakesTheIntent() {
        val ctx = object : ContextWrapper(null) {
            override fun startActivity(intent: Intent?) {
                throw android.content.ActivityNotFoundException("no browser")
            }
        }
        assertFalse(openWebLink(ctx as Context, "https://news.example/story/1"))
    }
}
