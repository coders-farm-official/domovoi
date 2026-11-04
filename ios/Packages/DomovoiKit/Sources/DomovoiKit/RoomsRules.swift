import Foundation

// ---------------------------------------------------------------------------
// The Rooms screen's rules: everything the views decide, as functions over the
// wire models. Ports of `SatModels.kt:255-331`, `SatOverviewTab.kt:70-205`,
// `SatHistoryTabs.kt:84-470` and `SatQuietHours.kt:68-94`.
//
// They are here, and not in the views, for the reason every section on this
// track has put them here: which tone a link is, whether a room is playing
// anything, whether a reminder's words may be shown, whether a clock time is
// one at all and which rooms a drop-in could even reach are **decisions**, and
// a view cannot be asked whether it got them right.
// ---------------------------------------------------------------------------

// MARK: - the roster's summary

/// `"1 online · 1 offline · 2 provisioned"` (`SatellitesScreen.kt:143`).
///
/// The counts come from ``RoomsSatellite/online``, which is `status ==
/// "online"` — and `offline` is **everything else**, `waiting` among them, as
/// Android's `sats.size - onlineCount` is. A room that is set up and has never
/// connected is not online, and saying "waiting" here would be a third number
/// the dashboard does not show.
public func roomsSummary(_ rooms: [RoomsSatellite]) -> String {
    let online = rooms.filter(\.online).count
    return RoomsStrings.summary(online: online, offline: rooms.count - online, provisioned: rooms.count)
}

/// Kotlin's `Math.round(Double)`: **half up**, which for a negative number is
/// not what Swift's `rounded()` does — `Math.round(-52.5)` is `-52` and
/// `(-52.5).rounded()` is `-53`.
///
/// Signal readings are negative dBm and land on halves often enough that this
/// matters for one digit on screen; the two phones should print the same digit
/// for the same reading.
func roomsRoundHalfUp(_ value: Double) -> Int {
    Int((value + 0.5).rounded(.down))
}

// MARK: - Wi-Fi

/// `wifiTone(rx)` (`SatModels.kt:257`) — by **rate**, which is the only thing
/// a satellite too old to report a signal gives.
///
/// The Kotlin's comment is the reason it is not more alarming than it looks:
/// "the rate is the LAST frame's, and an idle link's last frame is a beacon at
/// 1 Mbit/s".
public func roomsWifiRateTone(_ rxMbits: Double?) -> HealthTone {
    guard let rx = rxMbits else { return .idle }
    if rx < 5 { return .err }
    if rx < 15 { return .warn }
    return .ok
}

/// The received-signal bands, in dBm (`SatModels.kt:268-269`): at or above
/// -60 good, -60 to -70 fair, below -70 weak.
public let roomsWifiSignalGoodDbm = -60.0
public let roomsWifiSignalFairDbm = -70.0

/// `wifiSignalTone` (`SatModels.kt:271`).
public func roomsSignalTone(_ dbm: Double?) -> HealthTone {
    guard let dbm else { return .idle }
    if dbm >= roomsWifiSignalGoodDbm { return .ok }
    if dbm >= roomsWifiSignalFairDbm { return .warn }
    return .err
}

/// `wifiSignalWord` (`SatModels.kt:278`): the band in one word, and **empty**
/// for a reading there is none for — which is Android's `else -> ""`, so the
/// overview prints `-52 dBm` with nothing after it rather than `-52 dBm
/// unknown`.
public func roomsSignalWord(_ dbm: Double?) -> String {
    switch roomsSignalTone(dbm) {
    case .ok: return RoomsStrings.signalGood
    case .warn: return RoomsStrings.signalFair
    case .err: return RoomsStrings.signalWeak
    case .idle, .brand: return ""
    }
}

/// A link's tone (`wifiTone(w)`, `SatModels.kt:287`): by signal when the
/// satellite reports one, **red while it hosts the setup hotspot**, else by
/// rate.
///
/// The hotspot branch comes first on purpose: a radio in AP mode has a
/// splendid signal to itself and is not on the house network at all.
public func roomsWifiTone(_ wifi: RoomsWifi?) -> HealthTone {
    guard let wifi else { return .idle }
    if wifi.mode == "ap" { return .err }
    if wifi.signalDbm != nil { return roomsSignalTone(wifi.signalDbm) }
    return roomsWifiRateTone(wifi.rxMbits)
}

