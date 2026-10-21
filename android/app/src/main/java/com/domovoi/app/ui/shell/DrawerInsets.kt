package com.domovoi.app.ui.shell

import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.runtime.Composable

/**
 * The insets the EXPANDED shell's permanent sidebar (DrawerShell in
 * AppShell.kt) pads its content by.
 *
 * MainActivity calls enableEdgeToEdge(), so the window is laid out from y=0
 * behind the status bar. The sidebar's Surface is meant to run under the bars
 * (its colour fills behind the clock, as Material3's own drawer sheets do),
 * but its CONTENT has to start below them: before this, the "domovoi /
 * android" brand row sat under the status bar on a tablet in landscape, with
 * the clock drawn over it.
 *
 * Material3's `DrawerDefaults.windowInsets` is the system bars' Vertical +
 * Start; this is the same shape over `safeDrawing`, which adds the display
 * cutout (a phone in landscape is EXPANDED too, and its camera cutout lands
 * on the start edge in one of the two rotations) and the keyboard. The shell
 * root already carries imePadding(), which consumes the keyboard inset, so
 * the bottom here is the navigation bar alone while the keyboard is down and
 * nothing extra while it is up.
 *
 * End is left out: the sidebar never touches the end edge.
 */
internal val drawerSheetInsets: WindowInsets
    @Composable get() = WindowInsets.safeDrawing.only(WindowInsetsSides.Vertical + WindowInsetsSides.Start)
