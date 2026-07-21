package com.domovoi.app.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.domovoi.app.ui.theme.Domovoi

/** window.confirm analog. */
@Composable
fun ConfirmDialog(
    title: String,
    body: String,
    confirmLabel: String = "confirm",
    destructive: Boolean = false,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Domovoi.colors.raised,
        title = { Text(title, style = MaterialTheme.typography.titleMedium) },
        text = { Text(body, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted) },
        confirmButton = {
            TextButton(onClick = { onConfirm(); onDismiss() }) {
                Text(confirmLabel, color = if (destructive) Domovoi.colors.err else Domovoi.colors.brand)
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("cancel", color = Domovoi.colors.fgMuted) }
        },
    )
}

/** window.prompt analog — used by documents/drawings for naming files. */
@Composable
fun PromptDialog(
    title: String,
    placeholder: String = "",
    initial: String = "",
    confirmLabel: String = "create",
    onConfirm: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    var value by remember { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Domovoi.colors.raised,
        title = { Text(title, style = MaterialTheme.typography.titleMedium) },
        text = {
            Column {
                OutlinedTextField(
                    value = value,
                    onValueChange = { value = it },
                    placeholder = { Text(placeholder, color = Domovoi.colors.fgSubtle) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = { if (value.isNotBlank()) { onConfirm(value.trim()); onDismiss() } },
            ) { Text(confirmLabel, color = Domovoi.colors.brand) }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("cancel", color = Domovoi.colors.fgMuted) }
        },
    )
}
