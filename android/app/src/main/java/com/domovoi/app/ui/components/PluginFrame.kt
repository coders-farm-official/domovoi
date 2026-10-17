package com.domovoi.app.ui.components

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.net.CapabilityPlugin
import com.domovoi.app.net.LocalCapabilities
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.DomovoiColors
import com.domovoi.app.ui.theme.LocalDomovoiColors

/*
 * The plugin screen frame — the dashboard's PluginPageFrame
 * (web/static/components.jsx) for the app's screens that a plugin provides
 * (Stations, Images). A screen a plugin provides reads as that plugin's,
 * not as part of Domovoi: one band across the top with the screen's icon,
 * the plugin's name and publisher, a "plugin" badge and a link to its row
 * on the dashboard's Plugins page (version, update, disable live there; the
 * app has no plugin management of its own). Inside the frame the brand
 * colours are the plugin's accent, as the server derived and checked them
 * (/api/capabilities, plugins_runtime/accent.py), so the screen's buttons,
 * starred icons and selection washes take its colour; the shell around it
 * keeps Domovoi's amber.
 */

/** `#rrggbb` or `#rrggbbaa` (the server's form) → a Color; null otherwise. */
fun parseAccentHex(value: String?): Color? {
    val v = value?.trim() ?: return null
    if (!v.startsWith("#") || (v.length != 7 && v.length != 9)) return null
    val hex = v.substring(1)
    if (!hex.all { it in '0'..'9' || it in 'a'..'f' || it in 'A'..'F' }) return null
    val rgb = hex.substring(0, 6).toLong(16)
    val alpha = if (hex.length == 8) hex.substring(6, 8).toLong(16) else 0xFFL
    return Color((alpha shl 24) or rgb)
}

/** The accent's colours for one theme: the app's palette with the brand
 *  family replaced, plus the ink (the accent as text and icons), which the
 *  app's palette has no slot for. Each token the server didn't send (or
 *  sent malformed) keeps the app's own colour. */
data class PluginTint(val colors: DomovoiColors, val ink: Color)

fun pluginTint(base: DomovoiColors, plugin: CapabilityPlugin?): PluginTint {
    val accent = plugin?.accent
    val tokens = (if (base.isDark) accent?.dark else accent?.light).orEmpty()
    fun tok(key: String, fallback: Color) = parseAccentHex(tokens[key]) ?: fallback
    val colors = base.copy(
        brand = tok("brand", base.brand),
        brandHover = tok("brand_hover", base.brandHover),
        brandPress = tok("brand_press", base.brandPress),
        brandFg = tok("brand_fg", base.brandFg),
        brandSoft = tok("brand_soft", base.brandSoft),
    )
    return PluginTint(colors, tok("brand_ink", colors.fg))
}

/** Wraps a plugin-provided screen in its frame. [capability] names the
 *  plugin (the one whose manifest declares it); without one on this server
 *  the screen renders bare, as before. */
@Composable
fun PluginScreenFrame(
    capability: String,
    icon: ImageVector,
    content: @Composable () -> Unit,
) {
    val plugin = LocalCapabilities.current.pluginFor(capability)
    if (plugin == null) {
        content()
        return
    }
    val tint = pluginTint(Domovoi.colors, plugin)
    val c = tint.colors
    val scheme = MaterialTheme.colorScheme.copy(
        primary = c.brand,
        onPrimary = c.brandFg,
        primaryContainer = c.brandSoft,
    )
    CompositionLocalProvider(LocalDomovoiColors provides c) {
        MaterialTheme(colorScheme = scheme, typography = MaterialTheme.typography) {
            Column(Modifier.fillMaxSize()) {
                PluginFrameBand(plugin, icon, tint)
                Box(Modifier.fillMaxWidth().weight(1f)) { content() }
            }
        }
    }
}

@Composable
private fun PluginFrameBand(plugin: CapabilityPlugin, icon: ImageVector, tint: PluginTint) {
    val c = tint.colors
    val app = LocalApp.current
    val context = LocalContext.current
    val name = plugin.name?.takeIf { it.isNotBlank() } ?: plugin.slug
    Column(Modifier.fillMaxWidth().background(c.canvas)) {
        Row(
            Modifier
                .fillMaxWidth()
                .background(c.brandSoft)
                .heightIn(min = 44.dp)
                .padding(start = 16.dp, end = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(icon, contentDescription = null, tint = tint.ink, modifier = Modifier.size(18.dp))
            Row(
                Modifier.weight(1f),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(
                    name, color = c.fg, fontWeight = FontWeight.SemiBold,
                    style = MaterialTheme.typography.bodyMedium, maxLines = 1,
                )
                plugin.publisher?.takeIf { it.isNotBlank() }?.let {
                    Text(
                        "by $it", color = c.fgMuted, style = MaterialTheme.typography.bodySmall,
                        maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false),
                    )
                }
                Text(
                    "plugin",
                    color = tint.ink,
                    style = MaterialTheme.typography.labelSmall,
                    maxLines = 1,
                    modifier = Modifier
                        .background(c.card, RoundedCornerShape(6.dp))
                        .border(1.dp, c.border, RoundedCornerShape(6.dp))
                        .padding(horizontal = 6.dp, vertical = 1.dp),
                )
            }
            // The Plugins page is the dashboard's (version, update, disable);
            // this opens it in the browser at the plugin's row.
            Row(
                Modifier
                    .heightIn(min = 44.dp)
                    .clickable {
                        runCatching {
                            context.startActivity(
                                Intent(Intent.ACTION_VIEW, Uri.parse(app.api.absolute("/#plugins/${plugin.slug}"))),
                            )
                        }
                    }
                    .padding(horizontal = 12.dp)
                    .semantics { contentDescription = "manage $name on the dashboard's Plugins page" },
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text("manage", color = c.fgMuted, style = MaterialTheme.typography.bodySmall)
                Icon(
                    Icons.AutoMirrored.Filled.OpenInNew, contentDescription = null,
                    tint = c.fgMuted, modifier = Modifier.size(14.dp),
                )
            }
        }
        HorizontalDivider(color = c.border)
    }
}
