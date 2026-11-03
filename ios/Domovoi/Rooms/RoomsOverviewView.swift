import DomovoiKit
import SwiftUI

/// The overview tab — `SatOverviewTab.kt:70`.
///
/// Eight sections in Android's order: when the room was last heard from and
/// what its Wi-Fi is doing, what it is playing, the five read rows, the
/// volume, the announce box, "Only reminders for this device", the drop-in
/// state, and maintenance.
///
/// ### What is not here, and why there is no control for it
///
/// * **`restart`** and **`upgrade`** (`satellites.py:1576-1581`, `:1618-1623`
///   — admin, and admin security). The two **pills** stay, because
///   ``roomsNeedsUpgrade(coreSha:roomVersion:)`` and
///   ``roomsRebootNeeded(_:)`` are facts computed from two device-tier reads,
///   and ``RoomsStrings/upgradeIsOnTheDashboard`` says where the buttons are.
///   Android's confirm dialogs, its press ledger, its six-minute upgrade clock
///   and its outcome sentences (`SatUpgrade.kt`) go with them.
/// * **Starting a drop-in**: the peer picker and `start`
///   (`SatOverviewTab.kt:467-560`). The core's `/v1/admin/dropin/start` is
///   admin *security* (`domovoi/main.py:2649-2658`), so from this phone it
///   could only fail. **`hang up` is drawn** — `/v1/admin/dropin/end` is
///   device tier (`main.py:2718-2721`).
/// * **"Drop in from this phone"** (`PhoneDropinRow`, `PhoneDropinCall.kt:61`):
///   permitted, deferred, no audio layer in this app at all.
///
/// Every write is ``RoomsModel``'s, never this view's: a section that holds
/// the request loses it when the tab changes under it.
struct RoomsOverviewView: View {

    let model: RoomsModel
    let reads: RoomsRoomModel
    let room: RoomsSatellite

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            lastSeenAndWifi
            nowPlaying
            readRows
            volume
            announce
            timerScope
            dropIn
            maintenance
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - last connected, and the Wi-Fi pair (`SatOverviewTab.kt:88-157`)

