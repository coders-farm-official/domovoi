package com.domovoi.app.ui.screens.assistant

import android.content.Context
import android.os.Bundle
import android.os.CancellationSignal
import android.os.ParcelFileDescriptor
import android.print.PageRange
import android.print.PrintAttributes
import android.print.PrintDocumentAdapter
import android.print.PrintDocumentInfo
import android.print.PrintManager
import com.domovoi.app.assistant.docs.PhonePdfText
import com.domovoi.app.assistant.drafts.ChatDocumentsLibrary
import com.domovoi.app.assistant.drafts.DraftAssets
import com.domovoi.app.assistant.drafts.DraftAts
import com.domovoi.app.assistant.drafts.DraftCheck
import com.domovoi.app.assistant.drafts.DraftDiff
import com.domovoi.app.assistant.drafts.DraftDocx
import com.domovoi.app.assistant.drafts.DraftKinds
import com.domovoi.app.assistant.drafts.DraftPdf
import com.domovoi.app.assistant.drafts.PhoneDraft
import com.domovoi.app.assistant.drafts.PhoneDraftStore
import com.domovoi.app.assistant.drafts.SavedDraftFile
import com.domovoi.app.ui.screens.chat.DraftDoc
import com.domovoi.app.ui.screens.chat.DraftSaveResult
import com.domovoi.app.ui.screens.chat.DraftVersionView
import com.domovoi.app.ui.screens.chat.DraftView
import androidx.compose.runtime.getValue
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream

/** The phone's draft renderer and its "Chat documents" library, one each. */
internal object PhoneDraftFiles {
    @Volatile private var assets: DraftAssets? = null

    fun assets(context: Context): DraftAssets =
        assets ?: synchronized(this) { assets ?: DraftAssets(context.applicationContext).also { assets = it } }

    fun library(context: Context) = ChatDocumentsLibrary(File(context.applicationContext.filesDir, ChatDocumentsLibrary.FOLDER))

    /** A version as .docx or PDF: its file name and bytes. */
    fun render(context: Context, d: PhoneDraft, version: Int, body: String, format: String): Pair<String, ByteArray> {
        val a = assets(context)
        val bytes = if (format == "pdf") DraftPdf.render(a, body, d.template, d.title) else DraftDocx.render(body, a.template(d.template), d.title)
        return ChatDocumentsLibrary.fileName(d.title, version, format) to bytes
    }

    /** The ATS self-check on bytes this phone wrote: a PDF read back with
     *  the phone's PDF text API (when it has one), a .docx from its XML. */
    fun check(context: Context, bytes: ByteArray, format: String, markdown: String): DraftCheck {
        if (format != "pdf") return DraftAts.checkDocx(bytes, markdown)
        // Read as written always; with the phone's PDF text API too when it
        // has one (Android 15).
        val reader = PhonePdfText.readerOrNull()
        val tmp = File(context.cacheDir, "drafts-check-${System.nanoTime()}.pdf")
        return try {
            val text = reader?.let { r ->
                tmp.writeBytes(bytes)
                r.read(tmp, 200).joinToString("\n") { page -> page.blocks.joinToString("\n") { it.text } }
            }
            DraftAts.checkPdf(bytes, markdown, text)
        } catch (e: Exception) {
            DraftCheck(false, "text check: the saved file couldn't be read back", 0, DraftAts.expectedWords(markdown).size)
        } finally {
            tmp.delete()
        }
    }

    /** Android's own print dialog for a PDF the phone made (plan Q15: the
     *  phone's printers, Mopria or the maker's service). */
    fun print(context: Context, name: String, pdf: ByteArray): Boolean = runCatching {
        val pm = context.getSystemService(Context.PRINT_SERVICE) as PrintManager
        pm.print(name, BytesPrintAdapter(name, pdf), PrintAttributes.Builder().setMediaSize(PrintAttributes.MediaSize.NA_LETTER).build())
    }.isSuccess
}

/** A finished PDF handed to the print framework as it is. */
private class BytesPrintAdapter(private val name: String, private val pdf: ByteArray) : PrintDocumentAdapter() {
    override fun onLayout(
        oldAttributes: PrintAttributes?, newAttributes: PrintAttributes?, cancellationSignal: CancellationSignal?,
        callback: LayoutResultCallback, extras: Bundle?,
    ) {
        if (cancellationSignal?.isCanceled == true) {
            callback.onLayoutCancelled()
            return
        }
        val info = PrintDocumentInfo.Builder(name).setContentType(PrintDocumentInfo.CONTENT_TYPE_DOCUMENT)
            .setPageCount(PrintDocumentInfo.PAGE_COUNT_UNKNOWN).build()
        callback.onLayoutFinished(info, oldAttributes != newAttributes)
    }

    override fun onWrite(
        pages: Array<out PageRange>?, destination: ParcelFileDescriptor, cancellationSignal: CancellationSignal?,
        callback: WriteResultCallback,
    ) {
        try {
            FileOutputStream(destination.fileDescriptor).use { it.write(pdf) }
            callback.onWriteFinished(arrayOf(PageRange.ALL_PAGES))
        } catch (e: Exception) {
            callback.onWriteFailed(e.message)
        }
    }
}

/**
 * A phone chat's draft (its versions in [PhoneDraftStore]): saves go to the
 * phone's "Chat documents" library, "Send to home" to the home server's
 * Documents → From Chat when one is in reach ([homeUpload]), and a copy
 * anywhere through the system's file picker.
 */
