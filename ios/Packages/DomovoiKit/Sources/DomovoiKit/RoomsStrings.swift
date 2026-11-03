import Foundation

/// Every sentence the Rooms screen says, ported **word for word** from
/// `ui/screens/satellites/` (read tonight in this worktree). The few that are
/// not Android's are marked `iOS only` here and written down with their reason
/// in `design-notes/ios/overnight/rooms/IOS-ONLY-STRINGS.md`.
///
/// Pieces rather than format strings wherever two numbers or a room name sit
/// in one line, which is the shape `HomeToastStrings` settled on: a test can
/// then pin the words without pinning the arithmetic.
public enum RoomsStrings {

    // MARK: - the screen

    /// `PageHeader("Rooms", …)` (`SatellitesScreen.kt:148`). The Settings
    /// section's own header says it too, which is why `embedded` drops this
    /// one and keeps the summary.
    public static let title = "Rooms"

    /// `"$onlineCount online · $offlineCount offline · ${sats.size} provisioned"`
    /// (`SatellitesScreen.kt:143`). **U+00B7 with one space each side.**
    public static func summary(online: Int, offline: Int, provisioned: Int) -> String {
        "\(online) online · \(offline) offline · \(provisioned) provisioned"
    }

    /// `EmptyState(…)` (`SatellitesScreen.kt:158`).
    public static let noRoomsYet = "no rooms set up yet"
    public static let noRoomsHint = "set up a Raspberry Pi room, or use a spare phone as a room"

    // MARK: - one room's card

    public static let online = "online"
    public static let offline = "offline"

    /// `SatellitesScreen.kt:259`.
    public static let noMusic = "no music"

    /// `SatellitesScreen.kt:289` — what an online room's footer says instead
    /// of a time.
    public static let activeNow = "active now"

    /// `wifiLinkText` (`SatModels.kt:295-296`).
    public static let setupHotspot = "setup hotspot"
    public static let notOnTheHouseWifi = "not on the house Wi-Fi"

    /// The em dash a missing reading reads as, U+2014 (`SatModels.kt:293`).
    public static let dash = "—"

    /// `wifiSignalWord` (`SatModels.kt:283`).
    public static let signalGood = "good"
    public static let signalFair = "fair"
    public static let signalWeak = "weak"

    // MARK: - the broadcast card

    /// `SatellitesScreen.kt:360`.
    public static let broadcastHeading = "Broadcast"
    public static let broadcastHelp = "Speaks in every connected room at once."
    public static let broadcastPlaceholder = "dinner's ready"
    public static let broadcastButton = "broadcast"

    /// **iOS only.** The field's accessibility label.
    ///
    /// The placeholder vanishes the moment anything is typed, so it cannot be
    /// the label — and the two sentences already on the card
    /// (``broadcastHeading`` and ``broadcastHelp``) are each drawn as text of
    /// their own, so either as a label would make two elements answer to one
    /// name. Android needs none of this: its field is inside a labelled row.
    public static let broadcastFieldLabel = "broadcast to every room"

    /// `"$onlineCount online"` on the card's pill (`SatellitesScreen.kt:366`).
    public static func onlinePill(_ count: Int) -> String { "\(count) online" }

    // MARK: - the seven tabs (`SatelliteDetail.kt:60`)

    public static let tabOverview = "overview"
    public static let tabSessions = "sessions"
    public static let tabConversations = "conversations"
    public static let tabRecentlyPlayed = "recently played"
    public static let tabNotes = "notes"
    public static let tabTimers = "timers"
    public static let tabSettings = "settings"

    /// The close control's label. Android's is an icon with no words
    /// (`SatelliteDetail.kt:129`); every icon-only control here carries an
    /// accessibility label, so this is **iOS only**.
    public static let closeRoom = "close this room"

    // MARK: - the overview tab

