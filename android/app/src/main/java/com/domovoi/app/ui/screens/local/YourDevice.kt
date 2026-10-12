package com.domovoi.app.ui.screens.local

import androidx.activity.OnBackPressedDispatcher
import androidx.activity.OnBackPressedDispatcherOwner
import androidx.activity.compose.LocalOnBackPressedDispatcherOwner
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.layout.layoutId
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.domovoi.app.LocalApp
import com.domovoi.app.ui.screens.files.FilesBrowser
import com.domovoi.app.ui.screens.files.PhoneFileSource
import com.domovoi.app.ui.shell.LocalServerChoice
import com.domovoi.app.ui.shell.keyboardCrowdsTheWindow
import com.domovoi.app.ui.shell.title
import com.domovoi.app.ui.theme.Domovoi

/**
 * "Your device" on the connected Files, Music and Videos pages: a switch at
 * the top of the page between the server's content and the phone's own,
 * shown exactly as the unconnected pages show it (LocalMusicScreen,
 * LocalVideosScreen, FilesBrowser over PhoneFileSource — the same screens,
 * not copies), with each row's Download turned into Upload (DeviceUpload.kt).
 *
 * The choice is remembered per page for the session ([SourceChoices]), and
 * each side stays alive once shown, so switching back finds the server's
 * page where it was left — the folder, the tab, the page of tracks, the
 * scroll — without asking the server again.
 */

/** The pages that offer "Your device". */
internal enum class DevicePage { Files, Music, Videos }

/** Which side of the switch a page shows. */
internal enum class ContentSource { Server, Device }

/**
 * The side each page last showed. Lives in the workspace shell (one per
 * server, kept across a trip through local media and a rotation); every
 * page starts on the server's side.
 */
internal class SourceChoices {
    private val chosen = mutableStateMapOf<DevicePage, ContentSource>()

    fun of(page: DevicePage): ContentSource = chosen[page] ?: ContentSource.Server

    fun choose(page: DevicePage, source: ContentSource) {
        chosen[page] = source
    }

    /** "Music=Device" per page chosen, for the saved-state bundle. */
    fun encode(): List<String> = chosen.entries.map { (page, source) -> "${page.name}=${source.name}" }

    companion object {
        /** Restores what [encode] wrote; an entry it doesn't recognise is dropped. */
        fun decode(saved: List<String>): SourceChoices = SourceChoices().apply {
            saved.forEach { entry ->
                val page = DevicePage.entries.firstOrNull { it.name == entry.substringBefore('=') }
                val source = ContentSource.entries.firstOrNull { it.name == entry.substringAfter('=', "") }
                if (page != null && source != null) choose(page, source)
            }
        }

        val Saver: Saver<SourceChoices, ArrayList<String>> = Saver(
            save = { ArrayList(it.encode()) },
            restore = { decode(it) },
        )
    }
}

/** Provided by the workspace shell; a page outside it gets its own. */
internal val LocalSourceChoices = staticCompositionLocalOf { SourceChoices() }

/**
 * A page with the server / "your device" switch on top. The side not shown
 * is still composed but neither drawn nor laid out where it could be
 * touched, and its back handlers are parked on a dispatcher nothing presses,
 * so Back belongs to the side on screen.
 */
@Composable
internal fun SourceSwitchPage(
    page: DevicePage,
    server: @Composable () -> Unit,
    device: @Composable () -> Unit,
) {
    val choices = LocalSourceChoices.current
    val source = choices.of(page)
    val focus = LocalFocusManager.current
    // A side joins once it has been shown, and then stays — saveable, so
    // both come back after the workspace goes away and returns.
    val kept = rememberSaveable(
        saver = listSaver(
            save = { list -> list.map { it.name } },
            restore = { names -> mutableStateListOf(*names.map(ContentSource::valueOf).toTypedArray()) },
        ),
    ) { mutableStateListOf<ContentSource>() }
    SideEffect { if (source !in kept) kept.add(source) }

    Column(Modifier.fillMaxSize()) {
        SourceSwitch(
            serverTitle = LocalServerChoice.current.title(),
            source = source,
            onPick = {
                if (it != source) {
                    focus.clearFocus()
                    choices.choose(page, it)
                }
            },
        )
        Layout(
            content = {
                Box(Modifier.layoutId(ContentSource.Server)) {
                    if (source == ContentSource.Server || ContentSource.Server in kept) {
                        ShownOrParked(source == ContentSource.Server, server)
                    }
                }
                Box(Modifier.layoutId(ContentSource.Device)) {
                    if (source == ContentSource.Device || ContentSource.Device in kept) {
                        ShownOrParked(source == ContentSource.Device, device)
                    }
                }
            },
            modifier = Modifier.fillMaxWidth().weight(1f),
        ) { measurables, constraints ->
            val fixed = Constraints.fixed(constraints.maxWidth, constraints.maxHeight)
            val placeables = measurables.map { it.layoutId to it.measure(fixed) }
            layout(constraints.maxWidth, constraints.maxHeight) {
                placeables.forEach { (id, p) -> if (id == source) p.place(0, 0) }
            }
        }
    }
}

