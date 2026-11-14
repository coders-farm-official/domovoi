import DomovoiKit
import SwiftUI

/// A room's queue: Music's "room queue" tab, and a room's queue sheet
/// (``RoomQueueSheet``).
///
/// `QueueTab.kt` (Android 21b5ccfb, cb780e90, bd274c2f, 6d14aa91). What the
/// **speaker** plays from, not what this phone holds — an edit here lands for
/// everybody in the house. At its top the search box adds to it
/// (``QueueAddBox``); a row's tap plays that song now (the room's real jump);
/// the room's own Up next picks are marked, under a head that says what the
/// mix is built from and holds the room's autoplay switch; "less like this"
/// on a pick takes its artist out of the mix; "save as playlist" keeps the
/// queue. A guest gets the jump, the moves and remove — no switch, no "less
/// like this", no saving, no clear. Nothing of Up next shows without a
/// session.
///
/// ### Arrows, not drag
///
/// Android moved to a drag handle (`QueueDrag.kt`, with TalkBack move
/// actions). Reordering stays two buttons per row here, for the reason this
/// view gave before: a drag inside a section of the screen's own
/// `ScrollView` fights the parent scroll, and SwiftUI's `.onMove` needs a
/// `List` in edit mode, which would be a second scroll view with its own
/// height to guess at. Two taps that always work — and are VoiceOver's
/// own words — beat a gesture that sometimes does.
///
/// ### A long queue, a page at a time
///
/// A whole playlist can be a room's queue (~1,500 songs): the rows are drawn
/// 100 at a time, more as the list reaches their end, and deep into a queue
/// the songs already played are folded away (``RoomQueueWindow``).
struct MusicQueueView: View {

    /// The rooms the **server** reported — the picker's, never a made-up one.
    let rooms: [String]
    let queue: MusicQueueModel
    /// The room picker; off where the room is already chosen (a room's own
    /// sheet), which draws "<room>'s queue" instead.
    var picker = true
    /// Say what an edit did on a line inside the view — a sheet covers the
    /// shell's toast.
    var inline = false

    init(rooms: [String], queue: MusicQueueModel, picker: Bool = true, inline: Bool = false) {
        self.rooms = rooms
        self.queue = queue
        self.picker = picker
        self.inline = inline
    }

