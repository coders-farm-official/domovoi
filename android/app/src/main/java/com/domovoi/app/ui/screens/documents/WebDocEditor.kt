package com.domovoi.app.ui.screens.documents

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.print.PrintAttributes
import android.print.PrintManager
import android.util.Base64
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.domovoi.app.LocalApp
import com.domovoi.app.net.CleartextPolicy
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.theme.Domovoi
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import java.io.File

/**
 * The Documents editor, in the app: the server's own editor page
 * (`/doc.html?path=…`, web/static/doc_page.jsx) in an in-app view, so a
 * document looks and edits the same on the phone as on the dashboard —
 * fonts, pictures, tables, co-editing — and saves through the same route.
 *
 * Only the active server's own pages are shown in it ([sameOrigin]); a
 * link to anywhere else opens in the browser instead. The household token
 * is handed to the page through a small bridge (`window.DomovoiApp`,
 * [DocBridge]) rather than in the address, and only while the page on
 * screen is the server's. The bridge also gives the page the phone's
 * share sheet (Download → Word / Markdown) and its print service
 * (Download → PDF, where "Save as PDF" is one of the printers).
 *
 * Opened for documents in the server's Documents library
 * ([opensInDocEditor]); a phone folder has no server to edit on, so its
 * files keep the on-phone text editor.
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
internal fun WebDocEditorOverlay(relPath: String, onClose: () -> Unit) {
    val app = LocalApp.current
    val ctx = LocalContext.current
    val url = remember(relPath) { app.api.absolute("/doc.html?path=" + Uri.encode(relPath)) }
    val httpUrl = remember(url) { url.toHttpUrlOrNull() }
    var token by remember { mutableStateOf<String?>(null) }
    var tokenChecked by remember { mutableStateOf(false) }
    var webView by remember { mutableStateOf<WebView?>(null) }
    var pendingPick by remember { mutableStateOf<ValueCallback<Array<Uri>>?>(null) }
    val background = Domovoi.colors.canvas.toArgb()

    LaunchedEffect(url) {
        token = httpUrl?.let { runCatching { app.api.tokenForDownload(it) }.getOrNull() }
        tokenChecked = true
    }

    val pickPictures = rememberLauncherForActivityResult(ActivityResultContracts.GetMultipleContents()) { uris ->
        pendingPick?.onReceiveValue(uris.toTypedArray())
        pendingPick = null
    }

    // Back goes through the page, which saves before it closes; a page that
    // never loaded just closes.
    BackHandler {
        val w = webView
        if (w == null) onClose() else w.evaluateJavascript(
            "(function(){if(window.DomovoiDocPage){window.DomovoiDocPage.back();return 1}return 0})()"
        ) { r -> if (r != "1") onClose() }
    }

    DisposableEffect(Unit) {
        onDispose {
            webView?.let { w ->
                w.removeJavascriptInterface(DocBridge.NAME)
                w.stopLoading()
                w.destroy()
            }
            webView = null
        }
    }

    Box(Modifier.fillMaxSize().background(Domovoi.colors.canvas)) {
        when {
            httpUrl == null || !CleartextPolicy.permits(httpUrl) -> Column(Modifier.padding(24.dp)) {
                Text(
                    if (httpUrl == null) "This server's address can't be opened here."
                    else CleartextPolicy.refusalMessage(httpUrl.host),
                    style = MaterialTheme.typography.bodyMedium,
                )
                Button(onClick = onClose, modifier = Modifier.padding(top = 16.dp)) { Text("Close") }
            }
            !tokenChecked -> LoadingState()
            else -> AndroidView(
                modifier = Modifier.fillMaxSize(),
                factory = { c ->
                    WebView(c).apply {
                        setBackgroundColor(background)
                        settings.javaScriptEnabled = true
                        settings.domStorageEnabled = true
                        settings.allowFileAccess = false
                        settings.allowContentAccess = false
                        settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
                        settings.setSupportMultipleWindows(false)
                        settings.javaScriptCanOpenWindowsAutomatically = false
                        val bridge = DocBridge(
                            context = c,
                            server = httpUrl,
                            token = token,
                            onClose = onClose,
                        )
                        webViewClient = object : WebViewClient() {
                            override fun onPageStarted(view: WebView, pageUrl: String?, favicon: android.graphics.Bitmap?) {
                                bridge.pageUrl = pageUrl
                                super.onPageStarted(view, pageUrl, favicon)
                            }

                            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                                val target = request.url.toString().toHttpUrlOrNull()
                                if (target != null && sameOrigin(httpUrl, target)) return false
                                // Anywhere else is not this editor's to show.
                                if (target != null) {
                                    runCatching {
                                        c.startActivity(Intent(Intent.ACTION_VIEW, request.url)
                                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                                    }
                                }
                                return true
                            }
                        }
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
                        addJavascriptInterface(bridge, DocBridge.NAME)
                        webView = this
                        loadUrl(url)
                    }
                },
            )
        }
    }
}

/** The tap that opens the in-app document editor: formatted documents,
 *  markdown and Word, in the server's Documents library. */
