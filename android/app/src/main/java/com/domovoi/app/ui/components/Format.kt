package com.domovoi.app.ui.components

import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlin.math.abs
import kotlin.math.roundToInt

/** Parse server timestamps — ISO 8601, with or without offset. */
fun parseInstant(iso: String?): Instant? {
    if (iso.isNullOrBlank()) return null
    return runCatching { OffsetDateTime.parse(iso).toInstant() }
        .recoverCatching { Instant.parse(iso) }
        .recoverCatching { java.time.LocalDateTime.parse(iso).atZone(ZoneId.systemDefault()).toInstant() }
        .getOrNull()
}

/** "just now", "4m ago", "2h ago", "3d ago" — the web relTime helper. */
fun relTime(iso: String?): String {
    val t = parseInstant(iso) ?: return "—"
    val sec = Duration.between(t, Instant.now()).seconds
    if (sec < 0) return relFuture(-sec)
    return when {
        sec < 45 -> "just now"
        sec < 3600 -> "${(sec / 60.0).roundToInt()}m ago"
        sec < 86_400 -> "${(sec / 3600.0).roundToInt()}h ago"
        sec < 86_400L * 30 -> "${(sec / 86_400.0).roundToInt()}d ago"
        else -> DateTimeFormatter.ofPattern("MMM d, yyyy").withZone(ZoneId.systemDefault()).format(t)
    }
}

private fun relFuture(sec: Long): String = when {
    sec < 60 -> "in ${sec}s"
    sec < 3600 -> "in ${(sec / 60.0).roundToInt()}m"
    sec < 86_400 -> "in ${(sec / 3600.0).roundToInt()}h"
    else -> "in ${(sec / 86_400.0).roundToInt()}d"
}

/** "3:04", "1:02:33" — the web fmtDur helper. */
fun fmtDur(seconds: Double?): String {
    val s = seconds?.takeIf { it.isFinite() && it >= 0 }?.roundToInt() ?: return "—"
    val h = s / 3600
    val m = (s % 3600) / 60
    val sc = s % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, sc) else "%d:%02d".format(m, sc)
}

/** "12.4 MB" etc. */
fun fmtBytes(bytes: Long?): String {
    val b = bytes ?: return "—"
    if (b < 1024) return "$b B"
    val kb = b / 1024.0
    if (kb < 1024) return "%.0f KB".format(kb)
    val mb = kb / 1024.0
    if (mb < 1024) return "%.1f MB".format(mb)
    return "%.2f GB".format(mb / 1024.0)
}

/** Big duration for stats: "41h 12m". */
fun fmtBigDur(seconds: Double?): String {
    val s = seconds?.roundToInt() ?: return "—"
    val h = s / 3600
    val m = (s % 3600) / 60
    return if (h > 0) "${h}h ${m}m" else "${m}m"
}

/** Countdown "12:04" / "1:02:33", used by timers. */
fun fmtRemaining(untilIso: String?): Pair<String, Long> {
    val t = parseInstant(untilIso) ?: return "—" to 0L
    val sec = Duration.between(Instant.now(), t).seconds
    if (sec <= 0) return "now" to 0L
    return fmtDur(sec.toDouble()) to sec
}

fun isLive(iso: String?, withinSec: Long = 300): Boolean {
    val t = parseInstant(iso) ?: return false
    return abs(Duration.between(t, Instant.now()).seconds) < withinSec
}