    /// `SectionLabel("last connected")` (`SatOverviewTab.kt:89`) — kept as
    /// Android's words although this port reads `last_seen_at`: the label
    /// names the row, and "last seen" would be a second wording for a line
    /// the dashboard also calls "last connected". See DECISIONS.
    public static let lastConnected = "last connected"

    public static let wifiLabel = "wi-fi"

    /// `SatOverviewTab.kt:116`.
    public static let hostingItsSetupNetwork = "hosting its setup network, not on the house Wi-Fi"

    /// `SatOverviewTab.kt:149`.
    public static let noSignal = "no signal"

    public static let nowPlayingLabel = "now playing"

    /// `SatOverviewTab.kt:182`.
    public static let nothingPlaying = "nothing playing"

    /// The five read rows (`SatOverviewTab.kt:194-205`).
    public static let roomIDLabel = "room id"
    public static let voiceLabel = "voice"
    public static let registryDefault = "registry default"
    public static let versionLabel = "version"
    public static let mpdPortsLabel = "mpd ports"
    public static let streamLabel = "stream"

    /// `"control :${…} · http :${…}"` (`SatOverviewTab.kt:198`).
    public static func mpdPorts(control: Int?, http: Int?) -> String {
        "control :\(control.map(String.init) ?? dash) · http :\(http.map(String.init) ?? dash)"
    }

    /// `"(http :${…})"` — what the stream row says when there is no stream
    /// URL (`SatOverviewTab.kt:203`).
    public static func streamFallback(http: Int?) -> String {
        "(http :\(http.map(String.init) ?? dash))"
    }

    /// `" UTC"` — the raw stamp's suffix (`SatOverviewTab.kt:99`).
    public static let utcSuffix = " UTC"

    // MARK: - volume

    public static let volumeLabel = "volume"

    /// What the level reads as before the server has reported one
    /// (`SatOverviewTab.kt:294`).
    public static let volumeUnknown = "unknown"

    /// **iOS only.** A `Slider` has no words of its own, and Android's has no
    /// content description either (`SatOverviewTab.kt:296`) — which on a
    /// screen with one slider per room would read out as nothing at all. The
    /// help line underneath is a sentence, not a label, so this is the short
    /// one: `volume in kitchen`.
    public static func volumeFor(room: String) -> String { "volume in \(room)" }

    /// The two nudges' content descriptions (`SatOverviewTab.kt:305,318`).
    public static let quieter = "quieter"
    public static let louder = "louder"

    /// `SatOverviewTab.kt:325` — and its second clause, which appears only for
    /// an online room whose level the server has not reported.
    public static func volumeHelp(room: String, levelKnown: Bool, online: Bool) -> String {
        "master output — scales speech and music on \(room)"
            + (!levelKnown && online ? " · move to set (this board may not report its level)" : "")
    }

    /// `"${s.room_id} volume set to $level%"` (`SatOverviewTab.kt:264`).
    public static func volumeSet(room: String, level: Int) -> String {
        "\(room) volume set to \(level)%"
    }

    // MARK: - announce

    /// `SectionLabel("announce to ${s.room_id}")` (`SatOverviewTab.kt:371`).
    public static func announceTo(room: String) -> String { "announce to \(room)" }

    /// `SatOverviewTab.kt:379`.
    public static func announcePlaceholder(room: String) -> String { "speak in the \(room) room…" }

    public static let send = "send"

    /// `SatOverviewTab.kt:345` — the same words Home's broadcast uses, and
    /// deliberately the same constant: ``HomeToastStrings/typeAMessageFirst``.
    public static let typeAMessageFirst = HomeToastStrings.typeAMessageFirst

    /// `"${s.room_id} is offline"` (`SatOverviewTab.kt:349`).
    public static func roomIsOffline(_ room: String) -> String { "\(room) is offline" }

    /// `"announced to ${s.room_id}"` (`SatOverviewTab.kt:363`).
    public static func announcedTo(_ room: String) -> String { "announced to \(room)" }