/// `wifiLinkText` (`SatModels.kt:293`): what the card's footer reads —
/// `-52 dBm` with the rate second, the rate alone from an older satellite.
public func roomsWifiLinkText(_ wifi: RoomsWifi?) -> (main: String, sub: String?) {
    guard let wifi else { return (RoomsStrings.dash, nil) }
    if wifi.mode == "ap" { return (RoomsStrings.setupHotspot, RoomsStrings.notOnTheHouseWifi) }
    if let dbm = wifi.signalDbm {
        return ("\(roomsRoundHalfUp(dbm)) dBm", wifi.rxMbits.map { String(format: "%.0f Mbit/s", $0) })
    }
    guard let rx = wifi.rxMbits else { return (RoomsStrings.dash, nil) }
    return (String(format: "%.0f Mbit/s", rx), nil)
}

/// The overview's Wi-Fi pair — a headline with its tone, and the line under it
/// (`SatOverviewTab.kt:105-155`).
///
/// Four branches in the Kotlin's order: the hotspot, a reported signal, a rate
/// alone, and nothing at all. The third prints `rx / tx Mbit/s`, which is the
/// one place both directions are shown.
public func roomsWifiOverview(_ wifi: RoomsWifi?) -> (headline: String, tone: HealthTone, detail: String) {
    if wifi?.mode == "ap" {
        return (RoomsStrings.setupHotspot, .err, RoomsStrings.hostingItsSetupNetwork)
    }
    if let dbm = wifi?.signalDbm {
        let word = roomsSignalWord(dbm)
        let headline = word.isEmpty ? "\(roomsRoundHalfUp(dbm)) dBm" : "\(roomsRoundHalfUp(dbm)) dBm \(word)"
        let rate = wifi?.rxMbits.map { String(format: " · %.1f Mbit/s", $0) } ?? ""
        return (headline, roomsSignalTone(dbm), (wifi?.ssid ?? RoomsStrings.dash) + rate)
    }
    if let rx = wifi?.rxMbits {
        let both = String(format: "%.1f", rx) + (wifi?.txMbits.map { String(format: " / %.1f", $0) } ?? "")
        return (both + " Mbit/s", roomsWifiRateTone(rx), wifi?.ssid ?? RoomsStrings.dash)
    }
    return (RoomsStrings.noSignal, .idle, "")
}

// MARK: - the song line

/// `songTitle` (`SatModels.kt:307`): the title, else the file's last path
/// component, else `unknown`.
public func roomsSongTitle(_ np: RoomsNowPlaying?) -> String {
    if let title = np?.song?.title { return title }
    if let file = np?.song?.file { return String(file.split(separator: "/").last ?? "") }
    return "unknown"
}

/// Whether a room is **playing** — `state == "play" && song != nil`
/// (`SatellitesScreen.kt:196`).
///
/// Not "is there a song": a stopped room keeps its last song loaded and the
/// roster still names it, which is the 2026-10-11 bug where an idle room
/// looked like it was playing at 0:00 (`HomeModels.swift:179-184`).
public func roomsPlaying(_ np: RoomsNowPlaying?) -> Bool {
    np?.state == "play" && np?.song != nil
}

/// The elapsed seconds to draw: the server's reading plus the ticker's
/// extrapolation, and **zero** for a room that is not playing
/// (`SatellitesScreen.kt:198`).
public func roomsElapsedSec(_ np: RoomsNowPlaying?, extraSec: Double) -> Double {
    guard roomsPlaying(np) else { return 0 }
    return (np?.elapsedSec ?? 0) + max(0, extraSec)
}

/// The progress bar's fraction, clamped to 0...1 and zero without a duration
/// (`SatellitesScreen.kt:199`).
public func roomsProgress(elapsed: Double, duration: Double?) -> Double {
    guard let duration, duration > 0, elapsed.isFinite else { return 0 }
    return min(max(elapsed / duration, 0), 1)
}

/// The clock under the song: `1:12 / 3:40`, and the elapsed alone when the
/// duration is unknown (`SatellitesScreen.kt:277`).
public func roomsSongClock(elapsed: Double, duration: Double?) -> String {
    let left = fmtDur(elapsed)
    guard let duration, duration > 0 else { return left }
    return left + " / " + fmtDur(duration)
}

// MARK: - when a room was last heard from

