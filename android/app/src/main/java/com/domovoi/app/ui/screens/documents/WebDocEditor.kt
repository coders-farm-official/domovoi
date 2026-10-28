package com.domovoi.app.ui.screens.documents

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.CancellationSignal
import android.os.Handler
import android.os.Looper
import android.os.ParcelFileDescriptor
import android.print.PageRange
import android.print.PrintAttributes
import android.print.PrintDocumentAdapter
import android.print.PrintManager
import android.util.Base64
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebStorage
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import java.io.File

/**
 * The server's Documents documents (formatted documents, Markdown, Word)
 * open in the SAME editor as the dashboard's (web/static/doc_editor.jsx),
 * on its own page `doc.html?path=…`, in a WebView: fonts, pictures, tables,
 * co-editing and autosave all come with it. A phone folder's files (no
 * server) keep the on-phone text editor ([TextEditorOverlay]).
 *
 * Built like the spreadsheet host ([SheetWebEditor]), which it is meant to
 * merge with. The household token is the one thing this hands over, and
 * it is handed over the way every other request of the app gets it:
 *  - only after the active server has proved itself on this network
 *    ([com.domovoi.app.net.ApiClient.tokenForDownload]: the identity gate,
 *    the refused/throttled rules, room mode); a failed or empty check
 *    means the editor does not open at all;
 *  - only to that server's own origin: the WebView may not navigate
 *    anywhere else, and every request it makes is checked again: not the
 *    active server, plain http where [CleartextPolicy] refuses it, or a
 *    server that no longer proves itself (the network changed under an
 *    open document) is answered here with a refusal and never leaves the
 *    phone;
 *  - once (the bridge gives it a single time) and into the page's memory
 *    only (doc_page.jsx never stores it); and the page's storage is wiped
 *    when the editor closes all the same.
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
internal fun WebDocEditor(app: AppContainer, relPath: String, onClose: () -> Unit) {
    val dark = Domovoi.colors.isDark
    val closeNow by rememberUpdatedState(onClose)
    var handoff by remember { mutableStateOf<DocHandoff?>(null) }
    var failure by remember { mutableStateOf<String?>(null) }
    var webView by remember { mutableStateOf<WebView?>(null) }
    var pendingPick by remember { mutableStateOf<ValueCallback<Array<Uri>>?>(null) }

    LaunchedEffect(relPath) {
        val base = app.api.baseUrl.trimEnd('/').toHttpUrlOrNull()
        if (base == null) {
            failure = "No server is set up on this phone."
            return@LaunchedEffect
        }
        val page = base.newBuilder().encodedPath("/doc.html")
            .addQueryParameter("path", relPath).build()
        if (!CleartextPolicy.permits(page)) {
            failure = CleartextPolicy.refusalMessage(page.host)
            return@LaunchedEffect
        }
        val token = runCatching { app.api.tokenForDownload(page) }
        token.onSuccess { t ->
            if (t == null) failure = "This phone isn't paired with the home server, so it can't open documents."
            else handoff = DocHandoff(base, page, t)
        }.onFailure {
            failure = "Couldn't reach the home server safely: ${it.message ?: "not verified"}"
        }
    }

    val pickPictures = rememberLauncherForActivityResult(ActivityResultContracts.GetMultipleContents()) { uris ->
        pendingPick?.onReceiveValue(uris.toTypedArray())
        pendingPick = null
    }

    // Back is the editor's Close: it saves what's pending, then asks the
    // app to close (DocBridge.close).
    BackHandler {
        val wv = webView
        if (wv != null && handoff != null) {
            wv.evaluateJavascript(
                "(function(){if(window.DomovoiDocPage){window.DomovoiDocPage.back();return 1}return 0})()"
            ) { r -> if (r != "1") closeNow() }
        } else {
            closeNow()
        }
    }

    DisposableEffect(Unit) {
        onDispose {
            pendingPick?.onReceiveValue(null)
            webView?.let { wv ->
                wv.stopLoading()
                wv.removeJavascriptInterface(DocBridge.NAME)
                wv.destroy()
            }
            // Nothing the page kept stays behind.
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
                        lockDown(settings)
                        setBackgroundColor(android.graphics.Color.TRANSPARENT)
                        webViewClient = DocWebClient(app, h.base)
                        webChromeClient = object : WebChromeClient() {
                            override fun onShowFileChooser(
                                view: WebView,
                                callback: ValueCallback<Array<Uri>>,
                                params: FileChooserParams,
                            ): Boolean {
                                pendingPick?.onReceiveValue(null)
                                pendingPick = callback
                                return runCatching { pickPictures.launch("image/*"); true }.getOrElse {
                                    pendingPick = null
                                    false
                                }
                            }
                        }
                        addJavascriptInterface(
                            DocBridge(ctx, app, h.base, h.token, app.prefs.deviceId, dark) { closeNow() },
                            DocBridge.NAME,
                        )
                        loadUrl(h.page.toString())
                        webView = this
                    }
                },
            )
        }
    }
}

private data class DocHandoff(val base: HttpUrl, val page: HttpUrl, val token: String)

/** The same settings for the editor and for its print copy. */
@SuppressLint("SetJavaScriptEnabled")
private fun lockDown(s: WebSettings, javaScript: Boolean = true) {
    s.javaScriptEnabled = javaScript
    s.domStorageEnabled = javaScript
    s.allowFileAccess = false
    s.allowContentAccess = false
    s.setSupportMultipleWindows(false)
    s.javaScriptCanOpenWindowsAutomatically = false
    s.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
    // It only ever loads the household's own server; Safe Browsing would
    // send the addresses it loads to Google.
    s.safeBrowsingEnabled = false
}

