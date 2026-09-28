package com.domovoi.app.net

import androidx.compose.runtime.compositionLocalOf

/**
 * Is this install a shared screen — the kitchen tablet? The Android half of
 * the web's useSharedScreen / DeviceIdentity.sharedScreen
 * (web/static/components.jsx, web/static/data.js).
 *
 * An admin marks a device in the dashboard's Settings → Devices, and every
 * device row the server returns carries `shared_screen` — this install's
 * own registration included ([registerDevice]). While it is true, Home
 * masks what a passer-by should not read (calendar titles become "busy",
 * a reminder's words become its room, the problem rows collapse to one
 * neutral line) and the personal screens drop off every launcher
 * ([com.domovoi.app.ui.shell.SharedScreenHidden]). Presentational, not a
 * boundary: the tablet still holds the household token.
 *
 * Provided by the shell ([com.domovoi.app.ui.shell.AppShell]).
 */
val LocalSharedScreen = compositionLocalOf { false }

/**
 * The answer a device row gives. A row with no `shared_screen` at all comes
 * from a web backend older than shared screens (V014), which cannot mark a
 * device — so it is "no", never "unknown". (The web leaves such a row
 * unanswered, which on a paired browser means "shared" forever; for a phone
 * that would hide Chat for good the moment it met an older server.)
 */
fun sharedScreenAnswer(row: DeviceRow): Boolean = row.sharedScreen ?: false

/**
 * What the launchers and Home treat this install as. [answer] is the
 * server's last word (null while it has never answered): a paired install
 * with no answer yet may well be the kitchen tablet, so it counts as shared
 * until the server says otherwise — the web's rule. An unpaired install
 * can't learn (its registration would be refused), so it is never masked.
 */
fun isSharedScreen(answer: Boolean?, paired: Boolean): Boolean = answer ?: paired
