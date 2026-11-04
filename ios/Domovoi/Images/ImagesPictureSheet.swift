import DomovoiKit
import SwiftUI

/// One generated picture, full width, and the way out to another app.
///
/// ### Why the picture opens *here* and not in another app
///
/// Android hands the file over: `ServerFileTransfer.open` fetches the bytes
/// with the app's token, writes them to a `FileProvider` cache and fires
/// `Intent.ACTION_VIEW` (`ImagesScreen.kt:260-267`). iOS has no such
/// hand-off, and the comment Android leaves at `:258-260` says exactly why a
/// bare address cannot be handed over instead: `…/file/{name}` is device tier
/// and a URL without the household token is a 401.
///
/// So the picture opens in the app, which is the shape this app already uses
/// for a household picture (``FileImageSheet``, `FileViewers.swift:106`), and
/// the share sheet — ``FileShareSheet``, already what "save to device" means
/// here — is the way to Files, Photos or AirDrop. DECISIONS, "Tapping a tile
/// opens the picture in the app, not in another app".
///
/// ### What is not here, and why there is no control for it
///
/// **Setting it as wallpaper, editing it, or sending it to a room.** No
/// Android control, and no route in the plugin's client for any of them.
///
/// **A next/previous swipe through the generation's pictures.** Android opens
/// one picture at a time from the tile that was tapped and has no gallery;
/// building one here would be a screen Android does not have, and the three
/// tiles are a tap away behind the sheet.
struct ImagesPictureSheet: View {

    let name: String
    let model: ShellModel
    let onClose: () -> Void

    // No ``ImagesModel`` here. The first draft held one "so the sheet can
    // drop the fetch when the app moves house" and never read it, which is
    // the seam `[[reasoning-that-hides-a-bug]]` records — a property whose
    // doc comment is the only evidence it does anything. ``ImagesView``
    // closes this sheet on a house move (`looking = nil`), which is the
    // mechanism, and ``ImagesPictureModel``'s own epoch is what makes the
    // fetch in flight land nowhere.

    @State private var picture: ImagesPictureModel?

    /// The file this app has written for the share sheet, when one has been
    /// asked for.
    @State private var sharing: URL?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    content
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            // The file's own name, which is the only title there is: the
            // plugin names a generated file and nothing in Android's client
            // reads a friendlier label for it.
            .navigationTitle(name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(ImagesStrings.close, action: onClose)
                }
            }
        }
        .task {
            if picture == nil {
                let built = ImagesPictureModel(name: name, in: model.container)
                picture = built
                built.read()
            }
        }
        // The share sheet is presented over this one, so it is this view's
        // single `.sheet`: two `.sheet` modifiers on one view do not reliably
        // coexist in SwiftUI (``FilesView``'s note).
        .sheet(item: Binding(get: { sharing.map(ImagesShareFile.init) },
                             set: { sharing = $0?.url })) { file in
            FileShareSheet(url: file.url)
        }
    }

    @ViewBuilder private var content: some View {
        switch picture?.load ?? .loading {
        case .loading:
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
        case .bytes(let data):
            if let decoded = UIImage(data: data) {
                Image(uiImage: decoded)
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: .infinity)
                    // The name again: inside the sheet the navigation title
                    // is a separate element, and a picture with no label is
                    // an image VoiceOver skips.
                    .accessibilityLabel(name)
                shareLink(data)
            } else {
                // Bytes that arrived and would not decode. The model's own
                // floor covers "too few to be a picture"; this covers
                // `UIImage` saying no to something the server was happy to
                // send.
                Text(ImagesStrings.couldNotShowPicture)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
            }
        case .failed(let why):
            VStack(alignment: .leading, spacing: 8) {
                Text(why)
                    .font(.subheadline)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(ImagesStrings.retry) { picture?.read() }
            }
        }
    }

    /// The one way out, offered **only once the bytes are in hand**.
    ///
    /// A share control that appeared while the picture was still loading
    /// would have nothing to hand over, and this app's rule is that a control
    /// which cannot do what it says is not drawn
    /// ([[no-misleading-controls]]). The copy is written into the app's own
    /// container because that is what an activity sheet reads from — and the
    /// bytes are written, never the address: the address would carry the
    /// household token to whichever app took it, which is the thing Videos
    /// refuses to do at all (``VideosView``'s note).
    @ViewBuilder private func shareLink(_ data: Data) -> some View {
        HomeLink(FileStrings.verbSave) {
            if let written = ImagesPictureSheet.write(data, named: name) {
                sharing = written
            } else {
                // Nowhere to write, which is the app's own container: there
                // is no server detail to report, so the sentence is the whole
                // answer. The **shell's** toast, so it outlives this sheet
                // being dismissed.
                model.showToast(ImagesStrings.couldNotHandOverPicture)
            }
        }
    }

    /// Write the bytes where an activity sheet can read them, under their own
    /// name.
    ///
    /// A fresh subdirectory per share rather than the temporary directory's
    /// root, so two pictures with one name — from two houses, or one deleted
    /// and regenerated — cannot overwrite each other mid-share. The name is
    /// kept as it is rather than uniquified, because the share sheet shows
    /// the file's **name** and `kingfisher-1.png` is the wrong thing to hand
    /// somebody.
    ///
    /// **The name came from a server**, and a server is a thing that can be
    /// wrong or hostile, so it is hardened exactly as
    /// `FileSaveModel.safeName(for:)` hardens a library file's
    /// (`FileSave.swift:144-161`) — that method is `internal` to DomovoiKit
    /// and so not callable from here, which is a "Needs a shared change" note
    /// rather than a reason to skip the hardening. `../../something` must not
    /// write outside the folder, a name that is only dots must not produce a
    /// directory, and a colon is a path separator to some of Apple's own
    /// APIs.
    static func write(_ data: Data, named name: String) -> URL? {
        let folder = FileManager.default.temporaryDirectory
            .appending(path: "imagegen-share", directoryHint: .isDirectory)
            .appending(path: UUID().uuidString, directoryHint: .isDirectory)
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let file = folder.appending(path: Self.safeName(name), directoryHint: .notDirectory)
            try data.write(to: file, options: .atomic)
            return file
        } catch {
            return nil
        }
    }

    /// The last path component, by **both** separators (a Windows house's
    /// names hold backslashes), with the two characters that are not
    /// filenames taken out. `FileSave.swift:144-161`, said again because it
    /// cannot be called.
    static func safeName(_ raw: String) -> String {
        let tail = raw.split(whereSeparator: { $0 == "/" || $0 == "\\" }).last.map(String.init) ?? ""
        let trimmed = tail.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty || trimmed == "." || trimmed == ".." { return FileStrings.unnamedFile }
        return String(trimmed.map { $0 == ":" || $0 == "\0" ? "_" : $0 })
    }
}

/// The file the share sheet is open over, so `.sheet(item:)` has something to
/// key on.
struct ImagesShareFile: Identifiable, Equatable {
    let url: URL
    var id: String { url.path }

    init(_ url: URL) {
        self.url = url
    }
}
