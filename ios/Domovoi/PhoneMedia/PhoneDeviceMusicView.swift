import DomovoiKit
import SwiftUI

/// The device side of Music: the audio in the folders the person added.
///
/// `ui/screens/local/LocalMediaScreens.kt:118`'s `LocalMusicScreen` over
/// `:136`'s `LocalMusicList`, and `YourDevice.kt:262`'s
/// `YourDeviceMusic()`.
///
/// ### Playing arrived in the 2026-11-03 batch
///
/// A tap on a track plays it on the handset, with the neighbouring tracks
/// behind it (``PhoneMediaListModel/playQueue(around:)``), on the app's one
/// player (``ShellModel/player``) — lock screen, Now Playing and all.
///
/// **In the local shell too**, which review 2 of the 2026-11-03 batch
/// corrected: this file said a phone with no server had no device id and so
/// no player, and all three clauses in the middle were wrong. The id is
/// minted locally on first launch (`DeviceRegistry.swift:391-403`),
/// `AppShellView` hands the local shell the same ``ShellModel``, and
/// ``PlayerModel`` plays a `file://` track without a server
/// (`resolve(_:)`). The foot note that explained the absence is deleted with
/// the premise it rested on.
///
/// ### What is not, and why there is no control for it
///
/// * **The phone's own songs are not read at all.** Android lists every track
///   on the device from `MediaStore.Audio` (`LocalMedia.kt:59`). That needs
///   `MPMediaQuery` and `NSAppleMusicUsageDescription`, and an `MPMediaItem`
///   that is DRM-protected has `assetURL == nil` — so neither `AVPlayer` nor
///   the uploader can open one, and a list of rows where both controls fail is
///   what the owner's rule forbids ([[no-misleading-controls]]). The
///   permission is **not** asked for and this side is the picked folders
///   instead, which are readable, uploadable and now playable. DECISIONS asks
///   Kamron whether he wants the read-only list anyway.
///
///   (Until the 2026-11-03 batch there was a second reason — no handset
///   engine at all. That one is gone: D46 put `play` on these rows.)
/// * **No queue, no favourites, no "play on a room".** Those are the server's
///   library's, and a phone file is not in it until it has been uploaded.
///   What a tap does here is start the phone's own player on the phone's own
///   file, which is Android's behaviour too (`LocalMediaScreens.kt:158`).
struct PhoneDeviceMusicView: View {

    let model: ShellModel

    /// False in the local shell: no server, so no upload.
    var offersUpload = true

    @State private var device: PhoneDevicePage?
    @State private var visible = false

