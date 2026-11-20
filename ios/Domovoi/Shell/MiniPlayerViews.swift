import DomovoiKit
import SwiftUI

/// The docked mini player: a bar above the phone bar (and above the local
/// shell's tabs) with what is playing, play/pause, next and "Stop and close";
/// a tap opens the whole player in a sheet. `MiniPlayer.kt` and
/// `PlayerQueueSheet.kt`'s `DockedPlayer` on Android (owner, 2026-11-20).
///
/// ### It never builds the player
///
/// Reading ``ShellModel/player`` is what builds it, and a built player
/// activates a `.playback` audio session — which stops whatever the person
/// was already listening to in another app. So this watches
/// ``ShellModel/playerReady`` and reads the player only once something else
/// (a play tap in Music, Audiobooks, Podcasts, Stations) has built it.
///
/// ### One model with Music's player tab
///
/// The bar and the sheet read ``MusicLocalQueueModel`` over
/// ``PlayerModelMusicEngine`` — exactly what Music's `player` tab reads — and
/// the sheet *is* that tab's view, so the two can never disagree about what
/// is playing or what a control does.
struct MiniPlayerDock: View {

    let model: ShellModel

    @State private var queue: MusicLocalQueueModel?
    @State private var covers: MusicCoverStore?
    @State private var sheetOpen = false

    var body: some View {
        Group {
            if let queue, let face = face(of: queue) {
                MiniPlayerBar(
                    face: face,
                    queue: queue,
                    covers: covers,
                    onOpen: { sheetOpen = true }
                )
                .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .animation(.easeOut(duration: 0.2), value: queue.flatMap(face(of:)) == nil)
        .task(id: model.playerReady) { adopt() }
        .onChange(of: queue?.toast) { _, text in
            guard text != nil, let said = queue?.takeToast() else { return }
            model.showToast(said)
        }
        .sheet(isPresented: $sheetOpen) {
            if let queue {
                MiniPlayerSheet(queue: queue, covers: covers, onDone: { sheetOpen = false })
            }
        }
        // Nothing left to show (stopped and closed, the last song ended):
        // the sheet goes with the bar rather than staying up over "nothing
        // queued".
        .onChange(of: queue?.isEmpty ?? true) { _, empty in
            if empty { sheetOpen = false }
        }
    }

    private func face(of queue: MusicLocalQueueModel) -> MiniPlayerFace? {
        miniPlayerFace(
            current: queue.current,
            castingTo: queue.castingTo,
            remote: queue.engine?.remote,
            isPlaying: queue.engine?.isPlaying ?? false,
            positionSec: queue.positionSec
        )
    }

    /// Builds the bar's model over the player, once there is one.
    private func adopt() {
        guard model.playerReady, queue == nil, let deviceID = model.deviceID,
              let player = model.player else { return }
        let made = MusicLocalQueueModel(deviceID: deviceID)
        made.use(PlayerModelMusicEngine(player))
        queue = made
        if covers == nil { covers = MusicCoverStore(in: model.container) }
    }
}

/// The bar itself: `MiniPlayer.kt`'s row, 2 pt progress hairline on top.
private struct MiniPlayerBar: View {

    let face: MiniPlayerFace
    let queue: MusicLocalQueueModel
    let covers: MusicCoverStore?
    let onOpen: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    Rectangle().fill(Color.secondary.opacity(0.25))
                    Rectangle().fill(Color.brand)
                        .frame(width: proxy.size.width * face.progress)
                }
            }
            .frame(height: 2)
            .accessibilityHidden(true)
            HStack(spacing: 4) {
                Button(action: onOpen) {
                    HStack(spacing: 10) {
                        if let trackID = face.coverTrackID, let covers {
                            MusicCoverView(trackID: trackID, size: 36, corner: 6, covers: covers)
                        }
                        VStack(alignment: .leading, spacing: 2) {
                            Text(face.title)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(Color.primary)
                                .lineLimit(1)
                            if let room = face.castingTo {
                                HomeTonePill(label: MusicMoreStrings.castingTo(room), tone: .brand)
                            } else if let artist = face.artist {
                                Text(artist)
                                    .font(.footnote)
                                    .foregroundStyle(Color.fgMuted)
                                    .lineLimit(1)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .frame(minHeight: 44)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityElement(children: .combine)
                .accessibilityHint(MiniPlayerStrings.openHint)

                control(
                    face.isPlaying ? "pause.fill" : "play.fill",
                    face.isPlaying ? MusicTransportAction.pause.label : MusicTransportAction.resume.label
                ) { queue.engine?.toggle() }
                // Absent at the end of the queue, as on the player tab — but
                // its place is kept, so "Stop and close" never slides under a
                // second tap (`MiniPlayer.kt`'s reason for greying instead).
                if queue.canNext {
                    control("forward.end.fill", MusicTransportAction.skip.label) { queue.engine?.next() }
                } else {
                    Color.clear.frame(width: 44, height: 44).accessibilityHidden(true)
                }
                control("xmark", stopAndCloseLabel(queue.stopGroup), small: true) {
                    queue.stopAndClose()
                }
            }
            .padding(.leading, 12)
            .padding(.trailing, 4)
            .padding(.vertical, 4)
        }
        .background(.bar)
    }

    private func control(
        _ symbol: String,
        _ label: String,
        small: Bool = false,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(small ? .footnote.weight(.semibold) : .body)
                .foregroundStyle(small ? Color.fgMuted : Color.primary)
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }
}

/// The whole player, from the bar: Music's own `player` tab in a sheet
/// (`PlayerQueueSheet.kt` on Android). No lyrics and no cast menu here —
/// both need Music's reads of the house (its rooms, its lyrics docs), and a
/// sheet that started those on every open would be sending requests nobody
/// asked for. "Play here" while casting still works; casting *to* a room is
/// Music's, where the rooms are.
private struct MiniPlayerSheet: View {

    let queue: MusicLocalQueueModel
    let covers: MusicCoverStore?
    let onDone: () -> Void

    var body: some View {
        NavigationStack {
            ScrollView {
                Group {
                    if let covers {
                        MusicPlayerTabView(player: queue, covers: covers, lyrics: nil, rooms: [])
                    }
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(MiniPlayerStrings.sheetTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button(MiniPlayerStrings.done, action: onDone)
                }
            }
        }
        .presentationDragIndicator(.visible)
    }
}