/// **`last_seen_at` first**, falling back to `last_connected_at`.
///
/// The divergence from Android, and the schema's own words for why:
/// `last_connected_at` is when the MPD row was last allocated, "so it is NOT
/// when the satellite was last seen (an offline card read `19d ago` on
/// 2026-10-14 for rooms heard minutes before the core froze). Kept for
/// compatibility; show `last_seen_at`" (`web/backend/schemas.py:518-528`).
/// Android reads `last_connected_at` on both the card and the overview
/// (`SatellitesScreen.kt:290`, `SatOverviewTab.kt:92`), which is the defect
/// this port does not copy.
///
/// A blank string counts as absent: the backend has sent `""` for an
/// unconnected room, and `relTime("")` would be an em dash where the real
/// stamp is one field over.
public func roomsLastSeenISO(_ room: RoomsSatellite) -> String? {
    if let seen = room.lastSeenAt, !seen.trimmingCharacters(in: .whitespaces).isEmpty { return seen }
    guard let connected = room.lastConnectedAt,
          !connected.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
    return connected
}

/// What the card's footer and the overview's headline say: `active now` for an
/// online room, else how long ago it was last heard from
/// (`SatellitesScreen.kt:289`, `SatOverviewTab.kt:92`).
public func roomsLastSeenText(_ room: RoomsSatellite, nowMs: Int64) -> String {
    room.online ? RoomsStrings.activeNow : relTime(roomsLastSeenISO(room), nowMs: nowMs)
}

/// The raw stamp under it: `2026-11-02 19:30 UTC`
/// (`last_connected_at.replace('T',' ').take(16) + " UTC"`,
/// `SatOverviewTab.kt:99`).
///
/// The server's timestamps are UTC, and the Kotlin says so in the string
/// rather than converting — a stamp relabelled into the phone's zone would
/// disagree with the same row in the dashboard. An em dash when there is no
/// stamp, as everywhere else.
public func roomsRawStamp(_ room: RoomsSatellite) -> String {
    guard let iso = roomsLastSeenISO(room) else { return RoomsStrings.dash }
    let spaced = iso.replacingOccurrences(of: "T", with: " ")
    return String(spaced.prefix(16)) + RoomsStrings.utcSuffix
}

// MARK: - the read rows

/// `s.voice ?: "registry default"` (`SatOverviewTab.kt:196`).
public func roomsVoiceText(_ room: RoomsSatellite) -> String {
    room.voice ?? RoomsStrings.registryDefault
}

/// The volume the slider starts at: the server's level rounded, or nil while
/// the board has not reported one (`SatOverviewTab.kt:231`).
public func roomsVolumeLevel(_ room: RoomsSatellite) -> Int? {
    guard let volume = room.volume, volume.isFinite else { return nil }
    return min(max(roomsRoundHalfUp(volume), 0), 100)
}

/// A nudge's new level: `(vol + delta).coerceIn(0, 100)`
/// (`SatOverviewTab.kt:284`).
public func roomsNudge(_ level: Int, by delta: Int) -> Int {
    min(max(level + delta, 0), 100)
}

// MARK: - upgrade and reboot, as facts

/// `needsUpgrade` (`SatOverviewTab.kt:77`).
///
/// **A null `version` is UNKNOWN, not behind** — the Kotlin's own emphasis. A
/// hand-built satellite that has never reported a SHA must not be nagged, and
/// since this port draws no upgrade button there is nothing to enable either:
/// the pill is the whole feature.
public func roomsNeedsUpgrade(coreSha: String?, roomVersion: String?) -> Bool {
    guard let coreSha, let roomVersion else { return false }
    return coreSha != roomVersion
}

/// `upToDate` (`SatOverviewTab.kt:78`): both known and equal.
public func roomsUpToDate(coreSha: String?, roomVersion: String?) -> Bool {
    guard let coreSha, let roomVersion else { return false }
    return coreSha == roomVersion
}

/// `rebootNeeded(doc)` (`SatModels.kt:77`): the reason, and nil for a blank
/// one — which is a satellite that reports the field and has nothing to say.
public func roomsRebootNeeded(_ doc: RoomsHealthDoc?) -> String? {
    guard let reason = doc?.latest?.sample?.rebootNeeded,
          !reason.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
    return reason
}

/// `NetStepLine` (`SatModels.kt:84`).
public struct RoomsNetStepLine: Equatable, Sendable {
    public let word: String
    public let tone: HealthTone
    public let detail: String?
    public let hostname: String?
    public let early: String?

