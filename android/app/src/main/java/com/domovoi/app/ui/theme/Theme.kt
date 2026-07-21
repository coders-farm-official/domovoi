package com.domovoi.app.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable

// Theme preference: "light" | "dark" | "system" (mirrors the web toggle,
// plus follow-system which the web doesn't have).
enum class ThemeMode { Light, Dark, System }

private fun schemeFrom(c: DomovoiColors): ColorScheme {
    val base = if (c.isDark) darkColorScheme() else lightColorScheme()
    return base.copy(
        primary = c.brand,
        onPrimary = c.brandFg,
        primaryContainer = c.brandSoft,
        onPrimaryContainer = c.fg,
        secondary = c.fgMuted,
        onSecondary = c.card,
        secondaryContainer = c.sunken,
        onSecondaryContainer = c.fg,
        background = c.canvas,
        onBackground = c.fg,
        surface = c.canvas,
        onSurface = c.fg,
        surfaceVariant = c.sunken,
        onSurfaceVariant = c.fgMuted,
        surfaceContainer = c.card,
        surfaceContainerLow = c.card,
        surfaceContainerLowest = c.canvas,
        surfaceContainerHigh = c.raised,
        surfaceContainerHighest = c.raised,
        error = c.err,
        onError = c.card,
        outline = c.borderStrong,
        outlineVariant = c.border,
        scrim = androidx.compose.ui.graphics.Color.Black,
    )
}

@Composable
fun DomovoiTheme(mode: ThemeMode = ThemeMode.System, content: @Composable () -> Unit) {
    val dark = when (mode) {
        ThemeMode.Light -> false
        ThemeMode.Dark -> true
        ThemeMode.System -> isSystemInDarkTheme()
    }
    val colors = if (dark) DomovoiDarkColors else DomovoiLightColors
    CompositionLocalProvider(LocalDomovoiColors provides colors) {
        MaterialTheme(
            colorScheme = schemeFrom(colors),
            typography = DomovoiTypography,
            content = content,
        )
    }
}

object Domovoi {
    val colors: DomovoiColors
        @Composable @ReadOnlyComposable get() = LocalDomovoiColors.current
}