    /// Music's tab, over the rooms its now-playing read named.
    init(music: MusicModel, queue: MusicQueueModel) {
        self.init(rooms: music.castRooms, queue: queue)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if rooms.isEmpty {
                noRooms
            } else {
                head
                if inline, let said = queue.said {
                    Text(said)
                        .font(.footnote)
                        .foregroundStyle(queue.saidFailed ? Color.errInk : Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .accessibilityIdentifier(Self.statusID)
                }
                headControls
                watchOnly
                // Search to add, at the top: only where adding works;
                // otherwise the notice above says the queue's
                // blocked_reason. One box per room.
                if let room = queue.room, queue.canAdd {
                    QueueAddBox(target: .room(room))
                        .id(room)
                }
                rows
            }
        }
        // The clear confirmation. An `alert` rather than a
        // `confirmationDialog` because it has a body to read as well as a
        // choice to make.
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

    static let statusID = "room-queue-status"

    private var clearing: Binding<Bool> {
        Binding(get: { queue.confirmingClear }, set: { queue.confirmingClear = $0 })
    }

    // MARK: - the head

    /// Which room's queue, and how many are in it.
    @ViewBuilder
    private var head: some View {
        HStack(spacing: 8) {
            if picker, rooms.count > 1 {
                Picker(MusicStrings.roomLabel, selection: pick) {
                    ForEach(rooms, id: \.self) { room in
                        Text(room).tag(Optional(room))
                    }
                }
                .pickerStyle(.menu)
            } else if picker, let only = rooms.first {
                Text(only)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
            } else if !picker, let room = queue.room {
                Text(QueueStrings.roomQueueLabel(room))
                    .font(.subheadline.weight(.semibold))
                    .accessibilityAddTraits(.isHeader)
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

    /// Clear, and save as playlist — the household's, never a guest's.
    @ViewBuilder
    private var headControls: some View {
        if queue.mayClear {
            Button(role: .destructive) {
                queue.confirmingClear = true
            } label: {
                Label(MusicStrings.clearQueue, systemImage: "trash")
                    .font(.subheadline)
                    .frame(minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.errInk)
        }
        if queue.maySave, let room = queue.room {
            SaveAsPlaylistRow(
                flow: queue.save,
                enabled: true,
                hint: QueueStrings.roomSaveHint(room),
                canSave: queue.save.canSaveRoom,
                onSave: { queue.saveQueue() }
            )
        }
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
            let items = queue.items
            LazyVStack(alignment: .leading, spacing: 0) {
                // Keyed by the line: an entry by its index in the whole
                // queue, which survives an answer that repeats a song id —
                // a duplicate-id crash in `ForEach` over something a server
                // said rather than something this app did.
                ForEach(queue.lines, id: \.self) { line in
                    switch line {
                    case .upNextHead:
                        RoomUpNextHead(queue: queue)
                    case .showEarlier(let count):
                        moreButton(RoomQueueWindow.showEarlierLabel(count)) { queue.showEarlier() }
                    case .showMore(let count):
                        moreButton(RoomQueueWindow.showMoreLabel(count)) { queue.showMore() }
                            // Reached by scrolling: the next rows come in on
                            // their own. The button is for whoever does not
                            // scroll — VoiceOver, Switch Control.
                            .onAppear { queue.showMore() }
                    case .entry(let index):
                        if items.indices.contains(index) {
                            MusicQueueRow(queue: queue, item: items[index], index: index)
                        }
                    }
                }
            }
        }
    }

    /// "show more (N more)" / "show N played songs": a quiet full-width
    /// button.
    private func moreButton(_ label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(.footnote)
                .frame(maxWidth: .infinity, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
    }
}

/// The "up next" head: what the mix is built from, the autoplay switch, and
/// the one muted line.
struct RoomUpNextHead: View {

    let queue: MusicQueueModel

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .center, spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(RoomUpNextStrings.head)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Color.fgMuted)
                        .textCase(.uppercase)
                        .accessibilityAddTraits(.isHeader)
                    if !queue.upNextBasis.isEmpty {
                        Text(queue.upNextBasis)
                            .font(.caption)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 8)
                if queue.mayAutoplay, let room = queue.room {
                    Toggle(RoomUpNextStrings.autoplay, isOn: autoplay)
                        .font(.footnote)
                        .fixedSize()
                        .disabled(queue.busy)
                        .accessibilityLabel(RoomUpNextStrings.autoplayLabel(room))
                }
            }
            .frame(minHeight: 44)
            if let line = queue.upNextLine {
                Text(line)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.top, 8)
        .padding(.bottom, 4)
    }

    private var autoplay: Binding<Bool> {
        Binding(get: { queue.queue.autoplay == true }, set: { queue.setAutoplay($0) })
    }
}

/// One queue entry: its place, what it is, why the room picked it, and the
/// edits.
struct MusicQueueRow: View {

    let queue: MusicQueueModel
    let item: QueueItem
    /// Its index in the whole (shown) queue.
    let index: Int

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            place
            what
            Spacer(minLength: 4)
            edits
        }
        .padding(.vertical, 4)
        .background(
            item.playing ? Color(.secondarySystemBackground) : .clear,
            in: RoundedRectangle(cornerRadius: 6)
        )
    }

    /// The text, as the button that plays it now where that can be done.
    @ViewBuilder
    private var what: some View {
        if queue.mayPlayNow(item) {
            Button { queue.playNow(item) } label: {
                text
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityHint(QueueStrings.playNow)
        } else {
            text
                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
        }
    }

    private var text: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                if queue.isWaitingPick(index) {
                    Image(systemName: "sparkles")
                        .font(.caption2)
                        .foregroundStyle(Color.fgMuted)
                        .accessibilityLabel(RoomUpNextStrings.pick)
                }
                Text(item.displayTitle)
                    .font(.subheadline)
                    .fontWeight(item.playing ? .semibold : .regular)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(item.subtitle)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            // Why the room picked it: a muted chip ("same artist", "90s").
            if item.isPick, let reason = item.reasonLabel?.trimmingCharacters(in: .whitespaces), !reason.isEmpty {
                HomeTonePill(label: reason, tone: .idle)
            }
        }
        .accessibilityElement(children: .combine)
    }

    /// The number, or the speaker icon for the entry playing now.
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

    /// Less like this, up, down, out — each drawn only when it can actually
    /// be done. The questions are ``MusicQueueModel``'s, so the row does no
    /// index arithmetic of its own.
    @ViewBuilder
    private var edits: some View {
        HStack(spacing: 0) {
            if queue.mayLessLike(index) {
                control("hand.thumbsdown", RoomUpNextStrings.lessLikeLabel(item.artist), tint: .fgMuted) {
                    queue.lessLike(item)
                }
            }
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