/** [content] with Back while shown; parked, its back handlers listen to nothing. */
@Composable
private fun ShownOrParked(shown: Boolean, content: @Composable () -> Unit) {
    val pageLifecycle = LocalLifecycleOwner.current.lifecycle
    val parked = remember(pageLifecycle) {
        object : OnBackPressedDispatcherOwner {
            override val onBackPressedDispatcher = OnBackPressedDispatcher()
            override val lifecycle: Lifecycle = pageLifecycle
        }
    }
    // One provider either way: swapping the composition's shape would
    // throw away the very state this page keeps.
    val owner = if (shown) LocalOnBackPressedDispatcherOwner.current ?: parked else parked
    CompositionLocalProvider(LocalOnBackPressedDispatcherOwner provides owner) { content() }
}

@Composable
private fun SourceSwitch(serverTitle: String, source: ContentSource, onPick: (ContentSource) -> Unit) {
    // The page's own header steps aside for the keyboard; so does this.
    if (keyboardCrowdsTheWindow()) return
    val shape = RoundedCornerShape(999.dp)
    Row(
        Modifier
            .fillMaxWidth()
            .padding(start = 16.dp, end = 16.dp, top = 12.dp)
            .height(40.dp)
            .clip(shape)
            .background(Domovoi.colors.sunken, shape)
            .border(1.dp, Domovoi.colors.border, shape)
            .padding(3.dp)
            .selectableGroup(),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        SourceSegment(Icons.Filled.Dns, serverTitle, source == ContentSource.Server, Modifier.weight(1f)) {
            onPick(ContentSource.Server)
        }
        SourceSegment(Icons.Filled.PhoneAndroid, "your device", source == ContentSource.Device, Modifier.weight(1f)) {
            onPick(ContentSource.Device)
        }
    }
}

@Composable
private fun SourceSegment(
    icon: ImageVector,
    label: String,
    selected: Boolean,
    modifier: Modifier,
    onClick: () -> Unit,
) {
    val shape = RoundedCornerShape(999.dp)
    Row(
        modifier
            .fillMaxHeight()
            .clip(shape)
            .background(if (selected) Domovoi.colors.brandSoft else androidx.compose.ui.graphics.Color.Transparent, shape)
            .then(if (selected) Modifier.border(1.dp, Domovoi.colors.brand, shape) else Modifier)
            .selectable(selected = selected, role = Role.Tab, onClick = onClick)
            .padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center,
    ) {
        Icon(
            icon, contentDescription = null,
            tint = if (selected) Domovoi.colors.brand else Domovoi.colors.fgMuted,
            modifier = Modifier.size(15.dp),
        )
        Spacer(Modifier.width(6.dp))
        Text(
            label,
            style = MaterialTheme.typography.labelLarge,
            color = if (selected) Domovoi.colors.fg else Domovoi.colors.fgMuted,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

// ---------------------------------------------------------------------------
// The device side of each page: the unconnected page, with Upload on its rows.
// ---------------------------------------------------------------------------

@Composable
internal fun YourDeviceMusic() {
    LocalMusicScreen(
        subtitle = "on this device — upload sends a track to the server's music library",
        upload = rememberDeviceUploader(UploadKind.Music),
    )
}

@Composable
internal fun YourDeviceVideos() {
    LocalVideosScreen(
        subtitle = "on this device — upload sends a video to the server",
        upload = rememberDeviceUploader(UploadKind.Video),
    )
}

@Composable
internal fun YourDeviceFiles() {
    val app = LocalApp.current
    val ctx = LocalContext.current
    val source = remember(app) { PhoneFileSource(ctx, app.prefs) }
    FilesBrowser(source, uploadMode = true)
}