    /// `SatOverviewTab.kt:365`. **U+2014 EM DASH.** This is the sentence the
    /// whole `announced_to` reading exists for: a 200 can still mean the
    /// socket under the server's active-sessions map is dead.
    public static func acceptedButDidNotPlay(_ room: String) -> String {
        "\(room) accepted the request but didn't play — connection may be dead"
    }

    // MARK: - timers & reminders scope

    public static let timersAndRemindersLabel = "timers & reminders"

    /// `TIMER_SCOPE_LABEL` (`SatModels.kt:316`).
    public static let timerScopeLabel = "Only reminders for this device"

    /// `TIMER_SCOPE_HELP` (`SatModels.kt:318-322`), one string, three
    /// sentences, as the Kotlin concatenates it.
    public static let timerScopeHelp =
        "Covers timers and reminders. Off: this room also announces the ones set in other rooms "
        + "and says which room they came from. On: it announces only the ones set in this room. "
        + "The room a timer or reminder was set in always announces it."

    /// `timerScopeToast` (`SatModels.kt:326`).
    public static func timerScopeToast(room: String, on: Bool) -> String {
        on
            ? "\(room) now announces only its own timers and reminders"
            : "\(room) now announces timers and reminders from every room"
    }

    // MARK: - drop in

    public static let dropInLabel = "drop in"

    /// `"in call with $inCallWith"` (`SatOverviewTab.kt:518`).
    public static func inCallWith(_ peer: String) -> String { "in call with \(peer)" }

    public static let hangUp = "hang up"

    /// `"hung up ${s.room_id}"` (`SatOverviewTab.kt:505`).
    public static func hungUp(_ room: String) -> String { "hung up \(room)" }

    /// `SatOverviewTab.kt:525`.
    public static let noAecMic =
        "this room's mic can't do drop-in — it needs an echo-cancelling array (XVF3800)"

    /// `SatOverviewTab.kt:551` — the escaped quotes are part of it.
    public static let dropInHelp = "live two-way audio — say \"hang up\" or use the button to end"

    /// **iOS only.** Android draws a peer picker and a `start` button; the
    /// core's `/v1/admin/dropin/start` is admin *security* tier
    /// (`domovoi/main.py:2649-2658`) and the web hop forwards this phone's
    /// household token, so from here it can only fail. The fact is kept, the
    /// control is not.
    public static let dropInStartIsOnTheDashboard =
        "starting a drop-in needs the dashboard — this phone can end a call but not begin one"

    // MARK: - maintenance (facts only: both buttons are admin)

    public static let maintenanceLabel = "maintenance"

    /// The two pills (`SatOverviewTab.kt:665-667`).
    public static let needsUpgrade = "needs upgrade"
    public static let rebootNeeded = "reboot needed"

    /// `SatOverviewTab.kt:670-673`: the first clause depends on whether the
    /// boot network step failed, and the reason the satellite gave is in
    /// brackets at the end.
    public static func rebootLine(reason: String, netStepFailed: Bool) -> String {
        (netStepFailed
            ? "Its network settings did not all apply at this boot; a reboot tries again. "
            : "Upgraded, but not rebooted yet: its network settings are applied at boot. ")
            + "Unplug it for a few seconds and plug it back in. (\(reason))"
    }

    /// `SectionLabel("boot network step")` (`SatOverviewTab.kt:688`).
    public static let bootNetworkStep = "boot network step"

    /// `"host name ${netStep.hostname}"` (`SatOverviewTab.kt:701`).
    public static func hostName(_ name: String) -> String { "host name \(name)" }

    /// `netStepLine`'s seven words (`SatModels.kt:95-104`).
    public static let netStepApplied = "applied"
    public static let netStepUnchanged = "nothing to change"
    public static let netStepSkipped = "skipped"
    public static let netStepFailed = "failed"
    public static let netStepNotRun = "not run"
    public static let netStepNotInstalled = "not installed"
    public static let netStepRunning = "running"

