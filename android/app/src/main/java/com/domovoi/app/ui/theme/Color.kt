package com.domovoi.app.ui.theme

import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

// sRGB conversions of the oklch tokens in web/static/colors_and_type.css /
// .claude/skills/domovoi-design/colors_and_type.css. Single amber accent —
// never introduce a second accent color.

@Immutable
data class DomovoiColors(
    val brand: Color,
    val brandHover: Color,
    val brandPress: Color,
    val brandFg: Color,
    val brandSoft: Color,
    val canvas: Color,
    val card: Color,
    val raised: Color,
    val sunken: Color,
    val fg: Color,
    val fgMuted: Color,
    val fgSubtle: Color,
    val fgFaint: Color,
    val border: Color,
    val borderStrong: Color,
    val borderSoft: Color,
    val ok: Color,
    val okSoft: Color,
    val warn: Color,
    val warnSoft: Color,
    val err: Color,
    val errSoft: Color,
    val idle: Color,
    val idleSoft: Color,
    val isDark: Boolean,
)

val DomovoiLightColors = DomovoiColors(
    brand = Color(0xFFF2A618),
    brandHover = Color(0xFFE49900),
    brandPress = Color(0xFFD78D00),
    brandFg = Color(0xFF281600),
    brandSoft = Color(0x1FF2A618),
    canvas = Color(0xFFFCFAF6),
    card = Color(0xFFFFFFFF),
    raised = Color(0xFFFFFEFC),
    sunken = Color(0xFFF5F3F0),
    fg = Color(0xFF181611),
    fgMuted = Color(0xFF58554F),
    fgSubtle = Color(0xFF898681),
    fgFaint = Color(0xFFB9B7B4),
    border = Color(0xFFE6E4E1),
    borderStrong = Color(0xFFCFCDCA),
    borderSoft = Color(0xFFF0EEEB),
    ok = Color(0xFF54BF5C),
    okSoft = Color(0x1F54BF5C),
    warn = Color(0xFFFFA242),
    warnSoft = Color(0x1FFFA242),
    err = Color(0xFFEA3C3F),
    errSoft = Color(0x1FEA3C3F),
    idle = Color(0xFFA09E9B),
    idleSoft = Color(0x24A09E9B),
    isDark = false,
)

val DomovoiDarkColors = DomovoiColors(
    brand = Color(0xFFF9AD26),
    brandHover = Color(0xFFFFB93A),
    brandPress = Color(0xFFE49900),
    brandFg = Color(0xFF1C0E00),
    brandSoft = Color(0x24F9AD26),
    canvas = Color(0xFF14120F),
    card = Color(0xFF1B1916),
    raised = Color(0xFF221F1C),
    sunken = Color(0xFF100E0B),
    fg = Color(0xFFF5F3F0),
    fgMuted = Color(0xFFA7A49F),
    fgSubtle = Color(0xFF74716C),
    fgFaint = Color(0xFF4F4D48),
    border = Color(0xFF2F2D2A),
    borderStrong = Color(0xFF494744),
    borderSoft = Color(0xFF23211E),
    ok = Color(0xFF61D46A),
    okSoft = Color(0x2961D46A),
    warn = Color(0xFFFFA84A),
    warnSoft = Color(0x29FFA84A),
    err = Color(0xFFFF5F5B),
    errSoft = Color(0x29FF5F5B),
    idle = Color(0xFF73716E),
    idleSoft = Color(0x2E73716E),
    isDark = true,
)

val LocalDomovoiColors = staticCompositionLocalOf { DomovoiLightColors }