/** Same scheme, host and port: the active server's own pages. */
internal fun sameOrigin(a: HttpUrl, b: HttpUrl): Boolean =
    a.scheme == b.scheme && a.host.equals(b.host, ignoreCase = true) && a.port == b.port

/** Keeps the WebView on the active server and re-checks every request. */
private open class DocWebClient(private val app: AppContainer, private val base: HttpUrl) : WebViewClient() {

    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
        val url = request.url.toString().toHttpUrlOrNull() ?: return true
        // The editor's own pages stay; anything else is refused rather than
        // opened in here.
        return !sameOrigin(base, url)
    }

    override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
        val scheme = request.url.scheme.orEmpty()
        // The page's own data: URIs (embedded pictures) never touch the network.
        if (scheme == "data" || scheme == "blob") return null
        val url = request.url.toString().toHttpUrlOrNull() ?: return refused()
        if (!sameOrigin(base, url) || !CleartextPolicy.permits(url)) return refused()
        // Off the main thread already; the gate's proof is cached per network.
        val admitted = runCatching { runBlocking { app.api.tokenForDownload(url) } }.getOrNull()
        return if (admitted == null) refused() else null
    }

    private fun refused(): WebResourceResponse =
        WebResourceResponse("text/plain", "utf-8", 403, "Forbidden", emptyMap(), ByteArrayInputStream(ByteArray(0)))
}

/**
 * What doc.html reads before it renders (window.DomovoiAppBridge). The
 * token is handed over ONCE: doc_page.jsx takes it on load, and nothing that
 * runs later in the page (or a page it is navigated to) can ask again. The
 * rest is the page asking the phone for things a browser would do itself:
 * close, the share sheet, the print service.
 */
