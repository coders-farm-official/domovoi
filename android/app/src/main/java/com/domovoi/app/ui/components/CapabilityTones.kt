package com.domovoi.app.ui.components

/**
 * Server tone slug → app [Tone]. The `/api/capabilities` handler_display
 * entries carry an open tone vocabulary (neutral|media|device|info|comms);
 * unknown slugs render neutral (design §8) — never an error.
 */
fun toneForSlug(slug: String?): Tone = when (slug) {
    "media" -> Tone.Brand
    "device" -> Tone.Ok
    "comms" -> Tone.Warn
    "info" -> Tone.Idle
    else -> Tone.Idle // "neutral" and anything unknown
}