internal fun opensInDocEditor(name: String): Boolean =
    name.substringAfterLast('.', "").lowercase() in setOf("html", "htm", "md", "markdown", "docx")

/** Same scheme, host and port: the active server's own pages. */
internal fun sameOrigin(a: HttpUrl, b: HttpUrl): Boolean =
    a.scheme == b.scheme && a.host.equals(b.host, ignoreCase = true) && a.port == b.port

/**
 * `window.DomovoiApp` for the editor page. Its methods run on the WebView's
 * own thread; anything that touches views goes to the main thread.
 */
internal class DocBridge(
    private val context: Context,
    private val server: HttpUrl,
    private val token: String?,
    private val onClose: () -> Unit,
) {
    @Volatile
    var pageUrl: String? = null
    private val main = Handler(Looper.getMainLooper())
    // Print views must outlive the call that made them (printing is async).
    private val printing = mutableListOf<WebView>()

    private fun onServerPage(): Boolean =
        pageUrl?.toHttpUrlOrNull()?.let { sameOrigin(server, it) } == true

    /** The household token, for the server's own page only. */
    @JavascriptInterface
    fun deviceToken(): String? = if (onServerPage()) token else null

    @JavascriptInterface
    fun close() {
        main.post { onClose() }
    }

    /** Download → Word / Markdown: the file, through the share sheet. */
    @JavascriptInterface
    fun saveFile(name: String, mime: String, base64: String) {
        if (!onServerPage()) return
        val bytes = runCatching { Base64.decode(base64, Base64.DEFAULT) }.getOrNull() ?: return
        if (bytes.size > MAX_SHARE_BYTES) return
        main.post { shareDocumentFile(context, safeFileName(name), mime, bytes) }
    }

    /** Download → PDF: the page the editor built, to Android's print service. */
    @JavascriptInterface
    fun print(html: String, title: String) {
        if (!onServerPage()) return
        main.post {
            val w = WebView(context)
            w.settings.javaScriptEnabled = false
            w.settings.allowFileAccess = false
            w.settings.allowContentAccess = false
            printing += w
            w.webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView, url: String?) {
                    val pm = context.getSystemService(Context.PRINT_SERVICE) as? PrintManager ?: return
                    val name = safeFileName(title).ifBlank { "Document" }
                    runCatching { pm.print(name, view.createPrintDocumentAdapter(name), PrintAttributes.Builder().build()) }
                }
            }
            w.loadDataWithBaseURL(server.toString(), html, "text/html", "utf-8", null)
        }
    }

    companion object {
        const val NAME = "DomovoiApp"
        const val MAX_SHARE_BYTES = 64 * 1024 * 1024
    }
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
