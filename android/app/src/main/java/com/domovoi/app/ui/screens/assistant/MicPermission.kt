package com.domovoi.app.ui.screens.assistant

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import androidx.compose.runtime.Composable
import com.domovoi.app.ui.components.ConfirmDialog

/**
 * The microphone permission, asked the way Play's policy and Android's
 * guidance want it: on the first tap of the mic, after a one-line reason
 * the app gives itself ([MIC_REASON]), never at start. A refusal Android
 * will not ask about again (refused twice, or "don't ask again") gets the
 * way back instead ([MIC_REFUSED], the app's settings): the system shows no
 * dialog for it any more, so a tap that silently did nothing would mislead.
 */
internal enum class MicAsk { Reason, Refused }

/** Why the assistant wants the microphone, before Android's own dialog. */
internal const val MIC_REASON =
    "Domovoi needs the microphone to hear your question. It listens only after you tap the mic, stops when you " +
        "finish speaking, and what it hears stays on this phone: the audio is never kept."

/** After a refusal Android won't ask about again. */
internal const val MIC_REFUSED =
    "The microphone is off for Domovoi, and Android won't ask again. To talk to Domovoi on this phone, open the " +
        "app's settings, then Permissions, then Microphone, and allow it. Typing works without it."

/** The toast after a refusal Android may still ask about. */
internal const val MIC_NOT_NOW = "no microphone: typing works without it, and the mic asks again next time"

/** What a refused request leads to: [MicAsk.Refused] once Android won't
 *  show its dialog again ([canAskAgain] false), else nothing (a toast). */
internal fun afterRefusal(canAskAgain: Boolean): MicAsk? = if (canAskAgain) null else MicAsk.Refused

/** The dialog for [ask], if any. */
@Composable
internal fun MicPermissionDialog(ask: MicAsk?, onContinue: () -> Unit, onOpenSettings: () -> Unit, onDismiss: () -> Unit) {
    when (ask) {
        MicAsk.Reason -> ConfirmDialog(
            title = "use the microphone?",
            body = MIC_REASON,
            confirmLabel = "continue",
            onConfirm = onContinue,
            onDismiss = onDismiss,
        )
        MicAsk.Refused -> ConfirmDialog(
            title = "the microphone is off",
            body = MIC_REFUSED,
            confirmLabel = "open settings",
            onConfirm = onOpenSettings,
            onDismiss = onDismiss,
        )
        null -> Unit
    }
}

/** The activity behind a composable's context (permission rationale needs it). */
internal tailrec fun Context.activity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.activity()
    else -> null
}