    public init(word: String, tone: HealthTone, detail: String?, hostname: String?, early: String? = nil) {
        self.word = word
        self.tone = tone
        self.detail = detail
        self.hostname = hostname
        self.early = early
    }
}

/// `netStepLine(doc)` (`SatModels.kt:92`), branch for branch.
///
/// **Nil when there is no sample, and nil when the sample has neither a state
/// nor a host name** — so a satellite that does not report the boot step
/// leaves the panel exactly as it was before the step existed. An unknown
/// state keeps its own word with an `idle` tone rather than being dropped: the
/// core's vocabulary is open, and inventing "unknown" would hide a state a
/// newer satellite started reporting.
public func roomsNetStepLine(_ doc: RoomsHealthDoc?) -> RoomsNetStepLine? {
    guard let sample = doc?.latest?.sample else { return nil }
    let step = sample.netStep
    let host = sample.hostname.flatMap { $0.trimmingCharacters(in: .whitespaces).isEmpty ? nil : $0 }
    let state = step?.state
    if state == nil && host == nil { return nil }
    let word: String
    let tone: HealthTone
    switch state {
    case "applied": (word, tone) = (RoomsStrings.netStepApplied, .ok)
    case "unchanged": (word, tone) = (RoomsStrings.netStepUnchanged, .ok)
    case "skipped": (word, tone) = (RoomsStrings.netStepSkipped, .warn)
    case "failed": (word, tone) = (RoomsStrings.netStepFailed, .err)
    case "not_run": (word, tone) = (RoomsStrings.netStepNotRun, .warn)
    case "not_installed": (word, tone) = (RoomsStrings.netStepNotInstalled, .idle)
    case "running": (word, tone) = (RoomsStrings.netStepRunning, .idle)
    default: (word, tone) = (state ?? "", .idle)
    }
    return RoomsNetStepLine(
        word: word,
        tone: tone,
        detail: step?.detail.flatMap { $0.trimmingCharacters(in: .whitespaces).isEmpty ? nil : $0 },
        hostname: host,
        early: step?.early.flatMap { $0.trimmingCharacters(in: .whitespaces).isEmpty ? nil : $0 }
    )
}

/// The "unplug it" paragraph, or nil when no reboot is wanted
/// (`SatOverviewTab.kt:669-675`).
public func roomsRebootLine(_ doc: RoomsHealthDoc?) -> String? {
    guard let reason = roomsRebootNeeded(doc) else { return nil }
    let failed = roomsNetStepLine(doc)?.word == RoomsStrings.netStepFailed
    return RoomsStrings.rebootLine(reason: reason, netStepFailed: failed)
}

// MARK: - drop in

/// The rooms a drop-in from `room` could reach: **other** rooms that are
/// online, AEC-capable and not already in a call (`SatOverviewTab.kt:474`).
///
/// Kept as a rule although `start` is not drawn (DECISIONS): it is what the
/// "no eligible rooms" state is computed from, and it is the list a later
/// batch needs the day the core makes starting a drop-in device tier.
public func roomsPeers(for room: String, in rooms: [RoomsSatellite]) -> [RoomsSatellite] {
    rooms.filter { $0.roomID != room && $0.online && $0.fullDuplex && $0.inCallWith == nil }
}

/// What the drop-in section is in: a call, a mic that cannot do it, or
/// nothing to say (`SatOverviewTab.kt:516-527`).
public enum RoomsDropInState: Equatable, Sendable {
    /// In a call with this peer. **The only state with a control**: `hang up`,
    /// which is device tier (`domovoi/main.py:2718-2721`).
    case inCall(String)
    /// No echo-cancelling array, so this room cannot do drop-in at all.
    case noAec
    /// It could take a call; starting one needs the dashboard.
    case idle
}

public func roomsDropInState(_ room: RoomsSatellite) -> RoomsDropInState {
    if let peer = room.inCallWith { return .inCall(peer) }
    return room.fullDuplex ? .idle : .noAec
}

