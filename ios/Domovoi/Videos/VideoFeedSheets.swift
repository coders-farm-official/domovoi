import DomovoiKit
import SwiftUI

/// The long-press sheets: the actions, the session queue, and the details.
///
/// `VideoFeed.kt:682-782`'s three `ModalBottomSheet`s. One view over the
/// three cases rather than three `.sheet` modifiers, because on iOS a second
/// sheet presented while the first is up is a dismiss-and-present dance —
/// and Android's "options → playlist" is exactly that move.
struct VideoFeedSheetView: View {

    let which: VideoFeedSheet
    let feed: VideoFeedModel
    let engine: FeedEngine

    /// `save to device` (`VideoFeed.kt:459`). The caller does the lookup
    /// from a ``FeedVideo`` to the row, because the row is the feed view's to
    /// find — and it does **not** close this sheet, for the reason the
    /// options row below gives.
    let onSave: (FeedVideo) -> Void

    /// How the save is getting on, so the row can draw it.
    ///
    /// Passed in rather than read from a model here: this view is handed the
    /// feed and the engine and owns neither, and a save belongs to the
    /// screen (``VideosModel/saves``) because it outlives any one sheet.
    let saveState: SaveToDeviceModel.State

    /// Stop the save. See the options row.
    let onCancelSave: () -> Void

