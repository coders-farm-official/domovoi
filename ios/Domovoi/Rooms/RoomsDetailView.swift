import DomovoiKit
import SwiftUI

/// One room, opened — `SatelliteDetail` (`SatelliteDetail.kt:70`): a header, a
/// tab strip, and the open tab's body.
///
/// ### The tabs carry words
///
/// Android's seven are **icon-only**, with the reason in its own comment:
/// "the 7 labels overflow narrow widths" (`SatelliteDetail.kt:60`). An
/// icon-only tab with no accessibility label is a VoiceOver dead end, and this
/// app has an answer for the width that Android's `TabRow` did not use — the
/// strip scrolls, as ``MusicTabBar`` does, so seven words fit a phone at any
/// type size. Recorded in DECISIONS.
///
/// ### What is not ported
///
/// `keyboardCrowdsTheWindow()` (`:129`, `:162`), which removes the header and
/// the tab strip while the keyboard is up. It is a fix for a measured Android
/// landscape bug — the announce field and `send` ended up off screen while you
/// typed — and the iOS field is inside a scroll view that scrolls itself into
/// view on focus. On the Simulator checklist. (Its first reason was that
/// porting it would hide the one control that closed the room; that control
/// is gone with D56 and the navigation bar's back button cannot be hidden by
/// a keyboard, so what is left is the measured-bug argument alone.)
///
/// ### There is no close control (D56)
///
/// The header used to end in an `xmark` button labelled `close this room`.
/// A room is its own pushed screen now (Kamron, answer 56), so the system's
/// back button and its swipe-back gesture close it; one of ours beside them
/// would be a second way to do one thing, and a test that tapped `close this
/// room` would be testing a control no iPhone app draws.
struct RoomsDetailView: View {

    /// Passed through to the overview's drop-in row, which needs this phone's
    /// device id and its client to open a call (D54).
    let shell: ShellModel

    let model: RoomsModel

    /// **The roster's row, as it is now** — never a copy taken when the room
    /// was opened (``RoomsModel/openRoom``). A room that goes offline while
    /// its detail is up has to say so.
    let room: RoomsSatellite

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            RoomsTabStrip(tab: model.tab, onPick: { model.select($0) })
            tabBody
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// The dot, the room and its status (`CardHeader`,
    /// `SatelliteDetail.kt:129`), with no close control — see this type's
    /// note on D56.
    ///
    /// The room's id is kept here although the navigation bar also carries it
    /// (``RoomsRoomScreen``): the bar's title is a word in the chrome, and
    /// this line is what the dot and the status pill belong to. Android draws
    /// the same pair in its header.
    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            ToneDot(tone: room.online ? .ok : .idle, live: room.online)
            Text(room.roomID)
                .font(.title3)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            HomeTonePill(
                label: room.status ?? RoomsStrings.offline,
                tone: room.online ? .ok : .idle
            )
            Spacer(minLength: 8)
        }
    }

    @ViewBuilder
    private var tabBody: some View {
        if let reads = model.openReads {
            switch model.tab {
            case .overview:
                RoomsOverviewView(shell: shell, model: model, reads: reads, room: room)
            case .sessions:
                RoomsSessionsView(model: model, reads: reads)
            case .conversations:
                RoomsConversationsView(reads: reads)
            case .recentlyPlayed:
                RoomsPlayedView(model: model, reads: reads, room: room)
            case .notes:
                RoomsNotesView(model: model, reads: reads)
            case .timers:
                RoomsTimersView(model: model, reads: reads, room: room)
            case .settings:
                RoomsSettingsTabView(model: model, reads: reads, room: room)
            }
        }
    }
}

/// One room as its own pushed screen (D56).
///
/// Kamron, answer 56. What this adds over ``RoomsDetailView`` is the three
/// things being a screen means: a scroll view of its own (the roster's is
/// left behind, and in the Settings mounting the section's is too), the
/// navigation bar's title, and the lifetime — which room is open, and the
/// hold that keeps ``RoomsModel`` running while this screen is up.
///
/// ### Why the hold is here and not only on the roster
///
/// A pushed destination fires the covered view's `onDisappear`, so the
/// roster's own `release()` runs the moment this screen arrives. If nothing
/// held the model up from here, that would stop the reads this screen draws
/// from. ``RoomsModel/hold()`` and ``RoomsModel/release()`` are a count for
/// exactly that, and they say why a count and not a flag.
///
/// ### The room the detail is given is the roster's row, read fresh
///
/// ``RoomsOpenRoom`` carries the id, and the row is looked up in the roster
/// on every redraw — never captured when the push was made. A room that goes
/// offline, starts playing or joins a call while its screen is up has to say
/// so, which is ``RoomsModel/openRoom``'s whole point. A room that drops off
/// the roster entirely says that instead of drawing a stale copy.
struct RoomsRoomScreen: View {

    /// The shell, for the one thing a room screen needs from it: this phone's
    /// device id, which is a drop-in call's identity in the house's
    /// `active_dropins` (``dropInSocketURL(house:info:room:phoneID:)``).
    let shell: ShellModel

    let model: RoomsModel
    let open: RoomsOpenRoom