/// Whether "drop in from this phone" can do what it says (D54).
///
/// Android's own enable term, field for field: `val enabled = s.online &&
/// s.full_duplex && s.in_call_with == null` (`PhoneDropinCall.kt:81`), plus
/// one more this phone knows and the roster does not.
///
/// ### Why `inCallWith == nil` is in here, and why it was the bug
///
/// The core answers `target_busy` for a room that is already in a drop-in
/// with somebody else (`domovoi/phone_dropin.py:78-79`), so a button offered
/// then can only fail — Kamron's no-misleading-controls rule exactly. Review
/// round 1 found this drawn enabled: the row is deliberately drawn for every
/// state of the room — once, outside the state switch, so that the roster
/// reporting *this* call a second after it starts cannot unmount the sheet
/// (``RoomsOverviewView``'s drop-in note) — and the only term gating it read
/// this phone's own call state, which says nothing about a call between the
/// room and a third party.
///
/// ### Why `callIsBusy` is in here as well, and Android has no equivalent
///
/// The roster reports this phone's own call about a second after it starts,
/// so for that second `inCallWith` is still nil while a call is live. Android
/// does not need the extra term because its row is recomposed from a state
/// flow it also drives; here the honest answer is to ask both — the room's
/// roster row, and whether this phone is in a call at all
/// (``DropInCallModel/State/isBusy``).
///
/// `fullDuplex` is asked rather than left to ``roomsDropInState(_:)``, and it
/// has to be: the row is drawn in the `.noAec` state as well (Android
/// composes its button after the `!s.full_duplex` arm too,
/// `SatOverviewTab.kt:564`), and a room that cannot do two-way audio gets
/// `target_no_aec` (`phone_dropin.py:84-85`). The `.inCall` state is reached
/// for such a room too, since a peer is checked first.
public func roomsPhoneDropInEnabled(_ room: RoomsSatellite, callIsBusy: Bool) -> Bool {
    room.online && room.fullDuplex && room.inCallWith == nil && !callIsBusy
}

// MARK: - sessions and turns

/// A session's length, from its first to its last activity
/// (`SatHistoryTabs.kt:86-90`).
///
/// **Zero** when either stamp is unreadable, which is the Kotlin's `else 0.0`
/// — `fmtDur(0)` is `0:00`, and the row then says the session is new rather
/// than printing an em dash where a duration belongs.
public func roomsSessionDurationSec(startedAt: String?, lastActivity: String?) -> Double {
    guard let a = parseInstant(startedAt), let b = parseInstant(lastActivity) else { return 0 }
    return max(0, b.timeIntervalSince(a))
}

/// A session's second line: `3fa85f64 · 1:12 · person #4`
/// (`SatHistoryTabs.kt:114-117`). The person is left off when the session was
/// never attributed to one.
public func roomsSessionSubtitle(_ session: RoomsSession) -> String {
    let id = String((session.id ?? "").prefix(8))
    let duration = fmtDur(roomsSessionDurationSec(startedAt: session.startedAt, lastActivity: session.lastActivity))
    return "\(id) · \(duration)" + (session.personID.map { RoomsStrings.person($0) } ?? "")
}

/// The turns of one session, by `session_id` (`SatHistoryTabs.kt:80`).
public func roomsTurns(of session: RoomsSession, in turns: [RoomsTurn]) -> [RoomsTurn] {
    turns.filter { $0.sessionID == session.id }
}

/// The assistant's answer as a turn card shows it: whole when it is short or
/// expanded, else **130 characters with the trailing space trimmed and an
/// ellipsis**, and only when the text is longer than **140**
/// (`SatHistoryTabs.kt:155-157`).
///
/// The two numbers are different on purpose: a 141-character answer collapses
/// to 130 plus `…`, so expanding it always reveals something. Counting is in
/// Swift `Character`s where Kotlin counts UTF-16 units, so a 140-emoji answer
/// would collapse here and not there; the alternative is counting
/// `text.utf16.count` and cutting a grapheme in half, which is the worse of
/// the two.
public func roomsTurnText(_ text: String?, expanded: Bool) -> (shown: String, truncated: Bool) {
    let full = text ?? ""
    let long = full.count > 140
    guard long, !expanded else { return (full, long) }
    let cut = String(full.prefix(130))
    // `trimEnd()` — trailing whitespace only, so a cut that lands after a
    // space does not read as a gap before the ellipsis.
    let trimmed = String(cut.reversed().drop(while: { $0.isWhitespace }).reversed())
    return (trimmed + "…", true)
}

/// The handler pill: the handler's name, or `qa` for a turn the router never
/// matched (`SatHistoryTabs.kt:167`). The tone says which it was.
public func roomsHandlerPill(_ turn: RoomsTurn) -> (text: String, tone: HealthTone) {
    guard let handler = turn.matchedHandler else { return (RoomsStrings.qa, .idle) }
    return (handler, .brand)
}

