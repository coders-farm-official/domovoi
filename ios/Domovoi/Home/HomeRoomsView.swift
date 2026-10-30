import DomovoiKit
import SwiftUI

/// Home's **rooms** section — a port of `HomeRooms` (`HomeSections.kt:547`),
/// map §3.4, with §5's four states.
///
/// **This is the only section with no card title**: a plain `rooms` label row
/// with the stop-all button at its right, and then one card per group
/// (`HomeSections.kt:570`).
///
/// Every decision is the package's: the order and the grouping
/// (``sortRooms(_:)``, ``groupRooms(_:)``), what is playing
/// (``roomSong(_:)`` — the 2026-10-11 bug's fix), where it has got to
/// (``roomElapsedSec(_:stale:sinceReadSec:)``, with the clamp that stopped
/// `0:14 / 0:13`), the one pill (``roomPill(_:stale:)``), the second line
/// (``roomSecondLine(_:)``), the chips (``weakWifi(_:)``, ``kioskDead(_:)``,
/// ``inCallChip(_:)``), which transport buttons exist
/// (``roomTransport(_:stale:)``) and which rooms `stop all` would stop
/// (``stopAllCandidates(_:stale:)``, ``stopAllShown(candidates:stopping:)``).
///
/// ### nil is not empty, and that is most of this file's state handling
///
/// Four visibly different things (map §5):
///
/// - the rooms read has **not answered** → a 72-point grey block. A skeleton
///   is a promise, and this one is kept by a read that is in flight.
/// - it answered and **failed** → `rooms unavailable · the database isn't
///   answering` when health says the DB is down, else `couldn't load rooms`.
///   **This is the normal unpaired-and-dismissed state, not an exotic one**:
///   `/api/satellites` is device-read, so a phone that tapped `not now` on
///   the pairing screen sees exactly this.
/// - it answered **empty** → `no rooms yet` with a `set up a room` link.
/// - it answered with rooms → the groups.
///
/// A read that has succeeded once and then failed keeps its rooms and says
/// nothing (``ApiRead``'s rule, map §10.4), so the failure branch is only
/// ever a read that has *never* loaded. That is deliberate and is why there is
/// no retry button anywhere on Home (map §5.3).
///
/// ### What a person has to confirm on a screen
///
/// * that a room row at AX5 wraps rather than clipping — the row reflows, and
///   it is the busiest row in the app (a title, a pill, a song, a progress
///   bar, two times, up to four chips and two buttons);
/// * that the transport buttons are 44-point targets in fact;
/// * that `alpha 0.6` on an offline room leaves its text legible — it is
///   applied to the whole row, as Android applies it, so it dims text that
///   already passed its contrast check at full opacity. **The one thing on
///   this screen most likely to fail Apple's audit**, and it is Android's
///   design rather than a choice made here.
struct HomeRoomsView: View {

    /// ``HomeModel/roomsForDisplay`` — the read with the Wi-Fi push merged in.
    let rooms: [HomeRoom]

    /// Whether the rooms read has answered at all (``ApiRead/answered``) and
    /// whether it holds data. **Both**, because they are different facts and
    /// §5's table is about exactly that difference.
    let answered: Bool
    let hasData: Bool

    /// `health.db_reachable == false`, which chooses between the two failure
    /// texts.
    let dbDown: Bool

    /// `coreDown` — what the server said last, not what is true now.
    let stale: Bool

    /// Seconds since the rooms read landed
    /// (``HomeModel/sinceRoomsReadSec``), which is what advances a playing
    /// room's position between reads.
    let sinceReadSec: Double

    /// ``HomeModel/nowMs`` — the **phone's** clock, for `last seen 2h ago`.
    /// The timers card uses the server's; these are different facts.
    let nowMs: Int64

    /// Seconds to each room's soonest timer (``timerLeftByRoom(_:nowMs:)``),
    /// measured on the **server's** clock by the caller.
    let timerLeft: [String: Int64]