    /// **iOS only.** Android's `restart` and `upgrade` buttons and their
    /// confirm dialogs are not ported: `…/restart` is admin
    /// (`satellites.py:1576-1581`) and `…/upgrade` is admin security
    /// (`:1618-1623`), so both 401 every time from a phone. The pills above
    /// stay — they are facts from two device-tier reads — and this one line
    /// says where the buttons are.
    public static let upgradeIsOnTheDashboard =
        "restarting and upgrading a room are done from the dashboard on a computer"

    /// **iOS only.** What the panel says while the server's own record shows
    /// an upgrade running, so a room that is offline on purpose does not read
    /// as a room that has fallen off the Wi-Fi.
    public static let upgradeUnderWay =
        "it is syncing new code from the server and restarts itself when it lands"

    // MARK: - sessions

    /// `EmptyState("no sessions in this room")` (`SatHistoryTabs.kt:77`).
    public static let noSessions = "no sessions in this room"

    /// `SatHistoryTabs.kt:135`.
    public static let noTurnsRecorded = "no turns recorded"

    /// `" · person #$it"` (`SatHistoryTabs.kt:116`).
    public static func person(_ id: Int64) -> String { " · person #\(id)" }

    /// `"${session.intent_count} turn" + (if == 1) "" else "s"`
    /// (`SatHistoryTabs.kt:122`) — through ``plural(_:_:_:)``, so one turn is
    /// `1 turn`.
    public static func turns(_ count: Int) -> String { plural(count, "turn") }

    /// `"last ${relTime(...)}"` (`SatHistoryTabs.kt:127`).
    public static func last(_ rel: String) -> String { "last \(rel)" }

    /// A turn with no handler reads `qa` (`SatHistoryTabs.kt:167`).
    public static let qa = "qa"

    public static let more = "more"
    public static let less = "less"

    // MARK: - conversations

    /// `SatHistoryTabs.kt:228`.
    public static let searchThisRoom = "search this room…"

    /// `"${filtered.size} of ${all.size}"` (`SatHistoryTabs.kt:241`).
    public static func countOf(_ shown: Int, _ total: Int) -> String { "\(shown) of \(total)" }

    /// `SatHistoryTabs.kt:248-250`. The curly quotes are the Kotlin's
    /// (U+201C / U+201D).
    public static let nothingSaidInThisRoom = "nothing said in this room"
    public static let roomHasBeenQuiet = "this room has been quiet"
    public static func searchHint(_ needle: String) -> String { "q = “\(needle)”" }

    // MARK: - recently played

    /// `SatHistoryTabs.kt:322-324`.
    public static let nothingPlayedYet = "nothing played in this room yet"
    public static let nothingPlayedHint = "play some music and it'll show up here"

    public static let add = "add"
    public static let queued = "queued"

    /// The four outcomes of an add (`SatHistoryTabs.kt:306-312`); `track` is
    /// the fallback for a row with no title.
    public static let trackFallback = "track"
    public static func alreadyInLibrary(_ label: String) -> String { "already in library: \(label)" }
    public static func alreadyQueued(_ label: String) -> String { "already queued: \(label)" }
    public static func queuedForDownload(_ label: String) -> String { "queued for download: \(label)" }

    // MARK: - notes

    /// `SatHistoryTabs.kt:393-395`. **The em dash and the ellipsis are part of
    /// the hint**: it is the sentence a person says out loud.
    public static let noNotes = "no notes from this room"
    public static let noNotesHint = "say: domovoi, jot this down — …"

    // MARK: - timers

    /// `SatHistoryTabs.kt:466`.
    public static let noTimers = "no active timers or reminders"

    /// `SAT_TIMER_WORDS_HIDDEN` (`SatModels.kt:186`) — rule M1. Nothing else
    /// may put a name on a masked row.
    public static let timerWordsHidden = "reminder (words hidden)"

    public static let timerKind = "timer"
    public static let reminderKind = "reminder"