/// The conversations tab's client-side search: a blank query keeps
/// everything, and a query is matched **case-insensitively against the user
/// and assistant text together** (`SatHistoryTabs.kt:214-217`).
///
/// `localizedCaseInsensitiveContains` and not `lowercased().contains`: Kotlin's
/// `ignoreCase = true` folds case per locale, and the Turkish dotless i is the
/// case where the two disagree.
public func roomsSearchTurns(_ turns: [RoomsTurn], query: String) -> [RoomsTurn] {
    let needle = query.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !needle.isEmpty else { return turns }
    return turns.filter {
        "\($0.userText ?? "") \($0.assistantText ?? "")".localizedCaseInsensitiveContains(needle)
    }
}

// MARK: - recently played

/// The server's tone slug → this app's tone (`toneForSlug`,
/// `CapabilityTones.kt:8`). An **open** vocabulary: anything unknown is
/// neutral, never an error.
public func roomsToneForSlug(_ slug: String?) -> HealthTone {
    switch slug {
    case "media": return .brand
    case "device": return .ok
    case "comms": return .warn
    default: return .idle          // "info", "neutral" and anything unknown
    }
}

/// A played row's source pill (`playSourceTone`, `SatHistoryTabs.kt:267`):
/// the core's own two sources keep the brand accent, everything else asks
/// `/api/capabilities`.
public func roomsSourceTone(_ source: String?, capabilities: Capabilities?) -> HealthTone {
    if source == "library" || source == "playlist" { return .brand }
    return roomsToneForSlug(capabilities?.toneFor(source))
}

/// What the toast says after an add (`SatHistoryTabs.kt:306-312`), in the
/// Kotlin's branch order — `already_in_library` before `already_downloading`
/// before the server's own `message`.
public func roomsAddToast(_ result: RoomsAddByUrlResult, title: String?) -> String {
    let label = title ?? RoomsStrings.trackFallback
    if result.alreadyInLibrary { return RoomsStrings.alreadyInLibrary(label) }
    if result.alreadyDownloading { return RoomsStrings.alreadyQueued(label) }
    if let message = result.message, !message.isEmpty { return message }
    return RoomsStrings.queuedForDownload(label)
}

// MARK: - timers

/// What a timer row may be called: its label, a reminder's words, `—`, and
/// **``RoomsStrings/timerWordsHidden`` whenever the server masked it**
/// (`satTimerLabel`, `SatModels.kt:190`).
///
/// Rule M1 is checked first and nothing else may put a name on a masked row:
/// the server held those words back on purpose (`satellites.py:1090`).
public func roomsTimerLabel(_ timer: RoomsTimer) -> String {
    if timer.masked { return RoomsStrings.timerWordsHidden }
    if let label = timer.label { return label }
    if timer.isReminder, let message = timer.message { return message }
    return RoomsStrings.dash
}

/// `timer` or `reminder` — the row's pill and the cancel toast's noun
/// (`SatHistoryTabs.kt:472`).
public func roomsTimerKind(_ timer: RoomsTimer) -> String {
    timer.isReminder ? RoomsStrings.reminderKind : RoomsStrings.timerKind
}

/// Seconds left, never below zero, and nil when the stamp is unreadable
/// (`SatHistoryTabs.kt:469`).
public func roomsRemainingSec(expiresAt: String?, nowMs: Int64) -> Int64? {
    guard let thenMs = isoMs(expiresAt) else { return nil }
    return max(0, (thenMs - nowMs) / 1000)
}

/// `satFmtRemaining` (`SatHistoryTabs.kt:425`): `1h 2m`, `3m 04s`, `45s`, and
/// an em dash for nothing. **The seconds are padded only in the minutes
/// branch**, which is the Kotlin's shape.
public func roomsFmtRemaining(_ seconds: Int64?) -> String {
    guard let seconds else { return RoomsStrings.dash }
    let h = seconds / 3600
    let m = (seconds % 3600) / 60
    let s = seconds % 60
    if h > 0 { return "\(h)h \(m)m" }
    if m > 0 { return "\(m)m \(String(format: "%02d", s))s" }
    return "\(s)s"
}

/// Under ten minutes, which is when the remaining column turns warn
/// (`SatHistoryTabs.kt:490`).
public func roomsTimerUrgent(_ seconds: Int64?) -> Bool {
    guard let seconds else { return false }
    return seconds < 600
}

