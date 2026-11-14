import DomovoiKit
import SwiftUI

/// The queue tools, handed down a screen once rather than through every
/// initialiser between it and the control that needs them.
///
/// Set by ``MusicView`` (and a room queue sheet, for its own content); read
/// by the queue's search box, the track sheet's queue buttons and the room
/// cards' queue button. Nil where nobody set it — every reader then draws
/// nothing, which is the absent-not-disabled rule.
private struct QueueToolsKey: EnvironmentKey {
    static var defaultValue: QueueTools? { nil }
}

extension EnvironmentValues {
    var queueTools: QueueTools? {
        get { self[QueueToolsKey.self] }
        set { self[QueueToolsKey.self] = newValue }
    }
}

/// The queue's search box (the web's `PlayerQueueAdd`, Android's
/// `QueueAddBox.kt`), drawn wherever the app shows a queue: Music's player
/// and room-queue tabs and a room's queue sheet. It sits at the queue's top,
/// a friendly prompt in it; each find adds at the end on a tap, or plays next
/// from its own button.
///
/// **Hidden on a phone that is not paired**: it searches the household's
/// library and writes to a queue, and both need the pairing.
///
/// `saveIDs`: the phone queue's library songs for "save as playlist" under
/// the box, or nil where there is no such button (a room's queue has its own,
/// saved by the server from the room's list).
struct QueueAddBox: View {

    let target: QueueTarget
    var saveIDs: [Int64]?

    @Environment(\.queueTools) private var tools
    @State private var search: QueueSearchModel?

    var body: some View {
        if let tools, tools.paired {
            VStack(alignment: .leading, spacing: 6) {
                if let search {
                    field(search)
                    finds(search)
                    if !search.line.isEmpty {
                        Text(search.line)
                            .font(.footnote)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityIdentifier(Self.statusID)
                    }
                }
                if let saveIDs {
                    QueuePhoneSaveRow(trackIDs: saveIDs, client: tools.container.client)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            // One box per target: what was typed for one room's queue is not
            // offered to another, and a phone that starts casting gets a box
            // that adds to the room.
            .task(id: target) {
                if search?.target != target {
                    search?.stop()
                    search = tools.search(target)
                }
            }
            .onDisappear { search?.stop() }
        }
    }

    static let statusID = "queue-add-status"

    private func field(_ search: QueueSearchModel) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(Color.fgMuted)
                .accessibilityHidden(true)
            TextField(
                search.prompt,
                text: Binding(get: { search.text }, set: { search.update($0) }),
                prompt: ShellHintPrompt.text(search.prompt)
            )
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .submitLabel(.search)
            .accessibilityLabel(QueueStrings.searchLabel)
            if !search.text.isEmpty {
                Button { search.clear() } label: {
                    Image(systemName: "xmark.circle.fill")
                        .frame(minWidth: 44, minHeight: 44)
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.fgMuted)
                .accessibilityLabel(QueueStrings.clearSearch)
            }
        }
        .padding(.leading, 10)
        .frame(minHeight: 44)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 8))
        .overlay { RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary) }
    }

    @ViewBuilder
    private func finds(_ search: QueueSearchModel) -> some View {
        ForEach(search.rows) { find in
            QueueFindRow(find: find, onAdd: { search.add(find, next: false) }, onNext: { search.add(find, next: true) })
        }
        if let none = search.nothingNote {
            Text(none)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.vertical, 6)
        }
    }
}

/// One find: tap it to add it, or its two buttons (play next / add to queue).
struct QueueFindRow: View {

    let find: QueueFind
    let onAdd: () -> Void
    let onNext: () -> Void

    var body: some View {
        HStack(alignment: .center, spacing: 0) {
            Button(action: onAdd) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(find.title)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(find.sub)
                        .font(.caption.monospaced())
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(find.title)
            .accessibilityHint(QueueStrings.addToQueueLabel(find.label))
            icon("text.line.first.and.arrowtriangle.forward", QueueStrings.playNextLabel(find.label), tint: .fgMuted, action: onNext)
            icon("text.badge.plus", QueueStrings.addToQueueLabel(find.label), tint: .brandInk, action: onAdd)
        }
    }

    private func icon(_ symbol: String, _ label: String, tint: Color, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(tint)
        .accessibilityLabel(label)
    }
}

/// "save as playlist" for this phone's queue (the web's `PlayerQueueSave`):
/// a quiet button that opens a name field.
struct QueuePhoneSaveRow: View {