    /// `stop all`: armed, in flight, and the two actions.
    ///
    /// **The arm is passed in rather than held here, and that is 3b's
    /// decision** — see ``HomeView``. Android's lives inside the button, so
    /// scrolling the section away disarms it; a two-tap confirm that silently
    /// forgets the first tap is a control that misleads about its own state.
    ///
    /// The value itself, not a boolean, so the words come from
    /// ``StopAllArm/label(count:busy:nowMs:)`` and the "is it still armed"
    /// test is the package's four-second window rather than this view's idea
    /// of it.
    let arm: StopAllArm
    let stoppingAll: Bool
    let onStopAll: () -> Void

    /// A transport verb was tapped. ``HomeView`` owns the request and the
    /// per-room busy mark, for the reason the announce send is hoisted: a row
    /// holding the `Task` loses the request when the read that follows it
    /// reorders the list.
    let onTransport: (HomeRoom, String) -> Void

    /// Which rooms have a write in flight — the buttons are disabled.
    let busy: (String) -> Bool

    /// A room's left side, and `set up a room`, both go to the same place:
    /// Android's `navigate(Route.Satellites)`, which the shell turns into
    /// Settings → Rooms. **The room id is passed and thrown away** on both
    /// platforms (`HomeScreen.kt:429`): there is no deep link to one room.
    let onOpenRooms: () -> Void

    @Environment(\.dynamicTypeSize) private var typeSize

