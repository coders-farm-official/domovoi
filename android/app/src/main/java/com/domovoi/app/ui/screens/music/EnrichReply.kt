package com.domovoi.app.ui.screens.music

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull

/**
 * The toast after the Music page's "enrich" (POST /api/music/library/enrich).
 * The core starts a song-recognition sweep only when it can do something;
 * otherwise it answers `{"queued": false, "reason": ...}` and the toast says
 * why instead of "enrich started" (fix B3). An older core that always
 * answers `{"queued": true, ...}` keeps the old toast.
 */
internal fun enrichReplyText(reply: JsonElement?): String {
    val obj = reply as? JsonObject ?: return "enrich started"
    val queued = (obj["queued"] as? JsonPrimitive)?.booleanOrNull
    if (queued != false) return "enrich started"
    return when (val reason = (obj["reason"] as? JsonPrimitive)?.contentOrNull) {
        "disabled" -> "song recognition is turned off on the server"
        "offline" -> "no internet right now — try again when it's back"
        "no_provider" -> "song recognition needs a free AcoustID key or the Shazam add-on"
        "running" -> "already identifying songs"
        else -> "enrich didn't start (${reason ?: "unknown reason"})"
    }
}
