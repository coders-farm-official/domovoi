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
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.network.HttpException
import com.domovoi.app.player.CoverArt
import com.domovoi.app.ui.theme.Domovoi

/**
 * A cover at a fixed size — the web's CoverArt (components.jsx): the
 * picture once it loads, over a placeholder that simply stays when there is
 * none (a 404 from the cover route, an artless file). The box is the same
 * size either way, so a list row never jumps when the picture lands, and a
 * cover the server said it doesn't have is not asked for again this run
 * ([CoverArt.isMissing]).
 *
 * [model] comes from [CoverArt.model]: an absolute URL or a content:// URI.
 * [placeholder] defaults to the quiet sunken tile; the player passes its
 * warm gradient.
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
) {
    val shape = RoundedCornerShape(corner)
    var shown by remember(model) { mutableStateOf(false) }
    val ask = model != null && !CoverArt.isMissing(model)
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
                model = model,
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier.matchParentSize(),
                onSuccess = { shown = true },
                onError = { state ->
                    val status = (state.result.throwable as? HttpException)?.response?.code
                    CoverArt.noteFailure(model!!, status)
                },
            )
        }
    }
}
