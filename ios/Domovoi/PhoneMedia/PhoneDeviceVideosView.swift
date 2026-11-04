import DomovoiKit
import Photos
import SwiftUI

/// The device side of Videos: this phone's own videos, as a grid of posters.
///
/// `ui/screens/local/LocalMediaScreens.kt:276`'s `LocalVideosScreen` over
/// `:294`'s `LocalVideosGrid`, and `YourDevice.kt:269`'s
/// `YourDeviceVideos()`.
///
/// ### What is not, and why there is no control for it
///
/// * **No swipe feed.** Android opens the very same `VideoFeedPlayer` the
///   server's videos use (`LocalMediaScreens.kt:367`). ``FeedVideo`` holds a
///   server-relative `streamPath` (`VideoFeedModels.swift:31`) that
///   ``VideoStream`` resolves; a `PHAsset` has no such path, and `FeedVideo`
///   is shared infrastructure this item may not edit. One video at a time in
///   ``PhoneVideoPlayerView`` instead, and the shared change that would
///   unlock the feed is in DECISIONS.
/// * **No "centre the video you ended on".** `gridState.centerOn`
///   (`LocalMediaScreens.kt:308`) belongs to the feed: with one video at a
///   time, the grid never moved.
/// * **Sharing is ON here**, and off for every server video — the one place
///   in the app where those differ. A phone video is the person's own file;
///   a server video's stream URL carries the household token
///   (``VideoStream``, and ``FeedVideo/shareable`` is already false for every
///   server row), so handing one to another app would hand over the token.
struct PhoneDeviceVideosView: View {

    let model: ShellModel

    var offersUpload = true

    @State private var device: PhoneDevicePage?
    @State private var visible = false

    /// The one video open over the grid, if any.
    @State private var playing: PhoneMediaItem?