    var body: some View {
        NavigationStack {
            content
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button(VideoStrings.close) { feed.closeSheet() }
                    }
                }
        }
        .presentationDetents(which == .playlist ? [.medium, .large] : [.medium])
    }

    private var title: String {
        switch which {
        case .options: return feed.title
        case .playlist: return VideoStrings.playlistCount(feed.queue.count)
        case .details: return feed.title
        }
    }

    @ViewBuilder
    private var content: some View {
        switch which {
        case .options:
            options
        case .playlist:
            playlist
        case .details:
            details
        }
    }

    // MARK: - the actions

    /// Four, not Android's six.
    ///
    /// `upload` and `share` are not drawn — ``VideosView`` says why, and the
    /// short version for `share` is that on iOS the stream URL carries the
    /// household token, so sharing a server video would share the token.
    /// `save to device` **is** drawn, since D47.
    private var options: some View {
        List {
            Button {
                feed.open(.playlist)
            } label: {
                Label(VideoStrings.playlist, systemImage: "list.bullet")
            }
            Button {
                feed.open(.details)
            } label: {
                Label(VideoStrings.details, systemImage: "info.circle")
            }
            Button {
                engine.seek(toMs: 0)
                engine.play()
                feed.closeSheet()
            } label: {
                Label(VideoStrings.playFromBeginning, systemImage: "arrow.counterclockwise")
            }
            // `FeedAction(Icons.Outlined.Download, "save to device")`
            // (`VideoFeed.kt:459`), in the place Android puts it: after "play
            // from beginning" and before the two this app does not draw.
            //
            // **`showing`, not `loaded`.** The sheet is about the video on
            // screen — the one whose title is in the navigation bar — and a
            // fling that moved the page while the sheet was open moved the
            // title too. Nothing is drawn when there is no video, which is
            // the state a feed emptied underneath its own sheet is in.
            //
            // **The sheet stays open while it saves, and this row becomes the
            // progress.** Android closes its sheet and lets the system draw a
            // download notification with a cancel in it
            // (`DeviceDownloads.enqueueWithToken`); iOS has no such
            // notification and no system downloader to hand the request to,
            // so a sheet that closed on the tap would leave a film of any
            // size coming down with nothing on screen saying so and no way to
            // stop it — a control that cannot do what it says
            // ([[no-misleading-controls]]). ``SaveToDeviceButton`` draws the
            // tap, the percentage and the stop from one state, and when the
            // file is ready the screen's own binding swaps this sheet for the
            // share sheet (``VideoFeedView/openSheet``).
            if let video = feed.showing {
                SaveToDeviceButton(
                    state: saveState,
                    onTap: { onSave(video) },
                    onCancel: onCancelSave
                )
            }
        }
        .listStyle(.plain)
    }

    // MARK: - the session queue

    /// Tap a row to jump to it, the X to drop it. **It never deletes a
    /// file** — `VideoFeed.kt:709` says the same, and it is worth saying
    /// twice: an X next to a video is the shape of a delete.
    private var playlist: some View {
        ScrollViewReader { scroller in
            List {
                ForEach(Array(feed.queue.enumerated()), id: \.element.key) { index, video in
                    HStack(spacing: 12) {
                        Button {
                            // Sets the model's `current` and closes the
                            // sheet; the **pager** then follows `current`
                            // and its settle drives the player. Android goes
                            // the same way round (`VideoFeed.kt:333`), and it
                            // matters: a sheet that loaded the video itself
                            // would leave the pager showing a different page
                            // from the one playing.
                            _ = feed.picked(index)
                        } label: {
                            HStack(spacing: 12) {
                                Text("\(index + 1)")
                                    .font(.callout)
                                    .monospaced()
                                    .foregroundStyle(
                                        index == feed.current ? Color.brandInk : Color.fgMuted
                                    )
                                    .frame(minWidth: 32, alignment: .trailing)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(video.title)
                                        .font(.body)
                                        .fontWeight(index == feed.current ? .semibold : .regular)
                                        .foregroundStyle(
                                            index == feed.current ? Color.brandInk : Color.primary
                                        )
                                        .lineLimit(1)
                                    if let meta = Self.meta(video) {
                                        Text(meta)
                                            .font(.caption)
                                            .foregroundStyle(Color.fgMuted)
                                    }
                                }
                                Spacer(minLength: 0)
                            }
                            .contentShape(.rect)
                        }
                        .buttonStyle(.plain)
                        Button {
                            drop(video)
                        } label: {
                            Image(systemName: "xmark")
                                .font(.footnote.weight(.semibold))
                                .foregroundStyle(Color.fgMuted)
                                .frame(width: 44, height: 44)
                                .contentShape(.rect)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(VideoStrings.removeFromPlaylist)
                        .accessibilityHint(video.title)
                    }
                    .id(video.key)
                }
            }
            .listStyle(.plain)
            .onAppear {
                guard feed.queue.indices.contains(feed.current) else { return }
                scroller.scrollTo(feed.queue[feed.current].key, anchor: .center)
            }
        }
    }

    private func drop(_ video: FeedVideo) {
        let outcome = feed.remove(
            video.key,
            atSec: engine.positionSec,
            durationSec: engine.durationSec
        )
        switch outcome {
        case .notThere:
            return
        case .emptied:
            // The last one went. Nothing to do here either: the feed view
            // watches ``VideoFeedModel/isEmpty`` and closes with no key,
            // because there is no video the viewer ended on. A closure
            // passed down for it would be a second path to the same close.
            return
        case .stillThere:
            // The player is driven by the pager's settle, as it is for a
            // swipe: the view scrolls and `settled` does the rest. Nothing
            // to do here — which is the point of the model returning what it
            // returns rather than acting.
            return
        }
    }

    private static func meta(_ video: FeedVideo) -> String? {
        var parts: [String] = []
        if video.sizeBytes != nil { parts.append(fmtBytes(video.sizeBytes)) }
        if let modified = video.modifiedEpochSec { parts.append(fmtEpochDate(modified)) }
        return parts.isEmpty ? nil : parts.joined(separator: "  ·  ")
    }

    // MARK: - the details

    private var details: some View {
        List {
            ForEach(rows) { row in
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    Text(row.label)
                        .font(.subheadline)
                        .foregroundStyle(Color.fgMuted)
                        // A label above its value at the big text sizes, like
                        // the track sheet: Android's fixed 110dp label column
                        // leaves about forty points for "1920 × 1080".
                        .frame(minWidth: 96, alignment: .leading)
                    Text(row.value)
                        .font(.subheadline)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
            }
        }
        .listStyle(.plain)
    }

    private var rows: [VideoMetaRow] {
        guard let video = feed.showing else { return [] }
        return video.detailRows(facts: engine.facts)
    }
}
