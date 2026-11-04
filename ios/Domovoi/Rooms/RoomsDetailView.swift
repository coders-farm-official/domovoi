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
/// view on focus. Porting it would take away the one control that closes the
/// room, for a bug nobody has seen here. On the Simulator checklist.
struct RoomsDetailView: View {

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

    /// The dot, the room, its status, and the close control (`CardHeader`,
    /// `SatelliteDetail.kt:129`).
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
            Button {
                model.close()
            } label: {
                Image(systemName: "xmark")
                    .font(.body)
                    .frame(minWidth: 44, minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.brandInk)
            // Android's close is an icon with no words at all. Every
            // icon-only control in this app carries a label — this one is
            // ``RoomsStrings/closeRoom``, which IOS-ONLY-STRINGS records.
            .accessibilityLabel(RoomsStrings.closeRoom)
        }
    }

    @ViewBuilder
    private var tabBody: some View {
        if let reads = model.openReads {
            switch model.tab {
            case .overview:
                RoomsOverviewView(model: model, reads: reads, room: room)
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