    /// Whether **this** view is the one holding ``RoomsModel`` up.
    ///
    /// The guard that makes `onAppear`/`onDisappear` safe to receive twice.
    /// `@State`, so it belongs to this view's identity: one push, one hold,
    /// one release, whatever order and however many times the two callbacks
    /// arrive.
    @State private var held = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let room = model.openRoom, room.roomID == open.roomID {
                    RoomsDetailView(shell: shell, model: model, room: room)
                } else if let room = model.rooms.first(where: { $0.roomID == open.roomID }) {
                    // The model has not been told yet: `body` is evaluated
                    // before the `onAppear` below runs, so the first frame
                    // asks for a room nothing has opened. Draw the row we
                    // have rather than a spinner that lasts one frame.
                    RoomsDetailView(shell: shell, model: model, room: room)
                } else {
                    // The room left the roster while this screen was up — a
                    // rename on the dashboard, or a room that was forgotten.
                    // Not an error state: the screen says the room is not
                    // there and the back button is the way out.
                    Text(RoomsStrings.roomIsGone(open.roomID))
                        .font(.subheadline)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .navigationTitle(RoomsStrings.roomTitle(open.roomID))
        .navigationBarTitleDisplayMode(.inline)
        // **All four on one mechanism**, which review round 1 was right to
        // pull on. It used to be `model.open(_:)` from `.task` — justified by
        // "a sheet presented over this screen can fire `onAppear` again" —
        // and `hold()`/`release()` from `onAppear`/`onDisappear`, under a
        // sheet this very screen presents (``RoomsDropInRow``). Both halves
        // cannot be right: if an extra `onAppear` can arrive, it took the
        // holder count to 2 and the model never stopped; if it cannot, the
        // reason for `.task` was never there.
        //
        // So: one pair of callbacks, made safe against arriving more than
        // once. ``held`` is this view's own, so a second `onAppear` holds
        // nothing twice and a second `onDisappear` releases nothing twice —
        // the count is balanced whatever SwiftUI does. And
        // ``RoomsModel/open(_:)`` is idempotent, so re-entering cannot put
        // the tab strip back to `overview`, which is the one harm `.task`
        // was there to prevent.
        .onAppear {
            guard !held else { return }
            held = true
            model.open(open.roomID)
            model.hold()
        }
        .onDisappear {
            guard held else { return }
            held = false
            model.release()
            model.close()
        }
    }
}

/// The seven tabs, as a scrolling row of words (`CardTabStrip`,
/// `SatelliteDetail.kt:162`).
///
/// **Not a segmented `Picker`.** A segmented control divides the width it is
/// given and shrinks the text to fit, which is `minimumScaleFactor` by another
/// name and is rejected throughout this app; seven words do not fit a phone
/// between them at any type size. ``MusicTabBar`` reached the same answer, and
/// so does Android's own `ScrollableTabRow` elsewhere.
struct RoomsTabStrip: View {

    let tab: RoomsTab
    let onPick: (RoomsTab) -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 4) {
                ForEach(RoomsTab.allCases) { one in
                    Button {
                        onPick(one)
                    } label: {
                        Text(one.label)
                            .font(.subheadline)
                            .fontWeight(one == tab ? .semibold : .regular)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.horizontal, 12)
                            .frame(minHeight: 44)
                            .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    // The open tab is **told**, not only drawn darker: weight
                    // and colour are both invisible to VoiceOver, and the word
                    // alone cannot say which one is open.
                    .accessibilityAddTraits(one == tab ? [.isButton, .isSelected] : .isButton)
                    .foregroundStyle(one == tab ? Color.brandInk : Color.fgMuted)
                    .background(
                        one == tab ? Color(.secondarySystemBackground) : .clear,
                        in: Capsule()
                    )
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// ---------------------------------------------------------------------------
// The pieces every tab draws
// ---------------------------------------------------------------------------

/// A label over a value, which is most of this screen (`SectionLabel` plus a
/// row, `SatOverviewTab.kt:89-205`).
struct RoomsReadRow: View {

    let label: String
    let value: String

    /// A value that is a port number, a SHA or a URL is monospaced, because
    /// those are compared character by character against what the dashboard
    /// shows.
    var monospaced = false

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            Text(value)
                .font(monospaced ? .subheadline.monospaced() : .subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

/// A tab's heading — the words `SectionLabel` draws (`SatOverviewTab.kt:89`).
struct RoomsSectionLabel: View {

    let text: String

    var body: some View {
        Text(text)
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(Color.fgMuted)
            .accessibilityAddTraits(.isHeader)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// What a tab says while its read is in flight, has failed, or came back
/// empty — the three states Android draws with `LoadingState`, `ErrorState`
/// and `EmptyState` (`SatHistoryTabs.kt:72-78`).
///
/// One view for all seven tabs because the difference between them is two
/// sentences, and seven copies of this `if` is how one of them comes to draw
/// an empty list as a successful read.
struct RoomsTabState<Value: Sendable, Content: View>: View {

    let read: ApiRead<[Value]>
    let empty: String
    let hint: String?
    @ViewBuilder let content: ([Value]) -> Content

    var body: some View {
        let rows = read.data ?? []
        if !read.answered && rows.isEmpty {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .accessibilityLabel(empty)
        } else if let why = read.error, rows.isEmpty {
            Text(why)
                .font(.subheadline)
                .foregroundStyle(Color.errInk)
                .fixedSize(horizontal: false, vertical: true)
        } else if rows.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                Text(empty)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                if let hint {
                    Text(hint)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        } else {
            content(rows)
        }
    }
}