    /// The app's one player, or nil on a build with no audio engine.
    ///
    /// Held in `@State` and taken in a `.task`, never read from `body`:
    /// reading ``ShellModel/player`` is what **builds** it, and a build during
    /// a view update is the shape SwiftUI complains about. It is also how
    /// ``PodcastsView`` does it.
    @State private var player: PlayerModel?

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 10) {
                PhoneDeviceHeader(
                    title: PhoneMediaStrings.musicTitle,
                    subtitle: offersUpload
                        ? PhoneMediaStrings.deviceMusicSubtitle
                        : PhoneMediaStrings.unconnectedSubtitle
                )
                if let device {
                    PhoneDeviceBody(
                        device: device,
                        model: model,
                        noun: "tracks",
                        noMatch: PhoneMediaStrings.noTracksMatch,
                        nothingHere: PhoneMediaStrings.noMusicOnDevice,
                        loadingLine: PhoneMediaStrings.readingLibrary
                    ) { item in
                        PhoneMediaRowView(
                            item: item,
                            meta: device.list.meta(for: item),
                            uploads: device.uploads,
                            onTap: tap(item, device)
                        )
                    }
                    // A foot note saying "a phone track plays here once this
                    // phone has joined a domovoi" stood here and is gone
                    // (review 2): the player is not keyed to pairing, so the
                    // note was drawn in no state a person reaches and was
                    // wrong about why. `player` is still taken in the `.task`
                    // below because `tap(_:_:)` needs it; when it is nil the
                    // rows get no tap and the page says nothing, which is the
                    // honest shape of a build with no audio engine.
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // A handle that survives the list scrolling: the subtitle a case
        // used to find this `ScrollView` by is a `LazyVStack` child and
        // unbuilds (``PhoneDeviceLists``).
        .accessibilityIdentifier(PhoneDeviceLists.music)
        .modifier(PhoneDeviceLifecycle(
            page: .music,
            model: model,
            offersUpload: offersUpload,
            device: $device,
            visible: $visible
        ))
        .task { player = model.player }
        // A move brings a different scope with it, and the shell re-points
        // the player it holds rather than rebuilding it
        // (``ShellModel/serverChanged()``) — so this takes the slot again
        // rather than trusting the one read on the first appearance.
        .onChange(of: model.inputs.serverURL) { _, _ in
            guard visible else { return }
            player = model.player
        }
    }

    /// What tapping a row does, or nil when a tap would do nothing.
    ///
    /// A folder opens. A track plays **on this phone**, as Android's does
    /// (`LocalMediaScreens.kt:158-166`): the queue is the playable rows in
    /// this folder, the index is the tapped one, and the player cuts the
    /// window. Nil for a track when there is no player, and a row with no tap
    /// is drawn as a row rather than as a dead button
    /// (``PhoneMediaRowView/onTap``).
    ///
    /// The queue is built **in the closure**, when the tap happens, not when
    /// the row is drawn: a lazy list would otherwise map every visible row's
    /// whole folder into `PlayerItem`s on every scroll.
    ///
    /// Which is also why the row can be **gone** by the time the closure runs:
    /// the folder is re-read while the list is on screen. ``PhoneMediaListModel/playQueue(around:)``
    /// answers nil for that, and this says so rather than starting whatever is
    /// now at the top of the folder — a different song from the one under the
    /// thumb.
    private func tap(_ item: PhoneMediaItem, _ device: PhoneDevicePage) -> (() -> Void)? {
        if item.isDir { return { device.list.enter(item) } }
        guard let player, device.list.canPlay(item) else { return nil }
        return {
            guard let queue = device.list.playQueue(around: item) else {
                model.showToast(PhoneMediaStrings.thatTrackIsGone)
                return
            }
            // The paused room, if this phone was casting, is named by
            // ``PlayerModel/say`` in the shell's toast once the room has
            // answered the pause — not here, where the answer has not arrived.
            _ = player.play(queue.items, startAt: queue.index)
        }
    }
}

/// The middle of every device page: the picker, the refused card, the
/// breadcrumb, the filter, the count, the rows and the foot.
///
/// Generic over one row, so Music and Files share it and the Videos grid
/// does not (a grid wants its own container and its own near-end
/// threshold).
struct PhoneDeviceBody<Row: View>: View {

    let device: PhoneDevicePage
    let model: ShellModel
    let noun: String
    let noMatch: String
    let nothingHere: String
    let loadingLine: String
    @ViewBuilder let row: (PhoneMediaItem) -> Row

    private var list: PhoneMediaListModel { device.list }