internal class PhoneDraftDoc(
    private val context: Context,
    private val store: PhoneDraftStore,
    private val conversation: String,
    private val id: String,
    private val editDraft: suspend (String, Int) -> PhoneDraft?,
    private val homeUpload: (suspend (name: String, bytes: ByteArray) -> String)?,
) : DraftDoc {
    override val key: String = "phone:$conversation:$id"
    override val saveWhere: String = "${ChatDocumentsLibrary.FOLDER} on this phone"

    private suspend fun draft(): PhoneDraft = withContext(Dispatchers.IO) {
        store.load(conversation, id) ?: throw java.io.IOException("this draft is gone")
    }

    override suspend fun load(): DraftView = withContext(Dispatchers.IO) {
        val d = draft()
        var prev: String? = null
        val views = d.versions.sortedBy { it.version }.map { v ->
            val body = store.body(conversation, id, v.version).orEmpty()
            val display = DraftDiff.displayText(body)
            val changes = prev?.let { DraftDiff.diff(it, display) }
            prev = display
            DraftVersionView(
                v.version, body, display, changes, v.source, v.instruction,
                v.saved.map { s -> "${placeName(s.place)}${s.name} · ${s.check}" },
                basedOn = v.basedOn,
            )
        }
        DraftView(d.title, d.kind, DraftKinds.defaultFormat(d.kind), views)
    }

    private fun placeName(place: String) = when (place) {
        "home" -> "home server · From Chat/"
        "copy" -> "copy · "
        else -> "${ChatDocumentsLibrary.FOLDER}/"
    }

    override suspend fun edit(text: String, base: Int): Int? = editDraft(text, base)?.latest

    override suspend fun firstLines(version: Int): String? = withContext(Dispatchers.IO) {
        store.body(conversation, id, version)?.let { com.domovoi.app.ui.screens.chat.firstLinesOf(it) }
    }

    override suspend fun file(version: Int, format: String): Pair<String, ByteArray> = withContext(Dispatchers.IO) {
        val d = draft()
        val body = store.body(conversation, id, version) ?: throw java.io.IOException("that version is gone")
        PhoneDraftFiles.render(context, d, version, body, format)
    }

    override suspend fun save(version: Int, format: String): DraftSaveResult = withContext(Dispatchers.IO) {
        val body = store.body(conversation, id, version) ?: throw java.io.IOException("that version is gone")
        val (name, bytes) = file(version, format)
        val check = PhoneDraftFiles.check(context, bytes, format, body)
        val saved = PhoneDraftFiles.library(context).save(name, bytes)
        store.recordSave(conversation, id, version,
            SavedDraftFile(format, saved.name, "phone", check.line, check.ok, System.currentTimeMillis()))
        DraftSaveResult("${ChatDocumentsLibrary.FOLDER}/${saved.name}", check.line, check.ok)
    }

    override val sendHome: (suspend (Int, String) -> DraftSaveResult)? = homeUpload?.let { upload ->
        { version, format ->
            withContext(Dispatchers.IO) {
                val body = store.body(conversation, id, version) ?: throw java.io.IOException("that version is gone")
                val (name, bytes) = file(version, format)
                val check = PhoneDraftFiles.check(context, bytes, format, body)
                val where = upload(name, bytes)
                store.recordSave(conversation, id, version,
                    SavedDraftFile(format, where.substringAfterLast('/'), "home", check.line, check.ok, System.currentTimeMillis()))
                DraftSaveResult("home server · Documents/$where", check.line, check.ok)
            }
        }
    }
}

/** A phone chat's draft card: [ref] is the version its reply wrote (or
 *  offered to print); the chat's newer version, when there is one, shows as
 *  "latest vN". Save makes the kind's default format in Chat documents. */
@androidx.compose.runtime.Composable
internal fun PhoneDraftCard(
    ref: com.domovoi.app.assistant.drafts.DraftRef,
    messages: List<com.domovoi.app.assistant.StoredMessage>,
    line: String,
    actions: AssistantActions,
    toast: (String) -> Unit,
    onOpen: () -> Unit,
    scope: kotlinx.coroutines.CoroutineScope,
) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val doc = androidx.compose.runtime.remember(ref.id) { actions.draftDoc?.invoke(ref.id) } ?: return
    val newest = messages.mapNotNull { it.draft }.filter { it.id == ref.id }.maxOfOrNull { it.version } ?: ref.version
    val first by androidx.compose.runtime.produceState<String?>(null, ref.id, ref.version) {
        value = runCatching { doc.firstLines(ref.version) }.getOrNull()
    }
    com.domovoi.app.ui.screens.chat.DraftCard(
        com.domovoi.app.ui.screens.chat.DraftCardData(
            ref.id, ref.version, ref.kind, ref.title, ref.action, line,
            latest = newest.takeIf { it > ref.version }, firstLines = if (newest == ref.version) first else null,
        ),
        canPrint = true,
        onOpen = onOpen,
        onSave = {
            scope.launch {
                runCatching { doc.save(ref.version, DraftKinds.defaultFormat(ref.kind)) }
                    .onSuccess { toast("saved ${it.where} · ${it.check}") }
                    .onFailure { toast("couldn't save it: ${it.message}") }
            }
        },
        onShare = {
            scope.launch {
                runCatching { doc.file(ref.version, DraftKinds.defaultFormat(ref.kind)) }
                    .onSuccess { (name, bytes) ->
                        if (!com.domovoi.app.ui.screens.chat.shareDraftFile(context, name, bytes)) toast("no app on this phone can share it")
                    }
                    .onFailure { toast("couldn't share it: ${it.message}") }
            }
        },
        onPrint = { actions.printDraft(ref.id, ref.version) },
        onRevise = actions.reviseDraft?.let { r -> { text: String -> r(ref.id, text) } },
    )
}
