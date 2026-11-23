package com.domovoi.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.network.HttpException
import coil.request.ImageRequest
import com.domovoi.app.player.CoverArt
import com.domovoi.app.player.CoverSize
import com.domovoi.app.ui.theme.Domovoi

/**
 * A cover at a fixed size — the web's CoverArt (components.jsx): the
 * picture once it loads, over a placeholder that simply stays when there is
 * none (a 404 from the cover route, an artless file). The box is the same
 * size either way, so a list row never jumps when the picture lands, and a
 * cover the server said it doesn't have is not asked for again this run
 * ([CoverArt.isMissing]).
 *
 * A cover can change inside its file with the URL unchanged (V058), so the
 * request is keyed in memory by [CoverArt.epoch] as well: after the app
 * comes back to the screen the picture is fetched again (from disk, after
 * the server confirms its ETag; CoverArt.revalidate), with the last epoch's
 * picture shown meanwhile so nothing flickers. A versioned cover (V059,
 * `?v=`) needs none of that: its memory key is its URL, and a newer version
 * named by `library.cover.changed` ([CoverArt.versions]) replaces the one
 * the caller passed, the old picture staying until the new one lands.
 *
 * [model] comes from [CoverArt.model]: an absolute URL or a content:// URI.
 * A library track's cover is asked for at [coverSize] ([CoverArt.sized]),
 * by default the smallest copy sharp at [size] (CoverSize.forDp): a 40 dp
 * row gets the 160 px copy, not the original picture. Other art is drawn as
 * it is. [placeholder] defaults to the quiet sunken tile; the player passes
 * its warm gradient.
 */
@Composable
fun CoverImage(
    model: String?,
    size: Dp,
    modifier: Modifier = Modifier,
    corner: Dp = 8.dp,
    placeholder: Brush? = null,
    iconTint: Color? = null,
    iconSize: Dp = size * 0.42f,
    coverSize: CoverSize = CoverSize.forDp(size.value),
) {
    val shape = RoundedCornerShape(corner)
    // A newer version `library.cover.changed` named than the row carried
    // (V059): drawn at once, without the row being read again. The size is
    // part of the URL, so the memory key and Coil's disk key hold it too.
    val versions by CoverArt.versions.collectAsState()
    val drawn = CoverArt.sized(CoverArt.withKnownVersion(model, versions), coverSize)
    var shown by remember(drawn) { mutableStateOf(false) }
    // The memory key of the picture this box last showed: kept on screen
    // while a new version of the same cover loads.
    val last = remember { arrayOfNulls<String>(1) }
    val epoch by CoverArt.epoch.collectAsState()
    val ask = drawn != null && !CoverArt.isMissing(drawn)
    val context = LocalContext.current
    val request = remember(drawn, epoch) {
        drawn?.let {
            val key = CoverArt.memoryKey(it, epoch)
            val before = last[0]?.takeIf { k -> k != key && CoverArt.sameCover(k, it) }
            ImageRequest.Builder(context)
                .data(it)
                .memoryCacheKey(key)
                .placeholderMemoryCacheKey(before ?: CoverArt.memoryKey(it, epoch - 1))
                .build()
        }
    }
    Box(
        modifier
            .size(size)
            .clip(shape)
            .background(placeholder ?: SolidColor(Domovoi.colors.sunken))
            .border(1.dp, Domovoi.colors.border, shape),
        contentAlignment = Alignment.Center,
    ) {
        if (!shown) {
            Icon(
                Icons.Filled.MusicNote, contentDescription = null,
                tint = iconTint ?: Domovoi.colors.fgSubtle, modifier = Modifier.size(iconSize),
            )
        }
        if (ask) {
            AsyncImage(
                model = request,
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier.matchParentSize(),
                onSuccess = {
                    shown = true
                    last[0] = CoverArt.memoryKey(drawn!!, epoch)
                },
                onError = { state ->
                    val status = (state.result.throwable as? HttpException)?.response?.code
                    CoverArt.noteFailure(drawn!!, status)
                },
            )
        }
    }
}