    private var candidates: [String] { stopAllCandidates(rooms, stale: stale) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// The label row: `rooms`, and `stop all` at its right.
    ///
    /// The button is drawn only for **two or more** playing rooms, or while a
    /// batch is in flight (``stopAllShown(candidates:stopping:)``) — so it can
    /// vanish under the person for a second reason, which is the other half of
    /// why the arm is not held inside it.
    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(HomeRoomStrings.title)
                .font(.headline)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            if stopAllShown(candidates: candidates, stopping: stoppingAll) {
                stopAll
            }
        }
    }

    /// The two-tap button. Its words are ``StopAllArm/label(count:busy:nowMs:)``'s
    /// — `stop all`, then `stop 2 rooms?`, then `stopping…`.
    ///
    /// The label is computed from the arm the screen holds, so it says
    /// `stop 2 rooms?` **only while the arm is actually live**: the screen
    /// disarms on a timer of its own, so the question cannot sit there after
    /// the four seconds have passed, asking something a tap would no longer
    /// answer.
    private var stopAll: some View {
        let armed = arm.isArmed(nowMs: nowMs)
        return Button(
            arm.label(count: candidates.count, busy: stoppingAll, nowMs: nowMs),
            action: onStopAll
        )
        .buttonStyle(.bordered)
        // Armed, it is about to stop other people's rooms, so it stops looking
        // like the friendly control. `errInk` is 6.08:1 where `.red` is 3.55.
        .tint(armed ? Color.errInk : Color.brandInk)
        .font(.subheadline)
        .fixedSize(horizontal: false, vertical: true)
        .frame(minHeight: 44)
        .contentShape(.rect)
        .disabled(stoppingAll)
    }

    // MARK: - §5's four states

    @ViewBuilder
    private var content: some View {
        if !answered {
            // A 72 dp grey rounded block (`HomeSections.kt:583`) — a skeleton,
            // not a spinner, and hidden from VoiceOver, which has nothing to
            // say about a grey box.
            RoundedRectangle(cornerRadius: 12)
                .fill(.quaternary)
                .frame(height: 72)
                .frame(maxWidth: .infinity)
                .accessibilityHidden(true)
        } else if !hasData {
            // The ordinary unpaired case. One line, wrapping, in the muted
            // ink — not an error card and not a retry button, because nothing
            // on Home retries by hand (map §5.3).
            Text(dbDown ? HomeRoomStrings.roomsUnavailableDBDown : HomeRoomStrings.couldNotLoadRooms)
                .font(.subheadline)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        } else if rooms.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text(HomeRoomStrings.noRoomsYet)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(HomeRoomStrings.setUpARoom, action: onOpenRooms)
            }
        } else {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(groupRooms(sortRooms(rooms)), id: \.label) { group in
                    groupCard(group)
                }
            }
        }
    }

    /// One group: its label, if it has one, and its rooms.
    ///
    /// **A nil label draws no heading at all**, which ``groupRooms(_:)``
    /// already decided and which is not the same as a group called
    /// `ungrouped`: one heading reading `ungrouped` over every room in the
    /// house would tell a person nothing and imply there are others.
    private func groupCard(_ group: RoomGroup) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            if let label = group.label {
                Text(label)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Color.fgMuted)
                    .accessibilityAddTraits(.isHeader)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(group.rooms, id: \.roomID) { room in
                roomRow(room)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            .quaternary.opacity(0.4),
            in: RoundedRectangle(cornerRadius: 16, style: .continuous)
        )
    }

    // MARK: - one room (`HomeRoomRow`, `HomeSections.kt:686`)

    private func roomRow(_ room: HomeRoom) -> some View {
        let pill = roomPill(room, stale: stale)
        let verbs = roomTransport(room, stale: stale)
        return VStack(alignment: .leading, spacing: 6) {
            if typeSize.isAccessibilitySize {
                // The transport buttons move under the room rather than
                // beside it: two 44-point buttons and a wrapping title cannot
                // share a line at AX5.
                VStack(alignment: .leading, spacing: 8) {
                    identity(room, pill: pill)
                    if !verbs.isEmpty { transport(room, verbs: verbs) }
                }
            } else {
                HStack(alignment: .top, spacing: 10) {
                    identity(room, pill: pill)
                    Spacer(minLength: 8)
                    if !verbs.isEmpty { transport(room, verbs: verbs) }
                }
            }
            chips(room)
        }
        // `alpha 0.6` for an offline, waiting or last-known room
        // (`HomeSections.kt:700`): Android's, and the thing on this screen
        // most likely to cost a contrast finding.
        .opacity(dimmed(room) ? 0.6 : 1)
    }

    /// Offline, waiting, or any room at all while the core is down — because
    /// then every row is `last known` and none of it is confirmed.
    private func dimmed(_ room: HomeRoom) -> Bool {
        stale || !room.online
    }

    /// The left side: the dot, the room's **id** (never a label — the id *is*
    /// the name here), the one pill, and the second line. Tapping it opens
    /// Settings → Rooms.
    private func identity(_ room: HomeRoom, pill: RoomPill?) -> some View {
        Button(action: onOpenRooms) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    ToneDot(tone: dotTone(room), live: room.online && !stale)
                    Text(room.roomID)
                        .font(.headline)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    if let pill {
                        HomeTonePill(label: pill.label, tone: pill.tone)
                    }
                }
                secondLine(room)
            }
            .frame(minHeight: 44, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
    }

    /// The status dot: Ok online, Warn `waiting`, Idle otherwise
    /// (`HomeSections.kt:690`).
    ///
    /// Not ``roomPill(_:stale:)``'s tone, which is a different mapping on
    /// purpose: the dot grades *presence* and the pill names *what it is
    /// doing*, so an online room playing music has a green dot and a brand
    /// pill.
    private func dotTone(_ room: HomeRoom) -> HealthTone {
        if room.online { return .ok }
        if room.status == "waiting" { return .warn }
        return .idle
    }

    /// The song, or one of the four words.
    @ViewBuilder
    private func secondLine(_ room: HomeRoom) -> some View {
        switch roomSecondLine(room) {
        case .song(let song):
            songLines(room, song: song)
        case .lastSeen(let iso):
            // Rendered here and not in the package: ``RoomSecondLine`` carries
            // the ISO timestamp precisely because the words change every
            // minute against a clock, so a value that had rendered them would
            // be frozen at whatever second it was built.
            Text(lastSeenLine(iso, nowMs: nowMs))
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .monospacedDigit()
                .fixedSize(horizontal: false, vertical: true)
        case .quiet, .notConnectedYet, .neverConnected:
            if let text = roomSecondLine(room).text {
                Text(text)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// `<title> · <artist>`, the 3 dp bar, and `m:ss / m:ss` in monospace.
    ///
    /// Monospace on the two times for the reason the countdown is: a
    /// proportional digit changes width as it counts, so the line would twitch
    /// once a second.
    private func songLines(_ room: HomeRoom, song: HomeSong) -> some View {
        let elapsed = roomElapsedSec(room, stale: stale, sinceReadSec: sinceReadSec)
        return VStack(alignment: .leading, spacing: 4) {
            Text(songLine(song))
                .font(.footnote)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            HStack(spacing: 8) {
                ProgressView(value: roomProgress(room, elapsedSec: elapsed))
                    .progressViewStyle(.linear)
                    .frame(height: 3)
                    .tint(Color.brand)
                    .accessibilityHidden(true)
                // ``fmtDur(_:)`` gives a bare em dash for a stream with no
                // duration, which is why the second half is not conditional:
                // `1:12 / —` is the honest line for a radio station.
                Text("\(fmtDur(elapsed)) / \(fmtDur(song.durationSec))")
                    .font(.caption.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// The chip row, drawn only if there is something in it
    /// (`HomeSections.kt:775`).
    @ViewBuilder
    private func chips(_ room: HomeRoom) -> some View {
        let left = timerLeft[room.roomID]
        let call = inCallChip(room)
        let screenStopped = kioskDead(room)
        let weak = weakWifi(room)
        if left != nil || call != nil || screenStopped || weak {
            // Wrapping, not scrolling: four chips and a long room name do not
            // fit one line at an accessibility size, and a horizontal scroll
            // view hides what it cannot fit without saying so.
            VStack(alignment: .leading, spacing: 6) {
                if let left {
                    chip("timer", fmtLeft(left), tone: .idle, monospaced: true)
                }
                if let call {
                    chip("phone.fill", call, tone: .idle)
                }
                if screenStopped {
                    chip("tv.slash", HomeRoomStrings.screenStopped, tone: .warn)
                }
                if weak {
                    chip("wifi.slash", HomeRoomStrings.weakWifi, tone: .warn)
                }
            }
        }
    }

    /// One chip: a glyph and a word.
    ///
    /// The glyph is **never** the only carrier — every chip says its words
    /// beside it — so the image is hidden from VoiceOver and the label reads
    /// as the text alone.
    private func chip(
        _ systemImage: String,
        _ label: String,
        tone: HealthTone,
        monospaced: Bool = false
    ) -> some View {
        HStack(spacing: 4) {
            Image(systemName: systemImage)
                .font(.caption2)
                .accessibilityHidden(true)
            Text(label)
                .font(monospaced ? .caption.monospaced() : .caption)
                .fixedSize(horizontal: false, vertical: true)
        }
        .foregroundStyle(tone.inkColour)
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        // Opaque, like every other pill on this screen: a contrast check
        // cannot read a colour that is whatever is behind it.
        .background(Color(.secondarySystemBackground), in: Capsule())
        .accessibilityElement(children: .combine)
    }

    /// pause + stop while playing, resume + stop while paused, and **nothing
    /// at all for a quiet room** — "starting something in a room belongs to
    /// the Music page, which picks the room and the track together". A play
    /// button here would have to invent what to play.
    private func transport(_ room: HomeRoom, verbs: [String]) -> some View {
        HStack(spacing: 4) {
            ForEach(verbs, id: \.self) { verb in
                Button { onTransport(room, verb) } label: {
                    Image(systemName: glyph(for: verb))
                        .font(.body)
                        .frame(minWidth: 44, minHeight: 44)
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.brandInk)
                .disabled(busy(room.roomID))
                // `pause kitchen`, `resume kitchen`, `stop kitchen` — the
                // package's ``transportLabel(_:room:)``, and the only thing
                // that says which room an unlabelled glyph belongs to.
                .accessibilityLabel(transportLabel(verb, room: room.roomID))
            }
        }
    }

    /// The three verbs' glyphs. **A choice, not a transcription** — Android
    /// draws Material icons and there is no mapping from those names to SF
    /// Symbols. A name that does not exist draws nothing at all rather than
    /// failing, and nothing in the package can see that, so the three are on
    /// the Simulator checklist.
    private func glyph(for verb: String) -> String {
        switch verb {
        case "pause": return "pause.fill"
        case "resume": return "play.fill"
        default: return "stop.fill"
        }
    }
}