// MARK: - quiet hours

/// `quietClock` (`SatQuietHours.kt:73`): `"7:05"` → `"07:05"`, and **nil when
/// it is not a 24-hour clock time**.
///
/// The regex is the Kotlin's, anchored whole: `^([01]?\d|2[0-3]):([0-5]\d)$`.
/// So `24:00`, `7:5` and `half seven` are all nil, and nil is what stops the
/// PUT — a screen that posted `7:5` would have the server decide what it
/// meant.
public func roomsQuietClock(_ text: String) -> String? {
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    let parts = trimmed.split(separator: ":", omittingEmptySubsequences: false)
    guard parts.count == 2 else { return nil }
    let hourText = String(parts[0])
    let minuteText = String(parts[1])
    guard (1...2).contains(hourText.count), hourText.allSatisfy(\.isASCII), hourText.allSatisfy(\.isNumber),
          minuteText.count == 2, minuteText.allSatisfy(\.isASCII), minuteText.allSatisfy(\.isNumber),
          let hour = Int(hourText), let minute = Int(minuteText),
          (0...23).contains(hour), (0...59).contains(minute) else { return nil }
    return String(format: "%02d", hour) + ":" + minuteText
}

/// The PUT body, or **nil when custom hours are not both clock times**
/// (`quietBody`, `SatQuietHours.kt:80`).
///
/// A dictionary rather than `Data`, so a test can assert what was asked for
/// without depending on JSON key order. ``RoomsApi/setQuietHours(_:room:body:)``
/// serialises it.
public func roomsQuietBody(mode: String, start: String, end: String) -> [String: String]? {
    switch mode {
    case "custom":
        guard let s = roomsQuietClock(start), let e = roomsQuietClock(end) else { return nil }
        return ["mode": mode, "start": s, "end": e]
    case "house", "off":
        return ["mode": mode]
    default:
        return nil
    }
}

// MARK: - the read-only config tab

/// The fields that are **not** in the Advanced section, and the ones that
/// are (`SatSettingsTab.kt:126-128`).
public func roomsConfigSplit(
    _ fields: [RoomsConfigField]
) -> (common: [RoomsConfigField], advanced: [RoomsConfigField]) {
    (fields.filter { $0.section != "advanced" }, fields.filter { $0.section == "advanced" })
}

/// Fields grouped by `group`, **in the order the server listed them**
/// (`SatSettingsTab.kt:272`), with `general` for a field that names none.
///
/// Kotlin's `groupBy` keeps first-appearance order and `Dictionary(grouping:)`
/// does not, so the grouping is done by hand: a settings tab whose sections
/// move between reads is one nobody can scan.
public func roomsConfigGroups(_ fields: [RoomsConfigField]) -> [(group: String, fields: [RoomsConfigField])] {
    var order: [String] = []
    var byGroup: [String: [RoomsConfigField]] = [:]
    for field in fields {
        let group = field.group ?? RoomsStrings.generalGroup
        if byGroup[group] == nil { order.append(group) }
        byGroup[group, default: []].append(field)
    }
    return order.map { ($0, byGroup[$0] ?? []) }
}

/// A field's value **as words**, for a row that cannot be edited.
///
/// Android renders a control per type — a switch, a dropdown, a numeric field
/// (`SatSettingsTab.kt:268-330`). None of those is drawn here (the PATCH is
/// admin), so each type becomes text instead: a bool is on or off, a choice
/// gets its `choice_labels` entry when the raw value is not words a person
/// would pick, a number carries its unit, and a field the room has not
/// reported is an em dash.
public func roomsConfigValueText(_ field: RoomsConfigField) -> String {
    guard let value = field.value, value != .null else { return RoomsStrings.valueUnknown }
    switch value {
    case .bool(let on):
        return on ? RoomsStrings.boolOn : RoomsStrings.boolOff
    case .string(let text):
        if field.type == "choice" { return field.choiceLabels?[text] ?? text }
        return text.isEmpty ? RoomsStrings.valueUnknown : text
    case .number(let number):
        let text = number == number.rounded() && abs(number) < 1e15
            ? String(Int64(number))
            : String(number)
        return field.unit.map { "\(text) \($0)" } ?? text
    case .array, .object, .null:
        // A shape no field type promises. Saying so beats printing Swift's
        // description of an enum case.
        return RoomsStrings.valueUnknown
    }
}
