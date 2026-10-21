package com.domovoi.app.ui.screens.chat

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.platform.LocalContext
import com.domovoi.app.LocalToast
import com.domovoi.app.assistant.AssistantController
import com.domovoi.app.assistant.ChatExport
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * "Export all phone chats" (assistant plan part 7): Android's file picker
 * asks where the zip goes (Markdown plus the JSON, [ChatExport]), and the
 * chats are written there; nothing leaves the phone any other way. Here, not
 * beside the phone chat's own screens, because it names a picked file's
 * address (`android.net.Uri`): the assistant's packages are held to naming
 * nothing under `android/net` at all (NoNetworkTest).
 */
@Composable
internal fun rememberExportPhoneChats(assistant: AssistantController): () -> Unit {
    val context = LocalContext.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            val written = runCatching {
                val bundle = assistant.exportAll()
                val bytes = ChatExport.zip(bundle)
                withContext(Dispatchers.IO) {
                    context.contentResolver.openOutputStream(uri)?.use { it.write(bytes) } ?: error("no file")
                }
                bundle.count
            }
            toast(written.fold({ n -> "exported $n phone ${if (n == 1) "chat" else "chats"}" }, { "couldn't export the chats" }))
        }
    }
    return { pick.launch(ChatExport.fileName(System.currentTimeMillis())) }
}