    var body: some View {
        // **Six of these, in one child.** A `@ViewBuilder` takes at most ten
        // children and this body had exactly ten, which is a limit a later
        // line would have hit with an error nobody reading the diff would
        // expect.
        PhoneDeviceTopMatter(device: device, model: model, noun: noun)
        // Four states, not two: nothing picked, a read in flight, a read that
        // failed, and an answer that is empty. `loaded` is what tells the
        // middle two apart (``PagedState/loaded``).
        if list.hasNoLibraries {
            PhoneDeviceNote(
                title: PhoneMediaStrings.addAFolder,
                detail: PhoneMediaStrings.phoneFilesSubtitle
            )
        } else if list.selectedID == nil || !list.rows.state.loaded {
            HStack(spacing: 8) {
                ProgressView()
                Text(loadingLine)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        ForEach(Array(list.rows.state.items.enumerated()), id: \.element.id) { index, item in
            row(item)
                .onAppear {
                    if list.shouldLoadMore(lastVisible: index) { list.rows.loadMore() }
                }
            Divider()
        }
        if let empty = list.emptyNote(
            noMatch: noMatch,
            nothingHere: nothingHere,
            nothingHereDetail: PhoneMediaStrings.downloadsLandHere
        ) {
            PhoneDeviceNote(title: empty.title, detail: empty.detail)
        }
        PhoneListFooter(list: list)
    }
}

/// What every device page draws above its rows: which library, whether
/// uploads are off, how to add a folder, where in the folder tree this is,
/// the filter, and how many rows of how many.
struct PhoneDeviceTopMatter: View {

    let device: PhoneDevicePage
    let model: ShellModel
    let noun: String

    private var list: PhoneMediaListModel { device.list }

    var body: some View {
        PhoneLibraryPicker(list: list)
        if let refused = device.uploads?.refused {
            PhoneUploadRefusedCard(reason: refused.reason, blocked: refused.blocked)
        }
        // The two doors that need no permission, and the folder list as the
        // extra step: the simple default first, as Kamron's standing note
        // asks, with the browse underneath for somebody who wants to see
        // everything at once.
        if let uploads = device.uploads {
            PhonePickToSendView(
                uploads: uploads,
                // The photo picker only where photos are the point. The
                // device side of Music is folder audio, and PHPicker cannot
                // pick a song at all.
                photos: device.page != .music,
                files: true
            )
        }
        PhoneFolderPickerLink(device: device, model: model)
        PhoneBreadcrumb(list: list)
        PhoneFilterField(list: list)
        if let count = list.countText(noun: noun) {
            Text(count)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// The library root and a crumb per folder, scrolling sideways — the shape
/// ``FilesView``'s trail has, for its reason: a path four folders deep does
/// not fit across a phone, and eliding the middle hides the segment somebody
/// is trying to tap back to.
struct PhoneBreadcrumb: View {

    let list: PhoneMediaListModel

    var body: some View {
        if !list.breadcrumb.isEmpty {
            ScrollView(.horizontal) {
                HStack(spacing: 4) {
                    Button {
                        list.goToRoot()
                    } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "house.fill").font(.caption)
                            Text(list.currentLibrary?.label ?? FileStrings.libraryRoot)
                                .font(.caption)
                                .lineLimit(1)
                        }
                        .frame(minHeight: 44)
                        .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(Color.brandInk)
                    .accessibilityLabel(FileStrings.libraryRoot)

                    ForEach(Array(list.breadcrumb.enumerated()), id: \.offset) { index, segment in
                        Text("/")
                            .font(.caption)
                            .foregroundStyle(Color.fgMuted)
                        Button {
                            list.goToCrumb(index)
                        } label: {
                            Text(segment)
                                .font(.caption)
                                .lineLimit(1)
                                .frame(minHeight: 44)
                                .contentShape(.rect)
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(
                            index == list.breadcrumb.count - 1 ? Color.primary : Color.brandInk
                        )
                    }
                }
            }
            .scrollIndicators(.hidden)
        }
    }
}

/// Build the page, start it, stop it, and rebuild it when the server or the
/// folder list changes.
///
/// A `ViewModifier` rather than five modifiers copied into three screens: the
/// `onChange(of: model.inputs.serverURL)` guarded by `visible` is the one
/// every screen on this track has had to get right, and three copies is three
/// chances to leave the guard out.
struct PhoneDeviceLifecycle: ViewModifier {

    let page: PhoneMediaPageKind
    let model: ShellModel
    let offersUpload: Bool
    @Binding var device: PhoneDevicePage?
    @Binding var visible: Bool

    func body(content: Content) -> some View {
        content
            .task {
                if device == nil {
                    device = PhoneDevicePage(
                        page: page,
                        in: model.container,
                        deviceID: model.deviceID,
                        offersUpload: offersUpload
                    )
                }
                device?.start()
            }
            // `onAppear`/`onDisappear` for ``HomeView``'s reason: this screen
            // is inside the workspace's `NavigationStack`, so a pushed
            // destination fires `onDisappear` while this view stays in the
            // hierarchy.
            .onAppear { visible = true }
            .onDisappear {
                visible = false
                device?.stop()
            }
            .onChange(of: model.inputs.serverURL) { _, _ in
                guard visible else { return }
                device?.serverChanged()
            }
            // **The toast is the shell's**, drawn over the whole app: a toast
            // about an upload that failed must outlive a scroll, and it does
            // outlive this screen.
            .onChange(of: device?.uploads?.toast) { _, said in
                guard let said else { return }
                model.showToast(said)
                device?.uploads?.setToast(nil)
            }
    }
}