private class DocBridge(
    private val context: Context,
    private val app: AppContainer,
    private val base: HttpUrl,
    token: String,
    private val deviceId: String?,
    private val dark: Boolean,
    private val onClose: () -> Unit,
) {
    private val main = Handler(Looper.getMainLooper())
    private val pending = java.util.concurrent.atomic.AtomicReference<String?>(token)
    // The print copy, while Android's print service is using it; destroyed
    // when the print job ends (or the next one starts).
    private var printing: WebView? = null

    @JavascriptInterface fun deviceToken(): String = pending.getAndSet(null).orEmpty()
    @JavascriptInterface fun deviceId(): String = deviceId.orEmpty()
    @JavascriptInterface fun darkTheme(): Boolean = dark
    @JavascriptInterface fun close() { main.post { onClose() } }

    /** Download → Word / Markdown: the file, through the share sheet. */
    @JavascriptInterface
    fun saveFile(name: String, mime: String, base64: String) {
        if (base64.length > MAX_SHARE_BYTES / 3 * 4 + 4) return
        val bytes = runCatching { Base64.decode(base64, Base64.DEFAULT) }.getOrNull() ?: return
        main.post { shareDocumentFile(context, safeFileName(name), shareMime(mime), bytes) }
    }

    /** Download → PDF: the page the editor built, to Android's print service. */
    @JavascriptInterface
    fun print(html: String, title: String) {
        if (html.length > MAX_PRINT_CHARS) return
        main.post {
            printing?.destroy()
            val w = WebView(context)
            lockDown(w.settings, javaScript = false)
            printing = w
            val name = safeFileName(title).ifBlank { "Document" }
            w.webViewClient = object : DocWebClient(app, base) {
                override fun onPageFinished(view: WebView, url: String?) {
                    val pm = context.getSystemService(Context.PRINT_SERVICE) as? PrintManager
                    if (pm == null) { release(view); return }
                    val inner = view.createPrintDocumentAdapter(name)
                    val ok = runCatching {
                        pm.print(name, ReleasingAdapter(inner) { release(view) }, PrintAttributes.Builder().build())
                    }.isSuccess
                    if (!ok) release(view)
                }
            }
            w.loadDataWithBaseURL(base.toString(), html, "text/html", "utf-8", null)
        }
    }

    private fun release(view: WebView) {
        main.post {
            if (printing === view) printing = null
            view.destroy()
        }
    }

    companion object {
        const val NAME = "DomovoiAppBridge"
        const val MAX_SHARE_BYTES = 64 * 1024 * 1024
        const val MAX_PRINT_CHARS = 48 * 1024 * 1024
    }
}

/** Android's adapter for the print copy, telling us when the job is done. */
private class ReleasingAdapter(
    private val inner: PrintDocumentAdapter,
    private val onDone: () -> Unit,
) : PrintDocumentAdapter() {
    override fun onStart() = inner.onStart()
    override fun onLayout(
        oldAttributes: PrintAttributes?, newAttributes: PrintAttributes?,
        cancellationSignal: CancellationSignal?, callback: LayoutResultCallback?, extras: Bundle?,
    ) = inner.onLayout(oldAttributes, newAttributes, cancellationSignal, callback, extras)

    override fun onWrite(
        pages: Array<out PageRange>?, destination: ParcelFileDescriptor?,
        cancellationSignal: CancellationSignal?, callback: WriteResultCallback?,
    ) = inner.onWrite(pages, destination, cancellationSignal, callback)

    override fun onFinish() {
        inner.onFinish()
        onDone()
    }
}

/** The tap that opens the in-app document editor: formatted documents,
 *  markdown and Word, in the server's Documents library. */
internal fun opensInDocEditor(name: String): Boolean =
    name.substringAfterLast('.', "").lowercase() in setOf("html", "htm", "md", "markdown", "docx")

/** Only the two kinds of file the editor hands out. */
internal fun shareMime(mime: String): String = when (mime) {
    "text/markdown" -> "text/markdown"
    else -> "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}

/** A name that is only a name: no folders, nothing a file system refuses. */
internal fun safeFileName(name: String): String =
    name.replace(Regex("[\\\\/:*?\"<>|\\u0000-\\u001f]"), "_").trim().trimStart('.').take(120).ifBlank { "Document" }

/** Hands a file to the share sheet (a copy in the app's cache, through the
 *  app's file provider, read-only). */
internal fun shareDocumentFile(context: Context, name: String, mime: String, bytes: ByteArray): Boolean = runCatching {
    val dir = File(context.cacheDir, "docs-share").apply { mkdirs() }
    dir.listFiles()?.forEach { it.delete() }
    val f = File(dir, name)
    f.writeBytes(bytes)
    val uri = androidx.core.content.FileProvider.getUriForFile(context, "${context.packageName}.files", f)
    val send = Intent(Intent.ACTION_SEND)
        .setType(mime)
        .putExtra(Intent.EXTRA_STREAM, uri)
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    context.startActivity(Intent.createChooser(send, name).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
}.isSuccess
