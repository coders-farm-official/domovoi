package com.domovoi.app.ui.screens.documents

import android.annotation.SuppressLint
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebStorage
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.domovoi.app.AppContainer
import com.domovoi.app.net.CleartextPolicy
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.runBlocking
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import java.io.ByteArrayInputStream

/**
 * The server's Documents spreadsheets (.xlsx / .csv) open in the SAME editor
 * as the dashboard's (web/static/sheet_editor.jsx, Univer), on its own page
 * `sheet.html#path=…`, in a WebView: formulas, formatting, several sheets,
 * autosave, the "someone else is editing" notice and the stale-save choice
 * all come with it. A phone folder's sheets (no server) keep the plain grid
 * editor ([SheetEditorOverlay]).
 *
 * The household token is the one thing this hands over, and it is handed
 * over the way every other request of the app gets it:
 *  - only after the active server has proved itself on this network
 *    ([com.domovoi.app.net.ApiClient.tokenForDownload]: the identity gate,
 *    the refused/throttled rules, room mode);
 *  - only to that server's own origin: the WebView may not navigate
 *    anywhere else, and every request it makes is checked again — not the
 *    active server, plain http where [CleartextPolicy] refuses it, or a
 *    server that no longer proves itself (the network changed under an
 *    open sheet) is answered here with a refusal and never leaves the phone;
 *  - and not kept: the page's storage is wiped when the editor closes.
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
internal fun SheetWebEditor(app: AppContainer, relPath: String, onClose: () -> Unit) {
    val dark = Domovoi.colors.isDark
    val closeNow by rememberUpdatedState(onClose)
    var handoff by remember { mutableStateOf<SheetHandoff?>(null) }
    var failure by remember { mutableStateOf<String?>(null) }
    var webView by remember { mutableStateOf<WebView?>(null) }

    LaunchedEffect(relPath) {
        val base = app.api.baseUrl.trimEnd('/').toHttpUrlOrNull()
        if (base == null) {
            failure = "No server is set up on this phone."
            return@LaunchedEffect
        }
        val page = base.newBuilder().encodedPath("/sheet.html")
            .encodedFragment("path=" + Uri.encode(relPath, "/")).build()
        val token = runCatching { app.api.tokenForDownload(page) }
        token.onSuccess { t ->
            if (t == null) failure = "This phone isn't paired with the home server, so it can't open sheets."
            else handoff = SheetHandoff(base, page, t)
        }.onFailure {
            failure = "Couldn't reach the home server safely: ${it.message ?: "not verified"}"
        }
    }

    // Back is the editor's Close: it saves what's pending, then asks the
    // app to close (DomovoiAppBridge.close).
    BackHandler {
        val wv = webView
        if (wv != null && handoff != null) {
            wv.evaluateJavascript("window.dispatchEvent(new Event('domovoi-sheet-close'))", null)
        } else {
            closeNow()
        }
    }

    DisposableEffect(Unit) {
        onDispose {
            webView?.let { wv ->
                wv.stopLoading()
                wv.removeJavascriptInterface(SheetBridge.NAME)
                wv.destroy()
            }
            // The token went into the page's storage; it doesn't stay there.
            WebStorage.getInstance().deleteAllData()
        }
    }

    Box(Modifier.fillMaxSize().background(Domovoi.colors.canvas)) {
        val h = handoff
        when {
            failure != null -> Column(
                Modifier.padding(24.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Text(failure!!, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted)
                OutlinedButton(onClick = { closeNow() }) { Text("close") }
            }
            h == null -> LoadingState()
            else -> AndroidView(
                modifier = Modifier.fillMaxSize(),
                factory = { ctx ->
                    WebView(ctx).apply {
                        settings.javaScriptEnabled = true
                        settings.domStorageEnabled = true
                        settings.allowFileAccess = false
                        settings.allowContentAccess = false
                        settings.setSupportMultipleWindows(false)
                        settings.javaScriptCanOpenWindowsAutomatically = false
                        settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
                        // It only ever loads the household's own server; Safe
                        // Browsing would send the addresses it loads to Google.
                        settings.safeBrowsingEnabled = false
                        setBackgroundColor(android.graphics.Color.TRANSPARENT)
                        webViewClient = SheetWebClient(app, h.base)
                        addJavascriptInterface(
                            SheetBridge(h.token, app.prefs.deviceId, dark) { closeNow() },
                            SheetBridge.NAME,
                        )
                        loadUrl(h.page.toString())
                        webView = this
                    }
                },
            )
        }
    }
}

private data class SheetHandoff(val base: HttpUrl, val page: HttpUrl, val token: String)

/** Keeps the WebView on the active server and re-checks every request. */
private class SheetWebClient(private val app: AppContainer, private val base: HttpUrl) : WebViewClient() {

    private fun sameServer(url: HttpUrl): Boolean =
        url.scheme == base.scheme && url.host.equals(base.host, ignoreCase = true) && url.port == base.port

    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
        val url = request.url.toString().toHttpUrlOrNull() ?: return true
        // Downloads (the csv/xlsx buttons) and the page itself stay on the
        // server; anything else is refused rather than opened in here.
        return !sameServer(url)
    }

    override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
        val url = request.url.toString().toHttpUrlOrNull() ?: return refused()
        if (!sameServer(url) || !CleartextPolicy.permits(url)) return refused()
        // Off the main thread already; the gate's proof is cached per network.
        val admitted = runCatching { runBlocking { app.api.tokenForDownload(url) } }.getOrNull()
        return if (admitted == null) refused() else null
    }

    private fun refused(): WebResourceResponse =
        WebResourceResponse("text/plain", "utf-8", 403, "Forbidden", emptyMap(), ByteArrayInputStream(ByteArray(0)))
}

/** What sheet.html reads before auth.js runs (window.DomovoiAppBridge).
 *  The token is handed over ONCE: sheet.html takes it on load, and nothing
 *  that runs later in the page (or a page it is navigated to) can ask again. */
private class SheetBridge(
    token: String,
    private val deviceId: String?,
    private val dark: Boolean,
    private val onClose: () -> Unit,
) {
    private val main = Handler(Looper.getMainLooper())
    private val pending = java.util.concurrent.atomic.AtomicReference<String?>(token)

    @JavascriptInterface fun deviceToken(): String = pending.getAndSet(null).orEmpty()
    @JavascriptInterface fun deviceId(): String = deviceId.orEmpty()
    @JavascriptInterface fun darkTheme(): Boolean = dark
    @JavascriptInterface fun close() { main.post { onClose() } }

    companion object {
        const val NAME = "DomovoiAppBridge"
    }
}
