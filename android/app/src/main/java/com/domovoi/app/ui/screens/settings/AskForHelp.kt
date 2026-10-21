package com.domovoi.app.ui.screens.settings

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.HelpOutline
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.foundation.layout.padding
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.assistant.AssistantSupport
import com.domovoi.app.net.LocalCapabilities
import com.domovoi.app.net.LocalSharedScreen
import com.domovoi.app.net.decode
import com.domovoi.app.net.failureText
import com.domovoi.app.ui.screens.chat.HELP_APP_TITLE
import com.domovoi.app.ui.screens.chat.HelpEntry
import com.domovoi.app.ui.screens.chat.PlaceFacts
import com.domovoi.app.ui.screens.chat.ThreadRow
import com.domovoi.app.ui.screens.chat.helpEntry
import com.domovoi.app.ui.screens.chat.startablePlaces
import com.domovoi.app.ui.shell.Route
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Settings → About, "Ask Domovoi for help" (assistant plan part 9): a help
 * chat. On the home server when a chat can start there (the place picker's
 * rule): a thread titled [HELP_APP_TITLE], which the server answers from
 * Domovoi's help only. Otherwise a phone chat, where the manual the app
 * carries answers; with neither, the Manual screen.
 */
@Composable
internal fun AskForHelpCard(navigate: (Route) -> Unit) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val caps = LocalCapabilities.current
    val shared = LocalSharedScreen.current
    val connected by app.bus.connected.collectAsState()
    val scope = rememberCoroutineScope()
    var starting by remember { mutableStateOf(false) }
    val entry = helpEntry(
        startablePlaces(
            PlaceFacts(
                away = !connected,
                serverChatOn = caps.aiEnabled,
                phoneEngine = AssistantSupport.available,
                phoneCapable = AssistantSupport.phoneCanChat,
                shared = shared,
            ),
        ),
    )
    PanelCard("Ask Domovoi for help", "questions about Domovoi, answered from its manual") {
        Button(
            enabled = !starting,
            onClick = {
                when (entry) {
                    HelpEntry.ServerChat -> {
                        starting = true
                        scope.launch {
                            runCatching {
                                app.api.post("/api/chat/threads", buildJsonObject { put("title", HELP_APP_TITLE) })
                                    .decode<ThreadRow>()
                            }.onSuccess { t ->
                                app.pendingChatThread.value = t.id to (t.title ?: HELP_APP_TITLE)
                                navigate(Route.Chat)
                            }.onFailure { toast(failureText("start a help chat", it)) }
                            starting = false
                        }
                    }
                    HelpEntry.PhoneChat -> {
                        app.pendingPhoneChat.value = true
                        navigate(Route.Chat)
                    }
                    HelpEntry.Manual -> navigate(Route.Manual)
                }
            },
        ) {
            Icon(
                if (entry == HelpEntry.Manual) Icons.Filled.HelpOutline else Icons.AutoMirrored.Filled.Chat,
                contentDescription = null,
                modifier = Modifier.padding(end = 6.dp),
            )
            Text(if (entry == HelpEntry.Manual) "Open the user manual" else "Ask Domovoi for help")
        }
    }
}
