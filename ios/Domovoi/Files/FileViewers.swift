import DomovoiKit
import SwiftUI
import UIKit

/// Reading one of the household's documents, in the app.
///
/// **It says `read-only` on its face.** Android opens the same file in one of
/// three editors; this app has none, writing a document is the dashboard's
/// job, and the one thing this must not do is look like an editor that
/// silently fails to save ([[no-misleading-controls]]).
struct FileTextSheet: View {

    let entry: FileEntry
    let files: FilesModel
    let model: ShellModel

    @State private var reader: FileTextModel?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Text(FileStrings.readOnly)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    content
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(entry.displayTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(FileStrings.close) { files.doneReading() }
                }
            }
        }
        .task {
            if reader == nil {
                let built = FileTextModel(entry: entry, in: model.container)
                reader = built
                built.read()
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch reader?.load ?? .loading {
        case .loading:
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
        case .ready(let text):
            // Monospaced, selectable, and **not** rendered as Markdown: a
            // `.md` file is being read as the file it is, and interpreting it
            // would hide the characters somebody opened it to look at.
            Text(text)
                .font(.system(.footnote, design: .monospaced))
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        case .notShowable(let reason):
            // The server's answer, not a failure: this file is not text, or
            // it is too long for the route. Both end in the same place — the
            // phone can still be handed the file.
            refusal(FileTextModel.sentence(for: reason), FileStrings.notShowableSub)
        case .failed(let why):
            VStack(alignment: .leading, spacing: 8) {
                Text(FileStrings.unreachable)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(why)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(FileStrings.retry) { reader?.read() }
            }
        }
    }

    private func refusal(_ title: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            Text(detail)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            // The way out this app does have, offered only where it works:
            // `offersSave` is false for a file over the save cap, and a
            // "save it to this device" link that refused would be exactly the
            // control this screen is built not to draw.
            if files.offersSave(entry) {
                HomeLink(FileStrings.verbSave) {
                    files.doneReading()
                    files.saveToDevice(entry)
                }
            }
        }
    }
}

/// Looking at one of the household's pictures.
///
/// Any library, not just Documents: a photo in Music's folder art, a plugin's
/// screenshot, a snap on a stick. The bytes come through `/api/images/raw`,
/// which is the server's inert read — it never decodes an SVG, which is why
/// one of those lands here as ``FileStrings/notAPicture``.
struct FileImageSheet: View {

    let entry: FileEntry
    let files: FilesModel
    let model: ShellModel

    @State private var reader: FileImageModel?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    content
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(entry.displayTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(FileStrings.close) { files.doneViewing() }
                }
            }
        }
        .task {
            if reader == nil, let libraryID = files.selectedID {
                let built = FileImageModel(entry: entry, in: model.container)
                reader = built
                built.read(from: libraryID)
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch reader?.load ?? .loading {
        case .loading:
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
        case .bytes(let data):
            // `UIImage` and not `AsyncImage`: the bytes have already been
            // fetched through the client, with the household token, the
            // identity gate and the redirect delegate that go with it.
            // `AsyncImage` would ask `URLSession` for the URL again, with
            // none of that.
            if let picture = UIImage(data: data) {
                Image(uiImage: picture)
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: .infinity)
                    .accessibilityLabel(entry.displayTitle)
            } else {
                // Bytes that arrived and would not decode. The model's own
                // `failed` covers the fetch; this covers `UIImage` saying no
                // to something the server was happy to send.
                notAPicture
            }
        case .failed(let why):
            VStack(alignment: .leading, spacing: 8) {
                Text(why)
                    .font(.subheadline)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                if let libraryID = files.selectedID {
                    HomeLink(FileStrings.retry) { reader?.read(from: libraryID) }
                }
            }
        }
    }

    private var notAPicture: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(FileStrings.notAPicture)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            Text(FileStrings.notShowableSub)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if files.offersSave(entry) {
                HomeLink(FileStrings.verbSave) {
                    files.doneViewing()
                    files.saveToDevice(entry)
                }
            }
        }
    }
}

// MARK: - handing the file over

/// The system share sheet, over a file this app has just written into its own
/// container.
///
/// This is what "save to device" means on iOS: there is no public Downloads
/// folder to write into, so the phone's own answer is the share sheet — Save
/// to Files, AirDrop, Mail, whichever app the person wants. Dismissing it
/// deletes this app's copy (``FileSaveModel/taken()``), because the person
/// has already been handed the one they asked for.
struct FileShareSheet: UIViewControllerRepresentable {

    let url: URL

    func makeUIViewController(context: Context) -> UIActivityViewController {
        // The file **url**, not its bytes: every activity that can take a
        // file reads it from there, and handing over `Data` would put a
        // second copy in memory and lose the name.
        //
        // No `setValue(_:forKey: "subject")` here, however handy that looks
        // in a snippet: `subject` is not a property of
        // `UIActivityViewController`, and KVC into a class that does not have
        // the key throws `NSUnknownKeyException` — a crash on the one screen
        // whose whole job is handing somebody their file. The share sheet
        // shows the file's own name, which is why this view does not carry
        // one: an unread property is a seam nobody can see going wrong.
        UIActivityViewController(activityItems: [url], applicationActivities: nil)
    }

    /// Nothing to update: the sheet is built for one file and a different
    /// file is a different sheet (``FileSheet/id``).
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
