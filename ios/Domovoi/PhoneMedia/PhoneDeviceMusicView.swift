import DomovoiKit
import SwiftUI

/// The device side of Music: the audio in the folders the person added.
///
/// `ui/screens/local/LocalMediaScreens.kt:118`'s `LocalMusicScreen` over
/// `:136`'s `LocalMusicList`, and `YourDevice.kt:262`'s
/// `YourDeviceMusic()`.
///
/// ### What is not, and why there is no control for it
///
/// * **The phone's own songs are not read at all.** Android lists every track
///   on the device from `MediaStore.Audio` (`LocalMedia.kt:59`). That needs
///   `MPMediaQuery` and `NSAppleMusicUsageDescription`, and two independent
///   things make the rows useless: there is no handset audio engine in this
///   app, so nothing would play one, and an `MPMediaItem` that is
///   DRM-protected has `assetURL == nil`, so nothing could upload one
///   either. A list where neither control works is exactly what the owner's
///   rule forbids ([[no-misleading-controls]]), so the permission is **not**
///   asked for and this side is the folders instead — readable, uploadable,
///   and honest about not playing here. DECISIONS asks Kamron whether he
///   wants the read-only list anyway.
/// * **No play.** ``PhoneMediaStrings/noPhonePlayerYet`` says so at the foot
///   of the list, once, rather than a disabled arrow on every row.
/// * **No queue, no favourites, no "play on a room".** Those are the server's
///   library's, and a phone file is not in it until it has been uploaded.
struct PhoneDeviceMusicView: View {

    let model: ShellModel

    /// False in the local shell: no server, so no upload.
    var offersUpload = true

    @State private var device: PhoneDevicePage?
    @State private var visible = false

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
                            // A folder opens; a track does not play here.
                            onTap: item.isDir ? { device.list.enter(item) } : nil
                        )
                    }
                    // Said once, at the foot, because it is about the whole
                    // side and not about a row.
                    if !device.list.rows.state.items.isEmpty {
                        Text(PhoneMediaStrings.noPhonePlayerYet)
                            .font(.caption)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 8)
                    }
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .modifier(PhoneDeviceLifecycle(
            page: .music,
            model: model,
            offersUpload: offersUpload,
            device: $device,
            visible: $visible
        ))
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
