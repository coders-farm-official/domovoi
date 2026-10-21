package com.domovoi.app.ui.screens.assistant

import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import com.domovoi.app.assistant.AssistantController
import com.domovoi.app.assistant.AssistantUi
import com.domovoi.app.assistant.ConversationSummary
import com.domovoi.app.assistant.DeviceTier
import com.domovoi.app.assistant.ModelSetup
import com.domovoi.app.ui.screens.chat.ChatFrame
import com.domovoi.app.ui.screens.chat.ChatPlace
import com.domovoi.app.ui.screens.chat.ChatPlaceKind
import com.domovoi.app.ui.screens.chat.ChatRow

/** A phone conversation as a row of Chat's list. */
internal fun ConversationSummary.toChatRow(): ChatRow = ChatRow(
    place = ChatPlaceKind.Phone,
    id = id,
    title = title,
    snippet = null,
    updatedMs = updated,
    messages = messages,
)

/** Whether the phone can write a reply now: its model is on the phone;
 *  null while that is being read. */
internal fun phoneReady(ui: AssistantUi): Boolean? = when {
    ui.tier == DeviceTier.Unsupported -> false
    ui.setup == ModelSetup.Checking -> null
    else -> ui.setup == ModelSetup.Installed
}

/**
 * This phone as a Chat place: the on-device assistant's conversations
 * ([AssistantController], kept in the app's private storage) and
 * [PhoneChat] for a conversation, with the phone's voice loop, its model
 * setup and "report this reply" saved on the phone. Built only where the
 * install has the engine (a 64-bit phone and APK) and the screen isn't
 * shared; the controller is never touched otherwise.
 */
@Composable
internal fun rememberPhoneChatPlace(assistant: AssistantController): ChatPlace {
    val ui by assistant.state.collectAsState()
    return PhoneChatPlace(assistant, ui)
}

private class PhoneChatPlace(private val assistant: AssistantController, ui: AssistantUi) : ChatPlace {
    override val kind = ChatPlaceKind.Phone

    override val chats: List<ChatRow> = ui.history.map { it.toChatRow() }

    override val ready: Boolean? = phoneReady(ui)

    /** The controller keeps its list current itself. */
    override fun refresh() = Unit

    override fun deleteText(row: ChatRow): String = "\"${row.title}\" is deleted from this phone. This can't be undone."

    override fun delete(row: ChatRow) = assistant.delete(row.id)

    @Composable
    override fun Conversation(row: ChatRow?, frame: ChatFrame) = PhoneChat(assistant, row?.id, frame)
}