    private var lastSeenAndWifi: some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                RoomsSectionLabel(text: RoomsStrings.lastConnected)
                Text(roomsLastSeenText(room, nowMs: model.clockMs))
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                // The server's stamp as the server wrote it, said to be UTC
                // rather than converted: a stamp relabelled into the phone's
                // zone would disagree with the same row in the dashboard.
                Text(roomsRawStamp(room))
                    .font(.caption.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            wifiPair
        }
    }

    private var wifiPair: some View {
        let pair = roomsWifiOverview(room.wifi)
        return VStack(alignment: .leading, spacing: 2) {
            RoomsSectionLabel(text: RoomsStrings.wifiLabel)
            Text(pair.headline)
                .font(.subheadline)
                .foregroundStyle(pair.tone.inkColour)
                .fixedSize(horizontal: false, vertical: true)
            if !pair.detail.isEmpty {
                Text(pair.detail)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - now playing (`SatOverviewTab.kt:160-187`)

    @ViewBuilder
    private var nowPlaying: some View {
        VStack(alignment: .leading, spacing: 2) {
            RoomsSectionLabel(text: RoomsStrings.nowPlayingLabel)
            if roomsPlaying(room.nowPlaying) {
                let elapsed = roomsElapsedSec(room.nowPlaying, extraSec: model.extraSec)
                Text(songLine)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(roomsSongClock(elapsed: elapsed, duration: room.nowPlaying?.song?.durationSec))
                    .font(.caption.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text(RoomsStrings.nothingPlaying)
                    .font(.subheadline)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var songLine: String {
        let title = roomsSongTitle(room.nowPlaying)
        guard let artist = room.nowPlaying?.song?.artist, !artist.isEmpty else { return title }
        return title + " · " + artist
    }

    // MARK: - the five read rows (`SatOverviewTab.kt:194-205`)

    private var readRows: some View {
        VStack(alignment: .leading, spacing: 10) {
            RoomsReadRow(label: RoomsStrings.roomIDLabel, value: room.roomID, monospaced: true)
            RoomsReadRow(label: RoomsStrings.voiceLabel, value: roomsVoiceText(room))
            // A room that has never reported a SHA reads as an em dash and
            // **not** as "behind": ``roomsNeedsUpgrade(coreSha:roomVersion:)``
            // treats a null version as unknown, and so does this row.
            RoomsReadRow(
                label: RoomsStrings.versionLabel,
                value: room.version ?? RoomsStrings.dash,
                monospaced: true
            )
            RoomsReadRow(
                label: RoomsStrings.mpdPortsLabel,
                value: RoomsStrings.mpdPorts(control: room.mpdPorts?.control, http: room.mpdPorts?.http),
                monospaced: true
            )
            RoomsReadRow(
                label: RoomsStrings.streamLabel,
                value: room.nowPlaying?.streamURL
                    ?? RoomsStrings.streamFallback(http: room.mpdPorts?.http),
                monospaced: true
            )
        }
    }

    // MARK: - volume (`VolumeSection`, `SatOverviewTab.kt:236`)

    /// The slider and the two ±5 nudges.
    ///
    /// Both write through ``RoomsModel``: the slider debounces for 300 ms and
    /// the nudges commit at once, which is the difference between a gesture of
    /// dozens of values and one discrete value. A POST per slider step would
    /// both flood the room and arrive out of order, so the level that stuck
    /// would be whichever request the server handled last.
    private var volume: some View {
        let state = reads.volumeLevel(room)
        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                RoomsSectionLabel(text: RoomsStrings.volumeLabel)
                Text(state.known ? "\(state.level)%" : RoomsStrings.volumeUnknown)
                    .font(.subheadline.monospacedDigit())
                    .fixedSize(horizontal: false, vertical: true)
            }
            if typeSize.isAccessibilitySize {
                // A slider and two 44-point buttons do not share a line at
                // AX5 — and a slider squeezed to 40 points is one nobody can
                // set.
                VStack(alignment: .leading, spacing: 8) {
                    slider(state.level)
                    nudges
                }
            } else {
                HStack(spacing: 8) {
                    slider(state.level)
                    nudges
                }
            }
            Text(
                RoomsStrings.volumeHelp(
                    room: room.roomID,
                    levelKnown: state.known,
                    online: room.online
                )
            )
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func slider(_ level: Int) -> some View {
        Slider(
            value: Binding(
                get: { Double(level) },
                set: { model.volumeDragged(room, to: Int($0.rounded())) }
            ),
            in: 0...100,
            step: 1
        )
        .tint(Color.brand)
        // A slider has no words of its own, and `volume` alone would not say
        // which room's.
        .accessibilityLabel(RoomsStrings.volumeFor(room: room.roomID))
        .accessibilityValue("\(level)%")
    }

    private var nudges: some View {
        HStack(spacing: 4) {
            nudge("speaker.minus", by: -5, label: RoomsStrings.quieter)
            nudge("speaker.plus", by: 5, label: RoomsStrings.louder)
        }
    }

    private func nudge(_ glyph: String, by delta: Int, label: String) -> some View {
        Button {
            model.volumeNudged(room, by: delta)
        } label: {
            Image(systemName: glyph)
                .font(.body)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        .accessibilityLabel(label)
    }

    // MARK: - announce to this room (`AnnounceSection`, `SatOverviewTab.kt:336`)

    /// The field and `send`.
    ///
    /// The toast is decided by **`announced_to`, not by the status code**
    /// (``RoomsModel/announce(to:)``): a 200 with this room missing from the
    /// list means the socket under the server's active-sessions map is dead,
    /// and ``RoomsStrings/acceptedButDidNotPlay(_:)`` is the sentence that
    /// says so. That is the whole reason the answer is read at all.
    private var announce: some View {
        VStack(alignment: .leading, spacing: 6) {
            RoomsSectionLabel(text: RoomsStrings.announceTo(room: room.roomID))
            if typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 8) {
                    announceField
                    announceSend
                }
            } else {
                HStack(alignment: .center, spacing: 8) {
                    announceField
                    announceSend
                }
            }
        }
    }

    private var announceField: some View {
        TextField(
            RoomsStrings.announcePlaceholder(room: room.roomID),
            text: Binding(get: { reads.announceDraft }, set: { reads.announceDraft = $0 }),
            axis: .vertical
        )
        .lineLimit(1...)
        .font(.subheadline)
        .padding(.horizontal, 8)
        .padding(.vertical, 10)
        .frame(minHeight: 44)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 8))
        .overlay {
            RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary)
        }
        .accessibilityLabel(RoomsStrings.announceTo(room: room.roomID))
        // **No `submitLabel` and no `onSubmit`.** A vertical-axis field's
        // Return key makes a newline and `onSubmit` never fires, so a key
        // labelled `send` would lie — the defect measured in the Chat composer
        // and in Home's announce box on 2026-11-02. ``announceSend`` is the
        // control that announces and it is on screen at every width.
    }

    private var announceSend: some View {
        Button(RoomsStrings.send) {
            model.announce(to: room)
        }
        .buttonStyle(.borderedProminent)
        .tint(Color.brand)
        .foregroundStyle(Color.brandFg)
        .font(.subheadline)
        .fixedSize(horizontal: false, vertical: true)
        .frame(minHeight: 44)
        .contentShape(.rect)
        // Disabled only while this room's own write is in flight. An empty
        // message and an offline room are **toasts**, not a greyed button:
        // "explicit feedback on the no-op branches — a silent bail reads as a
        // broken button" (the rule Home's announce follows too).
        .disabled(reads.busy != nil)
    }

    // MARK: - "Only reminders for this device" (`SatOverviewTab.kt:407`)

    private var timerScope: some View {
        VStack(alignment: .leading, spacing: 6) {
            RoomsSectionLabel(text: RoomsStrings.timersAndRemindersLabel)
            Toggle(
                RoomsStrings.timerScopeLabel,
                isOn: Binding(
                    get: { reads.timerScopeOn(room) },
                    set: { model.setTimerScope(room, on: $0) }
                )
            )
            .tint(Color.brand)
            .disabled(reads.busy != nil)
            .fixedSize(horizontal: false, vertical: true)
            Text(RoomsStrings.timerScopeHelp)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - drop in (`DropInSection`, `SatOverviewTab.kt:467`)

    /// The state, `hang up` when there is a call, and one line saying where
    /// starting one is done.
    @ViewBuilder
    private var dropIn: some View {
        VStack(alignment: .leading, spacing: 6) {
            RoomsSectionLabel(text: RoomsStrings.dropInLabel)
            switch roomsDropInState(room) {
            case .inCall(let peer):
                Text(RoomsStrings.inCallWith(peer))
                    .font(.subheadline)
                    .foregroundStyle(Color.warnInk)
                    .fixedSize(horizontal: false, vertical: true)
                Button(RoomsStrings.hangUp) {
                    model.hangUp(room)
                }
                .buttonStyle(.bordered)
                .tint(Color.errInk)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .frame(minHeight: 44)
                .contentShape(.rect)
                Text(RoomsStrings.dropInHelp)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            case .noAec:
                // A fact about the hardware, not a refusal of this phone's:
                // this room cannot do drop-in at all.
                Text(RoomsStrings.noAecMic)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            case .idle:
                Text(RoomsStrings.dropInStartIsOnTheDashboard)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - maintenance, as facts (`MaintenanceSection`, `SatOverviewTab.kt:570`)

    private var maintenance: some View {
        let health = reads.health.data
        let needsUpgrade = roomsNeedsUpgrade(coreSha: model.coreSha, roomVersion: room.version)
        let reboot = roomsRebootLine(health)
        let step = roomsNetStepLine(health)
        return VStack(alignment: .leading, spacing: 8) {
            RoomsSectionLabel(text: RoomsStrings.maintenanceLabel)
            if needsUpgrade || reboot != nil {
                // Wrapping rather than a row: two pills and a long room name
                // do not fit one line at an accessibility size.
                VStack(alignment: .leading, spacing: 4) {
                    if needsUpgrade {
                        HomeTonePill(label: RoomsStrings.needsUpgrade, tone: .warn)
                    }
                    if reboot != nil {
                        HomeTonePill(label: RoomsStrings.rebootNeeded, tone: .warn)
                    }
                }
            }
            if room.upgrade?.running == true {
                Text(RoomsStrings.upgradeUnderWay)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let reboot {
                Text(reboot)
                    .font(.footnote)
                    .foregroundStyle(Color.warnInk)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let step {
                VStack(alignment: .leading, spacing: 2) {
                    RoomsSectionLabel(text: RoomsStrings.bootNetworkStep)
                    Text(step.word)
                        .font(.subheadline)
                        .foregroundStyle(step.tone.inkColour)
                        .fixedSize(horizontal: false, vertical: true)
                    if let detail = step.detail {
                        Text(detail)
                            .font(.caption)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let early = step.early {
                        Text(early)
                            .font(.caption)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let host = step.hostname {
                        Text(RoomsStrings.hostName(host))
                            .font(.caption.monospaced())
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            // The one line that replaces Android's two buttons.
            Text(RoomsStrings.upgradeIsOnTheDashboard)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
