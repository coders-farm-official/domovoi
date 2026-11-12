import DomovoiKit
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

/// The two doors that need no permission at all: pick a photo, or pick a
/// file, and it goes home.
///
/// Android has nothing quite like this — there, "upload" is a button on a row
/// of a list the app read with a media permission (`LocalMediaScreens.kt:204`),
/// so picking and uploading are the same gesture. On iOS **PHPicker and the
/// document picker run out of process**, which means they need no usage
/// string and no prompt: the person chooses, and only what they chose ever
/// reaches this app.
///
/// That makes this the **mass-appeal default** — tap, pick, it goes home —
/// with the folder list and the photo-library browse as the extra step for
/// somebody who wants to see everything at once (the owner's standing note:
/// keep defaults simple, extra steps optional).
///
/// ### What is not, and why there is no control for it
///
/// * **No multiple selection.** ``PhoneUploadModel`` posts one file per
///   request and reports one toast; a five-photo pick would be five uploads
///   with one message, and the one that failed would be the one nobody heard
///   about. The picker takes a single-item binding, which allows exactly one,
///   and the extra files are a later item's.
/// * **No destination choice**, for ``PhoneDeviceFilesView``'s reason: the
///   default library for the file's kind, named in the toast.
/// * **Nothing at all in the local shell**, where there is no server to send
///   to. The caller passes no uploader and this view is not drawn.
struct PhonePickToSendView: View {

    let uploads: PhoneUploadModel

    /// Which kind of door to offer. Videos offers the photo picker, Music the
    /// document picker, Files both.
    let photos: Bool
    let files: Bool

    @State private var picked: PhotosPickerItem?
    @State private var importing = false

    /// What the person is looking at while the bytes come across from the
    /// picker's process. A pick of a 4K video is not instant.
    @State private var reading: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 16) {
                if photos {
                    // The single-item `selection:` binding IS the limit:
                    // `maxSelectionCount:` only exists on the array overload,
                    // so asking for both does not compile.
                    PhotosPicker(
                        selection: $picked,
                        matching: .any(of: [.images, .videos])
                    ) {
                        Text(PhoneMediaStrings.pickAPhoto)
                            .font(.subheadline)
                            .frame(minHeight: 44)
                            .contentShape(.rect)
                    }
                    .foregroundStyle(Color.brandInk)
                    // The label CONTAINS the visible words rather than
                    // replacing them: a label of `photos` on a control that
                    // reads `upload a photo` breaks Voice Control's "tap
                    // upload a photo" — the words on screen must be sayable.
                    .accessibilityLabel(PhoneMediaStrings.pickAPhoto)
                }
                if files {
                    // Not `openInAnotherApp`: that is Android's open-with on a
                    // file you already have (`PhoneFileSource.kt:81`), the
                    // opposite direction from this door.
                    HomeLink(PhoneMediaStrings.pickAFile) { importing = true }
                }
            }
            if let reading {
                HStack(spacing: 8) {
                    ProgressView()
                    // Where it lands is the uploader's answer and it says so
                    // in its own toast; this line is only about the bytes
                    // coming across from the picker's process.
                    Text(PhoneMediaStrings.readingFromThisPhone(reading))
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .onChange(of: picked) { _, next in
            guard let next else { return }
            Task { await send(next) }
        }
        .fileImporter(
            isPresented: $importing,
            allowedContentTypes: [.item],
            allowsMultipleSelection: false
        ) { result in
            guard case .success(let urls) = result, let url = urls.first else { return }
            send(url)
        }
    }

    // MARK: - what a pick does

    /// A photo or a video from PHPicker.
    ///
    /// The bytes come over as `Data` from the picker's own process, so there
    /// is no asset and no file URL — the item handed to the uploader carries
    /// its bytes in a closure of its own, which is why
    /// ``PhoneUploadModel``'s `read` seam exists.
    private func send(_ item: PhotosPickerItem) async {
        // Cleared whichever way this goes, so a second pick of the same photo
        // is a second upload rather than nothing (`onChange` does not fire
        // for an unchanged value).
        defer { picked = nil }
        let (name, kind) = Self.nameAndKind(of: item)
        reading = name
        defer { reading = nil }
        guard let bytes = try? await item.loadTransferable(type: Data.self) else {
            uploads.setToast(PhoneMediaStrings.cannotReadOnThisPhone(name))
            return
        }
        uploads.uploadPicked(name: name, bytes: bytes, kind: kind)
    }

    /// What to call a picked item, and what it is.
    ///
    /// **Not `item.itemIdentifier`.** That is nil unless the picker was built
    /// with `photoLibrary: .shared()` — which this one is not, deliberately,
    /// because `.shared()` is the variant that needs
    /// `NSPhotoLibraryUsageDescription` and a prompt. So every pick came
    /// through as the literal name `photo`, with no extension: the server
    /// classifies an extensionless file as `other`
    /// (`files.py`'s `_entry_kind`), nothing indexes it as a picture, two
    /// picks in a row post the same name twice, and the file part's MIME type
    /// — which ``PhoneMediaItem/mimeType`` derives from the name — is
    /// `application/octet-stream` for a JPEG.
    ///
    /// The kind cannot be assumed either: the picker matches images **and**
    /// videos, and ``PhoneUploadModel/destination(for:)`` reads the kind to
    /// decide the library, so a picked video sent as `.image` landed in
    /// Pictures.
    private static func nameAndKind(of item: PhotosPickerItem) -> (String, PhoneMediaKind) {
        let type = item.supportedContentTypes.first
        let isVideo = type?.conforms(to: .movie) == true
        let ext = type?.preferredFilenameExtension ?? (isVideo ? "mov" : "jpg")
        // An identifier when there is one — a caller may one day pass a
        // `photoLibrary:` — and otherwise something unique, because two picks
        // in one session must not be one name.
        let stem = item.itemIdentifier?
            .split(separator: "/").first.map(String.init)
            ?? UUID().uuidString.prefix(8).lowercased()
        return ("\(isVideo ? "video" : "photo")-\(stem).\(ext)", isVideo ? .video : .image)
    }

    /// A file from the document picker.
    ///
    /// Read **here**, inside the security scope the importer opened, and not
    /// handed to the uploader as a URL: that scope closes when this closure
    /// returns, and an upload reading the URL afterwards gets a permission
    /// error that looks exactly like a missing file.
    private func send(_ url: URL) {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let name = url.lastPathComponent
        guard let bytes = try? Data(contentsOf: url, options: .mappedIfSafe) else {
            uploads.setToast(PhoneMediaStrings.cannotReadOnThisPhone(name))
            return
        }
        uploads.uploadPicked(
            name: name,
            bytes: bytes,
            kind: PhoneMediaKind(fileName: name, isDir: false)
        )
    }
}