    /// What the photo library said last time it was asked, so the limited
    /// state and the gate can be drawn. Read on appearing, not asked for:
    /// the system alert arrives on a tap.
    @State private var access = PhonePhotosSource.access

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 10) {
                PhoneDeviceHeader(
                    title: PhoneMediaStrings.videosTitle,
                    subtitle: offersUpload
                        ? PhoneMediaStrings.deviceVideosSubtitle
                        : PhoneMediaStrings.unconnectedSubtitle
                )
                photoAccess
                if let device {
                    PhoneDeviceTopMatter(device: device, model: model, noun: "videos")
                    grid(device)
                    if let empty = device.list.emptyNote(
                        noMatch: PhoneMediaStrings.noTracksMatch,
                        nothingHere: PhoneMediaStrings.noVideosOnDevice,
                        nothingHereDetail: PhoneMediaStrings.downloadsLandHere
                    ) {
                        PhoneDeviceNote(title: empty.title, detail: empty.detail)
                    }
                    PhoneListFooter(list: device.list)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // A handle that survives the list scrolling: the subtitle a case
        // used to find this `ScrollView` by is a `LazyVStack` child and
        // unbuilds (``PhoneDeviceLists``).
        .accessibilityIdentifier(PhoneDeviceLists.videos)
        .modifier(PhoneDeviceLifecycle(
            page: .videos,
            model: model,
            offersUpload: offersUpload,
            device: $device,
            visible: $visible
        ))
        // One `.sheet`, as ``FilesView`` and ``VideoFeedSheetView`` have it:
        // several `.sheet` modifiers on one view do not reliably coexist in
        // SwiftUI — the last attached wins and the others silently never
        // present.
        .sheet(item: $playing) { item in
            PhoneVideoPlayerView(item: item)
        }
    }

    // MARK: - what iOS says about the library

    /// The gate, the limited-access line, or nothing.
    ///
    /// Three states where Android's `PermissionGate`
    /// (`LocalMediaScreens.kt:89`) has two: `.limited` is a real answer iOS
    /// gives and Android has no equivalent of, and **not** drawing it would
    /// show a library with four videos in it and no explanation, which reads
    /// as a broken screen (DECISIONS, IOS-ONLY-STRINGS).
    @ViewBuilder private var photoAccess: some View {
        switch access {
        case .notDetermined, .denied, .restricted:
            VStack(alignment: .leading, spacing: 8) {
                PhoneDeviceNote(
                    title: PhoneMediaStrings.noAccessToVideos,
                    detail: PhoneMediaStrings.permissionNeeded
                )
                // Only when there is something an allow could change. Once
                // the person has said no, iOS will not ask again — the button
                // would open nothing — so after a refusal the sentence above
                // stands alone and the folders below are the way in.
                if access == .notDetermined {
                    HomeLink(PhoneMediaStrings.allowAccess) {
                        Task {
                            access = await PhonePhotosSource.ask()
                            device?.list.reloadLibraries()
                        }
                    }
                }
            }
        case .limited:
            VStack(alignment: .leading, spacing: 8) {
                Text(PhoneMediaStrings.limitedPhotoAccess)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(PhoneMediaStrings.chooseMorePhotos) {
                    // iOS's own limited-library picker, which is the only
                    // control that can change this state from inside the app.
                    Task { await PhonePhotosSource.chooseMore() }
                }
            }
        case .authorized:
            EmptyView()
        @unknown default:
            EmptyView()
        }
    }

    // MARK: - the grid

    /// `LazyVGrid`, adaptive, 16:9 tiles — Android's adaptive 160 dp with
    /// 12 dp gaps (`LocalMediaScreens.kt:325`).
    ///
    /// The minimum is a `@ScaledMetric` rather than a flat 160: at an
    /// accessibility size a caption of two wrapped lines under a 160-point
    /// tile is taller than the tile, and a grid that keeps four columns at
    /// AX5 is four columns of unreadable captions.
    @ViewBuilder private func grid(_ device: PhoneDevicePage) -> some View {
        let rows = device.list.rows.state.items
        if device.list.selectedID != nil, !device.list.rows.state.loaded, rows.isEmpty {
            HStack(spacing: 8) {
                ProgressView()
                Text(PhoneMediaStrings.readingVideos)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        LazyVGrid(columns: [GridItem(.adaptive(minimum: tileWidth), spacing: 12)], spacing: 12) {
            ForEach(Array(rows.enumerated()), id: \.element.id) { index, item in
                PhoneVideoTile(
                    item: item,
                    posters: device.posters,
                    uploads: device.uploads,
                    // **A folder tile enters the folder; it does not play.**
                    // Android's grid is `MediaStore.Video`, which is flat and
                    // has no folders in it at all
                    // (`LocalMediaScreens.kt:294`); a picked folder can have
                    // sub-folders, and ``PhoneMemorySource``/``PhoneFolderSource``
                    // keep them in a kind-narrowed page on purpose — hiding
                    // them would hide the way down to the videos inside. A
                    // play badge over one would be the misleading control the
                    // owner's rule forbids.
                    onOpen: item.isDir
                        ? { device.list.enter(item) }
                        : { playing = item }
                )
                .onAppear {
                    // The grid threshold, not the list one: a grid builds
                    // more cells per screen, so the near-end guard has to
                    // fire earlier (``Paging/gridThreshold``).
                    if device.list.shouldLoadMore(
                        lastVisible: index,
                        threshold: Paging.gridThreshold
                    ) {
                        device.list.rows.loadMore()
                    }
                }
            }
        }
    }

    @ScaledMetric(relativeTo: .body) private var tileWidth: CGFloat = 160
}

/// One phone video: a poster, a play badge, and the caption Android draws —
/// the name and how long it runs (`LocalMediaScreens.kt:389`).
///
/// ### The caption stacks at an accessibility type size
///
/// The same squeeze ``PhoneMediaRowView`` was measured with in train I002: a
/// caption that wraps beside a trailing `@ScaledMetric` 48-point upload box
/// (about 135 points at AX5) in a tile about 361 wide. The box keeps scaling
/// and moves under the caption instead, so the name has the tile's width.
struct PhoneVideoTile: View {

    let item: PhoneMediaItem
    let posters: PhonePosterStore
    let uploads: PhoneUploadModel?

    /// Play a video, or walk into a folder — the caller decides which, and
    /// the tile draws itself to match.
    let onOpen: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button(action: onOpen) {
                Rectangle()
                    // Opaque, never a material: a contrast audit cannot read
                    // a colour that is whatever happens to be behind it, and
                    // this tile has a play badge drawn on top of it
                    // (``VideoPosterTile``'s note, which cost five rounds of
                    // misdiagnosis).
                    .fill(Color(.secondarySystemBackground))
                    .overlay { frame }
                    .overlay { badge }
                    .clipShape(.rect(cornerRadius: 8))
                    .overlay {
                        RoundedRectangle(cornerRadius: 8)
                            .strokeBorder(Color(.separator), lineWidth: 1)
                    }
                    .aspectRatio(16 / 9, contentMode: .fit)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            // The video, not the word `play`: VoiceOver reads a grid of forty
            // of these, and forty rows saying "play" name nothing.
            .accessibilityLabel(item.name)
            .accessibilityHint(item.isDir ? FileStrings.folder : VideoStrings.play)

            captionLayout {
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.name)
                        .font(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    if item.durationSec != nil {
                        Text(item.durationText)
                            .font(.caption2)
                            .foregroundStyle(Color.fgMuted)
                    }
                }
                .frame(maxWidth: captionMaxWidth, alignment: .leading)
                // Horizontal branch only: inside a `VStackLayout` a `Spacer`
                // is vertical padding, not a gap beside the caption.
                if !typeSize.isAccessibilitySize { Spacer(minLength: 2) }
                // One branch, not two: ``PhoneUploadModel/offersUpload(_:)``
                // is `!item.isDir` since the transfers unit's streaming
                // upload took the 64 MiB ceiling out, so the `else` that drew
                // `PhoneUploadTooBigNote` was unreachable.
                if let uploads, !item.isDir, uploads.offersUpload(item) {
                    PhoneUploadRowButton(
                        state: uploads.state(for: PhoneUploadKey.of(item, uploads.kind)),
                        onTap: { uploads.upload(item) }
                    )
                }
            }
        }
        // Asked when the grid BUILDS this cell, not when the grid is
        // created: a lazy grid of four hundred videos must not queue four
        // hundred PhotoKit requests, which is what a `.task` on a parent
        // would do.
        .task(id: assetID) { if let assetID { posters.want(assetID) } }
    }

    /// The app's pattern for a row that changes shape; the same one
    /// ``PhoneMediaRowView`` uses.
    @Environment(\.dynamicTypeSize) private var typeSize

    /// The caption and the upload box: a row, or a column at an
    /// accessibility size.
    private var captionLayout: AnyLayout {
        typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 6))
            : AnyLayout(HStackLayout(alignment: .top, spacing: 6))
    }

    /// Flexible only in the vertical branch, so the ordinary tile is laid out
    /// exactly as it was. `CGFloat.infinity` spelt out rather than
    /// `.infinity` in a ternary with `nil`.
    private var captionMaxWidth: CGFloat? {
        typeSize.isAccessibilitySize ? CGFloat.infinity : nil
    }

    /// The asset this tile's poster belongs to, or nil for a video in a
    /// picked folder — nothing makes a thumbnail of one of those, because
    /// `AVAssetImageGenerator` per cell is a decode per cell.
    private var assetID: String? {
        guard case .asset(let identifier) = item.origin else { return nil }
        return identifier
    }

    @ViewBuilder private var frame: some View {
        if item.isDir {
            Image(systemName: "folder.fill")
                .font(.system(size: 26))
                .foregroundStyle(Color.fgMuted)
                .accessibilityHidden(true)
        } else if let assetID, let image = posters.poster(assetID) {
            Image(uiImage: image)
                .resizable()
                .aspectRatio(contentMode: .fill)
        } else {
            // The film glyph, and **no spinner**: a grid of spinners on a
            // slow library is noise, and it is what a tile settles to for
            // every video in a picked folder.
            VideoGlyph()
        }
    }

    /// The play circle — **not drawn on a folder**, which gets the folder
    /// glyph as its whole face instead.
    @ViewBuilder private var badge: some View {
        if !item.isDir {
            playBadge
        }
    }

    private var playBadge: some View {
        Image(systemName: "play.fill")
            .font(.system(size: 18, weight: .semibold))
            .foregroundStyle(Color.brandFg)
            .frame(width: 40, height: 40)
            .background(Color.brand, in: .circle)
            .accessibilityHidden(true)
    }
}
