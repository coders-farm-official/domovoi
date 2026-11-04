@preconcurrency import Photos
import AVKit
import DomovoiKit
import SwiftUI

/// One video from this phone, played here.
///
/// `ui/screens/local/LocalMediaScreens.kt:367` opens the server's own
/// `VideoFeedPlayer` over the grid's order; this is one video and no swipe,
/// for the reason ``PhoneDeviceVideosView`` states and DECISIONS records:
/// ``FeedVideo`` holds a server-relative `streamPath` that ``VideoStream``
/// resolves, a `PHAsset` has no such path, and `FeedVideo` is shared
/// infrastructure this item may not edit.
///
/// ### Sharing is offered here, and nowhere else in this app
///
/// `shareable = true` for a phone video (`LocalMediaScreens.kt:363`), and
/// false for every server one. That is not an inconsistency: a phone video
/// is the person's own file and carries nothing of the household with it,
/// whereas a server video's stream URL carries the household **token**
/// (``VideoStream``), so handing one to another app hands over the token.
/// ``VideosView``'s doc comment says so, and this item does not change it.
///
/// ### What is not, and why there is no control for it
///
/// * **No position saved.** `/api/videos/position` is the server's record of
///   a server video (`VideosModel`); a file on this phone is not in it, and
///   there is no phone-side store for one.
/// * **No next/previous.** That is the feed, above.
/// * **No share for an asset that will not come down.** An iCloud video with
///   no local bytes cannot be handed to another app, so the sheet is not
///   offered until the player has a URL in hand.
struct PhoneVideoPlayerView: View {

    let item: PhoneMediaItem

    @Environment(\.dismiss) private var dismiss

    @State private var player: AVPlayer?

    /// The file the share sheet would hand over. For a video in a picked
    /// folder this is the file itself; for an asset it is whatever
    /// PhotoKit resolved, which may be a URL it owns.
    @State private var shareable: URL?

    @State private var trouble: String?
    @State private var sharing = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                if let player {
                    VideoPlayer(player: player)
                        .aspectRatio(16 / 9, contentMode: .fit)
                        .frame(maxWidth: .infinity)
                        .accessibilityLabel(item.name)
                } else if let trouble {
                    PhoneDeviceNote(title: VideoStrings.cantPlay, detail: trouble)
                        .padding(.horizontal, 16)
                } else {
                    ProgressView()
                        .frame(maxWidth: .infinity, minHeight: 160)
                        .accessibilityLabel(PhoneMediaStrings.readingVideos)
                }
                Text(item.name)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 16)
                Spacer(minLength: 0)
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(FileStrings.close) {
                        // Stopped before the sheet goes, not left to
                        // deallocation: an `AVPlayer` that is still playing
                        // when its view goes away keeps the audio session,
                        // and this app's session belongs to the music player.
                        player?.pause()
                        dismiss()
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    // **Only once there is a file.** The control is not drawn
                    // while the URL is unknown, rather than drawn and
                    // failing — the owner's rule, and the reason the share
                    // state is a URL and not a boolean.
                    if shareable != nil {
                        Button {
                            sharing = true
                        } label: {
                            Image(systemName: "square.and.arrow.up")
                                .frame(minWidth: 44, minHeight: 44)
                                .contentShape(.rect)
                        }
                        .accessibilityLabel(VideoStrings.share)
                    }
                }
            }
            .sheet(isPresented: $sharing) {
                if let shareable {
                    FileShareSheet(url: shareable)
                }
            }
        }
        .task(id: item.id) { await open() }
        .onDisappear { player?.pause() }
    }

    /// Resolve the item to something `AVPlayer` can take.
    ///
    /// Two origins and no third. A file in a picked folder is opened inside
    /// its folder's security scope — the grant is on the **tree**, so the
    /// scope is started on the folder and not on the file, which is
    /// ``PhoneFolderSource/bytes(of:)``'s rule and the one easy thing to get
    /// wrong here. An asset goes through `PHImageManager`, which hands back
    /// an `AVPlayerItem` and downloads from iCloud when it has to.
    private func open() async {
        switch item.origin {
        case .file(let url):
            player = AVPlayer(url: url)
            shareable = url
        case .asset(let identifier):
            guard let made = await Self.playerItem(of: identifier) else {
                trouble = PhoneMediaStrings.cannotReadOnThisPhone(item.name)
                return
            }
            player = AVPlayer(playerItem: made.item)
            shareable = made.url
        }
        player?.play()
    }

    /// An asset's player item, and the file behind it when there is one.
    ///
    /// `isNetworkAccessAllowed`, so a video somebody has optimised storage
    /// for plays at all. The URL comes from the item's own asset when it is a
    /// `AVURLAsset`; a composition — a slow-motion video, a Live Photo's
    /// movie — has no single file, so there is **no share control** for one
    /// rather than a share of something that is not the video.
    private static func playerItem(
        of identifier: String
    ) async -> (item: AVPlayerItem, url: URL?)? {
        let assets = PHAsset.fetchAssets(withLocalIdentifiers: [identifier], options: nil)
        guard let asset = assets.firstObject else { return nil }
        let options = PHVideoRequestOptions()
        options.isNetworkAccessAllowed = true
        options.deliveryMode = .automatic
        return await withCheckedContinuation { resume in
            PHImageManager.default().requestPlayerItem(
                forVideo: asset,
                options: options
            ) { made, _ in
                guard let made else {
                    resume.resume(returning: nil)
                    return
                }
                let url = (made.asset as? AVURLAsset)?.url
                resume.resume(returning: (made, url))
            }
        }
    }
}

// No share-sheet view of its own: ``FileShareSheet`` (`FileViewers.swift:212`)
// already wraps `UIActivityViewController` for exactly this — a file URL, no
// `subject` KVC — and a second copy of it would be a second place for that
// `NSUnknownKeyException` note to be forgotten. The difference between the
// two callers is in what they hand over, not in the sheet: ``FileSaveModel``
// spools a server file to a temporary copy and deletes it on dismissal, while
// a phone video is the person's own file and is shared where it lies.