    /// `"fires ${relTime(t.expires_at)}"` (`SatHistoryTabs.kt:483`).
    public static func fires(_ rel: String) -> String { "fires \(rel)" }

    public static let cancel = "cancel"

    /// `"cancelled ${kind} #${t.id}"` (`SatHistoryTabs.kt:443`).
    public static func cancelled(kind: String, id: Int64) -> String { "cancelled \(kind) #\(id)" }

    /// **iOS only**, and the same judgement ``CancelOutcome/gone`` records for
    /// Home's timers: a 404 means it fired, or somebody else cancelled it,
    /// between the read and the tap — which is not a failure to report as one.
    /// Android has no branch for it (`SatHistoryTabs.kt:447` toasts
    /// "cancel failed: HTTP 404").
    public static let timerAlreadyFinished = HomeToastStrings.alreadyFinished

    // MARK: - quiet hours

    /// `SectionLabel("quiet hours")` (`SatQuietHours.kt:130`).
    public static let quietHoursLabel = "quiet hours"

    /// `QUIET_CHOICES` (`SatQuietHours.kt:58-62`), in order.
    public static let quietModes: [(value: String, label: String)] = [
        ("house", "Same as the house"),
        ("custom", "Custom hours"),
        ("off", "Off for this room"),
    ]

    /// `QUIET_HELP` (`SatQuietHours.kt:64-66`).
    public static let quietHelp =
        "Quiet hours: Domovoi won't speak announcements in this room, like low battery or printer "
        + "problems. Alarms, timers and reminders still ring."

    /// The two fields' labels and the button (`SatQuietHours.kt:166-176`).
    public static let quietFrom = "from"
    public static let quietTo = "to"
    public static let quietSave = "Save"

    /// What a time that is not a 24-hour clock time gets
    /// (`SatQuietHours.kt:113`).
    public static let timesLookLike = "times look like 22:00"

    /// `quietToast` (`SatQuietHours.kt:90-94`). The custom branch's dash is an
    /// **en dash, U+2013**, between two normalised clock times.
    public static func quietToast(room: String, mode: String, start: String, end: String) -> String {
        switch mode {
        case "off": return "\(room) has no quiet hours now"
        case "custom":
            return "\(room) keeps quiet \(roomsQuietClock(start) ?? start)–\(roomsQuietClock(end) ?? end)"
        default: return "\(room) follows the house's quiet hours"
        }
    }

    // MARK: - the settings tab, which is read-only here

    /// Android's empty state is `"settings unavailable"` with
    /// `"the room must be online to edit its config"`
    /// (`SatSettingsTab.kt:120-123`). The heading is kept; **the hint is iOS
    /// only** — nothing here edits, so "to edit its config" would promise a
    /// control this screen does not have.
    public static let settingsUnavailable = "settings unavailable"
    public static let settingsNeedsOnlineRoom = "the room must be online to report its settings"

    /// **iOS only**, in place of Android's "Saving rewrites the Pi's
    /// config.toml and restarts the room device to apply"
    /// (`SatSettingsTab.kt:137-139`): the PATCH is admin
    /// (`satellites.py:1712-1716`), so these fields are shown and not offered.
    public static let configIsReadOnly =
        "what this room is set to. Changing any of it is done from the dashboard on a computer."

    /// The group a field with no group falls into (`SatSettingsTab.kt:273`).
    public static let generalGroup = "general"

    /// The Advanced disclosure's title and its warning
    /// (`SatSettingsTab.kt`, read tonight).
    public static let advancedGroup = "advanced"

    /// The tier pill (`SatSettingsTab.kt:307`).
    public static let restartTier = "restart"

    /// What a field whose value the room has not reported reads as.
    public static let valueUnknown = dash

    /// **iOS only.** A `bool` field is a `Switch` on Android
    /// (`SatSettingsTab.kt:311`); nothing here is editable, so the state is
    /// read as a word instead. Lower case, like the rest of this screen.
    public static let boolOn = "on"
    public static let boolOff = "off"
}
