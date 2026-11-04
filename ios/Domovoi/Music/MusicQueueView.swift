import DomovoiKit
import SwiftUI

/// The room queue tab.
///
/// `QueueTab.kt`. What the **speaker** plays from, not what this phone holds
/// — the distinction the Android file's heading draws, which matters because
/// an edit here lands for everybody in the house.
///
/// ### Arrows, not drag
///
/// Reordering is two buttons per row. Android's reason was a gesture
/// conflict: a long-press drag inside a `LazyColumn` inside the screen's own
/// scrolling `LazyColumn` fights the parent scroll. The same is true here and
/// worse — SwiftUI's `.onMove` needs a `List` in edit mode, and this tab is
/// a section of a `ScrollView`, so a `List` inside it would be a second
/// scroll view with its own height to guess at. Two taps that always work
/// beat a gesture that sometimes does.
struct MusicQueueView: View {

    let music: MusicModel
    let queue: MusicQueueModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if music.castRooms.isEmpty {
                noRooms
            } else {
                picker
                watchOnly
                rows
                clearButton
            }
        }
        // The clear confirmation. An `alert` rather than a
        // `confirmationDialog` because it has a body to read as well as a
        // choice to make, and because the destructive option must be the one
        // that is harder to hit by accident.
        .alert(
            MusicStrings.clearQueueTitle(room: queue.room ?? MusicStrings.roomLabel),
            isPresented: clearing
        ) {
            Button(MusicStrings.clearQueue, role: .destructive) { queue.clear() }
            Button(MusicStrings.cancel, role: .cancel) { queue.confirmingClear = false }
        } message: {
            Text(MusicStrings.clearQueueBody)
        }
    }

    private var clearing: Binding<Bool> {
        Binding(get: { queue.confirmingClear }, set: { queue.confirmingClear = $0 })
    }

    // MARK: - the room

    /// Which room's queue. A `Picker` over the rooms the **server reported**,
    /// never a made-up one, and absent entirely when there is nothing to
    /// choose between.
    @ViewBuilder
    private var picker: some View {
        let rooms = music.castRooms
        HStack(spacing: 8) {
            if rooms.count > 1 {
                Picker(MusicStrings.roomLabel, selection: pick) {
                    ForEach(rooms, id: \.self) { room in
                        Text(room).tag(Optional(room))
                    }
                }
                .pickerStyle(.menu)
            } else if let only = rooms.first {
                Text(only)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            if let count = queue.countText {
                Text(count)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var pick: Binding<String?> {
        Binding(get: { queue.room }, set: { queue.pick($0) })
    }

    private var noRooms: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(MusicStrings.noRooms)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            Text(MusicStrings.noRoomsNote)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Why the controls are not there, when the server said why.
    @ViewBuilder
    private var watchOnly: some View {
        if let note = queue.watchOnlyNote {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: "lock")
                    .foregroundStyle(Color.fgMuted)
                    .accessibilityHidden(true)
                Text(note)
                    .font(.footnote)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 8))
        }
    }

    // MARK: - the entries

    @ViewBuilder
    private var rows: some View {
        if queue.isFirstLoad {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .accessibilityLabel(MusicStrings.tabQueue)
        } else if let trouble = queue.read.error, queue.items.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text(trouble)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(MusicStrings.retry) { queue.read.refresh() }
            }
        } else if let note = queue.emptyNote {
            Text(note)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 24)
        } else {
            // Keyed by position **and** song id. The id alone is MPD's and is
            // unique in a healthy queue; the pair survives an answer that
            // repeats one, which would otherwise be a duplicate-id crash in
            // `ForEach` over something a server said rather than something
            // this app did.
            ForEach(Array(queue.items.enumerated()), id: \.offset) { index, item in
                MusicQueueRow(queue: queue, item: item, index: index)
            }
        }
    }

    /// Empty the queue — household only, and absent for a guest rather than
    /// refused on tap (``RoomQueue/mayClear``).
    @ViewBuilder
    private var clearButton: some View {
        if queue.mayClear, !queue.items.isEmpty {
            Button(role: .destructive) {
                queue.confirmingClear = true
            } label: {
                Text(MusicStrings.clearQueue)
                    .font(.subheadline)
                    .frame(minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.errInk)
        }
    }
}

/// One queue entry: its place, what it is, and the three edits.
struct MusicQueueRow: View {

    let queue: MusicQueueModel
    let item: QueueItem
    let index: Int

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            place
            VStack(alignment: .leading, spacing: 2) {
                Text(item.displayTitle)
                    .font(.subheadline)
                    .fontWeight(item.playing ? .semibold : .regular)
                    .fixedSize(horizontal: false, vertical: true)
                Text(item.subtitle)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            edits
        }
        .padding(.vertical, 4)
    }

    /// The number, or the speaker icon for the entry playing now.
    ///
    /// The icon has a label rather than being hidden, because it is the only
    /// thing on the row saying this is the one playing — the semibold title
    /// is not something VoiceOver reads out.
    @ViewBuilder
    private var place: some View {
        if item.playing {
            Image(systemName: "speaker.wave.2.fill")
                .font(.caption)
                .foregroundStyle(Color.brandInk)
                .accessibilityLabel(MusicStrings.nowPlayingHere)
        } else {
            Text("\(index + 1)")
                .font(.caption.monospacedDigit())
                .foregroundStyle(Color.fgMuted)
                .accessibilityHidden(true)
        }
    }

    /// Up, down, out — each drawn only when it can actually be done.
    ///
    /// The three questions are ``MusicQueueModel``'s
    /// (``MusicQueueModel/mayMoveUp(_:)`` and the rest), so the row does no
    /// index arithmetic of its own and a guest is never shown a remove on the
    /// entry the core will refuse to remove.
    @ViewBuilder
    private var edits: some View {
        HStack(spacing: 0) {
            if queue.mayMoveUp(index) {
                control("arrow.up", MusicStrings.moveUp) { queue.moveUp(index) }
            }
            if queue.mayMoveDown(index) {
                control("arrow.down", MusicStrings.moveDown) { queue.moveDown(index) }
            }
            if queue.mayRemove(item) {
                control("xmark", MusicStrings.removeFromQueue, tint: .errInk) {
                    queue.remove(item)
                }
            }
        }
    }

    private func control(
        _ symbol: String,
        _ label: String,
        tint: Color = .brandInk,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.caption)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(tint)
        .accessibilityLabel(label)
    }
}
