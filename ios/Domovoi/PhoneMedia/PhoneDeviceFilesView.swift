import DomovoiKit
import SwiftUI

/// The device side of Files: the folders the person added, and their photos.
///
/// `ui/screens/files/PhoneFiles.kt:70`'s `LocalFilesScreen` and
/// `YourDevice.kt:277`'s `YourDeviceFiles()`, which on Android are three
/// lines because its Files screen is one `FilesBrowser` over a `FileSource`
/// interface.
///
/// ### Why this is a second browser and not ``FilesView``
///
/// The iOS ``FilesModel`` is bound to the server's routes directly — an
/// ``ApiRead`` over `/api/files/libraries` and a ``Pager`` over
/// `/api/files/browse` (`FilesModel.swift:58,76`), with `move`, `importInto`
/// and `rescan` calling `/api/files/*` by hand — so it has **no source
/// seam**. Giving it one is a change to a do-not-edit file, so it is written
/// down and not made (DECISIONS, "Needs a shared change" 3), and the two
/// browsers will drift until a later batch merges them.
///
/// ### What is not, and why there is no control for it
///
/// * **No write of any kind on the phone** — no delete, no rename, no move
///   between folders, no new folder. Android gets all of those free from
///   `FileSource`; here nothing writes, so no control claims to.
/// * **No library-and-folder picker for an upload.** Android opens
///   `SendHomeSheet` over `/api/files/browse?kind=folder` and lets the person
///   choose any editable library and any folder (`PhoneFiles.kt:291`). An
///   upload from here goes to the **default** library for the file's kind —
///   ``phoneDefaultHomeLibrary(_:_:)``, ported exactly — and the toast says
///   where it went. The sheet is a screen of its own; its absence is stated,
///   not stubbed.
/// * **No "send to home" from the image viewer.** Same reason: that control
///   is the sheet above. A picture's row has the ordinary upload button.
/// * **The photo library is flat**, not one folder per album
///   (``PhonePhotosSource``'s own note).
struct PhoneDeviceFilesView: View {

    let model: ShellModel

    var offersUpload = true

    @State private var device: PhoneDevicePage?
    @State private var visible = false

    /// The picture being looked at, if any. One sheet, as every screen in
    /// this app has: several `.sheet` modifiers on one view do not reliably
    /// coexist in SwiftUI.
    @State private var looking: PhoneMediaItem?

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 10) {
                PhoneDeviceHeader(
                    title: FileStrings.title,
                    subtitle: PhoneMediaStrings.phoneFilesSubtitle
                )
                if let device {
                    PhoneDeviceBody(
                        device: device,
                        model: model,
                        noun: "items",
                        noMatch: FileStrings.noMatch,
                        nothingHere: FileStrings.emptyFolder,
                        loadingLine: PhoneMediaStrings.readingLibrary
                    ) { item in
                        PhoneMediaRowView(
                            item: item,
                            meta: device.list.meta(for: item),
                            uploads: device.uploads,
                            onTap: tap(item, device)
                        )
                    }
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // A handle that survives the list scrolling: the subtitle a case
        // used to find this `ScrollView` by is a `LazyVStack` child and
        // unbuilds (``PhoneDeviceLists``).
        .accessibilityIdentifier(PhoneDeviceLists.files)
        .modifier(PhoneDeviceLifecycle(
            page: .files,
            model: model,
            offersUpload: offersUpload,
            device: $device,
            visible: $visible
        ))
        .sheet(item: $looking) { item in
            PhoneImageViewerSheet(item: item, source: device)
        }
    }

    /// What tapping a row does, or nil when a tap would do nothing.
    ///
    /// Three answers, and the third is the one worth naming: a folder opens,
    /// a picture is looked at, and **everything else has no tap** — a
    /// document is not read here (the reader is
    /// ``FilesView``'s, over the server's `/api/documents/text` route, and a
    /// phone file is not on the server). Those rows are drawn as rows and not
    /// as dead buttons.
    ///
    /// **Audio is deliberately among them**, even though the phone can play it
    /// since D46. Android's `PhoneFiles.kt` has no play either — only
    /// `LocalMusicScreen` does — and the reason holds on both: a tap in a
    /// file browser opens the thing, and starting a queue of "the playable
    /// rows around this one" out of a mixed folder of documents, photographs
    /// and one `.m4a` is a queue nobody asked for. The device side of Music is
    /// where a folder is a list of tracks.
    private func tap(_ item: PhoneMediaItem, _ device: PhoneDevicePage) -> (() -> Void)? {
        if item.isDir { return { device.list.enter(item) } }
        if item.kind == .image { return { looking = item } }
        if item.kind == .video { return { looking = item } }
        return nil
    }
}

/// A picture or a video from this phone, looked at.
///
/// `PhoneFiles.kt:159`'s `ImageViewerOverlay`, with its pinch-zoom left to
/// the platform: `ScrollView`'s own double-tap and pinch do what Android's
/// hand-written 1–6× gesture does, and a hand-written one here would be a
/// second gesture recogniser fighting the first.
///
/// A video opens ``PhoneVideoPlayerView`` instead, because that is what plays
/// one.
///
/// ### What is not, and why there is no control for it
///
/// **No "send to home".** Android's viewer has it (`PhoneFiles.kt:171`) and
/// it opens the library-and-folder sheet this item does not build; the row's
/// own upload button is the way home, and it says where the file went.
/// **"open in another app"** is the share sheet, which is offered for a phone
/// file because it is the person's own.
struct PhoneImageViewerSheet: View {

    let item: PhoneMediaItem
    let source: PhoneDevicePage?

    @Environment(\.dismiss) private var dismiss
    @State private var bytes: Data?
    @State private var trouble = false

    var body: some View {
        if item.kind == .video {
            PhoneVideoPlayerView(item: item)
        } else {
            NavigationStack {
                VStack(spacing: 12) {
                    picture
                    Text(item.name)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 16)
                    Spacer(minLength: 0)
                }
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button(FileStrings.close) { dismiss() }
                    }
                }
            }
            .task(id: item.id) {
                // The bytes come from the **page's** source, which knows
                // which part of the phone this row is on. Read here rather
                // than in the row, so a row scrolled off screen cannot
                // cancel a picture somebody is looking at.
                guard let source else {
                    trouble = true
                    return
                }
                do { bytes = try await source.read(item) } catch { trouble = true }
            }
        }
    }

    @ViewBuilder private var picture: some View {
        if let bytes, let image = UIImage(data: bytes) {
            ScrollView([.horizontal, .vertical]) {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
            }
            .accessibilityLabel(item.name)
        } else if trouble || bytes != nil {
            // `bytes != nil` and no image means the phone handed over bytes
            // that are not a picture it can decode — a format, not an
            // absence, and Android's own sentence for it
            // (`PhoneFiles.kt:196`).
            PhoneDeviceNote(
                title: PhoneMediaStrings.imageCannotBeShown,
                detail: PhoneMediaStrings.cannotReadOnThisPhone(item.name)
            )
            .padding(.horizontal, 16)
        } else {
            ProgressView()
                .frame(maxWidth: .infinity, minHeight: 160)
                .accessibilityLabel(PhoneMediaStrings.readingLibrary)
        }
    }
}
