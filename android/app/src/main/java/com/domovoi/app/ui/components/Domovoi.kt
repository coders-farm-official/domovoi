package com.domovoi.app.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.unit.dp
import com.domovoi.app.ui.theme.Domovoi

/**
 * Domovoi, the brand cat, drawn as a minimal glyph: two triangular ears +
 * a round face. Per the design rules he appears in exactly three places —
 * the top-left wordmark, next to the literal word "Domovoi", and empty
 * states (sleeping variant).
 */
@Composable
fun DomovoiGlyph(size: Int = 22, tint: Color? = null) {
    val color = tint ?: Domovoi.colors.brand
    Canvas(Modifier.size(size.dp)) {
        drawDomovoi(color, awake = true)
    }
}

@Composable
fun SleepingDomovoi(size: Int = 44) {
    val color = Domovoi.colors.fgFaint
    Canvas(Modifier.size(size.dp)) {
        drawDomovoi(color, awake = false)
    }
}

private fun androidx.compose.ui.graphics.drawscope.DrawScope.drawDomovoi(color: Color, awake: Boolean) {
    val w = size.width
    val h = size.height
    // ears
    val leftEar = Path().apply {
        moveTo(w * 0.18f, h * 0.42f); lineTo(w * 0.18f, h * 0.08f); lineTo(w * 0.44f, h * 0.30f); close()
    }
    val rightEar = Path().apply {
        moveTo(w * 0.82f, h * 0.42f); lineTo(w * 0.82f, h * 0.08f); lineTo(w * 0.56f, h * 0.30f); close()
    }
    drawPath(leftEar, color)
    drawPath(rightEar, color)
    // face
    drawOval(color, topLeft = Offset(w * 0.10f, h * 0.28f), size = Size(w * 0.80f, h * 0.62f))
    // eyes (canvas background shows through)
    val eyeColor = Color.Transparent
    if (awake) {
        drawCircle(eyeColor, radius = w * 0.055f, center = Offset(w * 0.36f, h * 0.58f), blendMode = androidx.compose.ui.graphics.BlendMode.Clear)
        drawCircle(eyeColor, radius = w * 0.055f, center = Offset(w * 0.64f, h * 0.58f), blendMode = androidx.compose.ui.graphics.BlendMode.Clear)
    } else {
        drawLine(eyeColor, Offset(w * 0.30f, h * 0.58f), Offset(w * 0.42f, h * 0.58f), strokeWidth = w * 0.045f, blendMode = androidx.compose.ui.graphics.BlendMode.Clear)
        drawLine(eyeColor, Offset(w * 0.58f, h * 0.58f), Offset(w * 0.70f, h * 0.58f), strokeWidth = w * 0.045f, blendMode = androidx.compose.ui.graphics.BlendMode.Clear)
    }
}