    let trackIDs: [Int64]
    let client: DomovoiClient

    @State private var flow = SaveAsPlaylistFlow()

    var body: some View {
        SaveAsPlaylistRow(
            flow: flow,
            enabled: !trackIDs.isEmpty,
            hint: trackIDs.isEmpty ? QueueStrings.saveAsPlaylistNone : QueueStrings.saveAsPlaylistHint,
            canSave: flow.canSave(trackIDs),
            onSave: {
                guard flow.canSave(trackIDs) else { return }
                let ids = trackIDs
                let flow = flow
                let client = client
                // Not this row's: the flow holds the outcome, and a row that
                // scrolls away mid-save still finds it there.
                Task { await flow.save(client: client, trackIDs: ids) }
            }
        )
    }
}

/// The save button and, once pressed, its name field: this phone's queue
/// (``QueuePhoneSaveRow``) and a room's (the room queue's header). `hint`:
/// the button's accessibility name; `onSave` sends.
struct SaveAsPlaylistRow: View {

    let flow: SaveAsPlaylistFlow
    let enabled: Bool
    let hint: String
    let canSave: Bool
    let onSave: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if flow.open {
                TextField(
                    QueueStrings.playlistNamePrompt,
                    text: Binding(get: { flow.name }, set: { flow.name = $0 }),
                    prompt: ShellHintPrompt.text(QueueStrings.playlistNamePrompt)
                )
                .textFieldStyle(.roundedBorder)
                .submitLabel(.done)
                .onSubmit(onSave)
                .accessibilityLabel(QueueStrings.playlistNameLabel)
                HStack(spacing: 12) {
                    Button(action: onSave) {
                        Label(QueueStrings.save, systemImage: "square.and.arrow.down")
                            .font(.subheadline)
                            .frame(minHeight: 44)
                            .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(canSave ? Color.brandInk : Color.fgMuted)
                    .disabled(!canSave)
                    HomeLink(QueueStrings.cancel) { flow.cancel() }
                }
            } else {
                Button { flow.ask() } label: {
                    Label(QueueStrings.saveAsPlaylist, systemImage: "square.and.arrow.down")
                        .font(.subheadline)
                        .frame(minHeight: 44)
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .foregroundStyle(enabled ? Color.brandInk : Color.fgMuted)
                .disabled(!enabled)
                .accessibilityLabel(QueueStrings.saveAsPlaylist)
                .accessibilityHint(hint)
            }
            if !flow.note.isEmpty {
                Text(flow.note)
                    .font(.footnote)
                    .foregroundStyle(flow.failed ? Color.errInk : Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier(Self.noteID)
            }
        }
    }

    static let noteID = "queue-save-note"
}

/// A song row's Play next / Add to queue (21b5ccfb), to wherever the player
/// plays now — the room this phone casts to, else this phone, read at the
/// tap (``QueueTools/followingTarget``). The library's, a genre's and a
/// playlist's rows all draw this one.
///
/// One menu rather than Android's two icons side by side: an iPhone row
/// already carries the cover, the title and one or two controls, and two
/// more 44-point targets left the title a word wide at the larger text
/// sizes. The two verbs and their words are Android's.
///
/// **Absent** where it could not do what it says: no queue tools, or a target
/// it cannot reach (``QueueTools/offersSongQueue``).
struct SongQueueMenu: View {

    let track: LibraryTrack
    /// Where the sentence goes: a sheet's own line, or the shell's toast.
    let onSaid: (String) -> Void

    @Environment(\.queueTools) private var tools

    var body: some View {
        if let tools, tools.offersSongQueue {
            Menu {
                Button(QueueStrings.playNext) { add(tools, next: true) }
                Button(QueueStrings.addToQueue) { add(tools, next: false) }
            } label: {
                Image(systemName: "text.badge.plus")
                    .frame(minWidth: 44, minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.brandInk)
            // The track in the label: ten rows each saying "queue" name
            // nothing.
            .accessibilityLabel(QueueStrings.songQueueMenu(track.displayTitle))
        }
    }

    /// The tools' own `Task`, not the row's: the row may scroll away, or the
    /// sheet close, before the room answers.
    private func add(_ tools: QueueTools, next: Bool) {
        let track = track
        let onSaid = onSaid
        Task { @MainActor in
            if let said = await tools.songToFollowing(track, next: next) { onSaid(said) }
        }
    }
}
