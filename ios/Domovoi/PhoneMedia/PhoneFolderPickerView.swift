import DomovoiKit
import SwiftUI
import UniformTypeIdentifiers

/// Add a folder on this phone, list the ones added, and take one off again.
///
/// `ui/screens/files/PhoneFiles.kt:84`'s `addPhoneFolder` and `:103`'s
/// `removePhoneFolder`. Android's Storage Access Framework tree grant and
/// iOS's security-scoped bookmark are the same idea in the same words — a
/// token that outlives the pick — and `.fileImporter` in folder mode is the
/// exact analogue of the SAF folder picker. **Neither needs a permission
/// string**, which is why this item adds no `Info.plist` key for it.
///
/// ### What is not, and why there is no control for it
///
/// * **Nothing is written into a picked folder.** No new folder, no rename,
///   no delete, no save-into-the-phone. The grant asked for covers reading,
///   which is all any screen over it uses (DECISIONS, "Needs a shared
///   change" 3).
/// * **"remove" is not a delete**, and
///   ``PhoneMediaStrings/removingKeepsTheFiles`` says so out loud rather than
///   in a comment, because next to a Files-style browser the word reads as
///   one.
struct PhoneFolderPickerView: View {

    let folders: PhoneFolderBookmarks

    /// Run after the list changed, so the page's source — built from the
    /// folders as they were — is rebuilt.
    let onChanged: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var picking = false

    /// Said when iOS would not keep the grant, which is Android's
    /// "null when the platform will not persist it".
    @State private var trouble: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Text(PhoneMediaStrings.phoneFilesSubtitle)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    HomeLink(PhoneMediaStrings.addAFolder) { picking = true }
                    if let trouble {
                        Text(trouble)
                            .font(.footnote)
                            .foregroundStyle(Color.errInk)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    ForEach(folders.folders) { folder in
                        row(folder)
                        Divider()
                    }
                    if folders.folders.isEmpty {
                        PhoneDeviceNote(
                            title: PhoneMediaStrings.addAFolder,
                            detail: PhoneMediaStrings.downloadsLandHere
                        )
                    } else {
                        Text(PhoneMediaStrings.removingKeepsTheFiles)
                            .font(.caption)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            // No `navigationTitle`. The first line of the sheet already says
            // what it is, in the household's own words
            // (``PhoneMediaStrings/phoneFilesSubtitle``), and a second title
            // over it would say it twice at every type size.
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button(FileStrings.close) { dismiss() }
                }
            }
        }
        // **`.folder`, and `.fileImporter`, not a `UIDocumentPickerViewController`
        // wrapper.** The SwiftUI importer returns a security-scoped URL and
        // nothing else is needed: `PhoneFolderBookmarks.add(_:)` opens the
        // scope, records the bookmark and closes it again.
        .fileImporter(
            isPresented: $picking,
            allowedContentTypes: [.folder],
            allowsMultipleSelection: false
        ) { picked in
            switch picked {
            case .success(let urls):
                guard let url = urls.first else { return }
                if folders.add(url) {
                    trouble = nil
                    onChanged()
                } else {
                    trouble = PhoneMediaStrings.folderCouldNotBeKept
                }
            case .failure:
                // The importer's own failure, which is not the same as a
                // bookmark that could not be made: the person may simply have
                // cancelled, and a cancel is not an error to report.
                trouble = nil
            }
        }
    }

    private func row(_ folder: PhoneFolder) -> some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(folder.name)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                if folders.unresolvable.contains(folder.path) {
                    // The row stays and says why, where Android drops it
                    // silently (`PhoneFolders.kt:46`): a folder that vanished
                    // leaves nobody able to ask what happened.
                    Text(PhoneMediaStrings.folderNoLongerReadable)
                        .font(.caption)
                        .foregroundStyle(Color.warnInk)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text(folder.path)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .lineLimit(2)
                        .truncationMode(.head)
                }
            }
            Spacer(minLength: 8)
            Button {
                folders.remove(path: folder.path)
                onChanged()
            } label: {
                Text(PhoneMediaStrings.removeFolder)
                    .font(.subheadline)
                    .frame(minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.brandInk)
            // Named with the folder, because a list of them all labelled
            // `remove` names nothing — the mistake a grid of forty tiles
            // saying `play` already made on this track.
            .accessibilityLabel("\(PhoneMediaStrings.removeFolder) \(folder.name)")
        }
        .padding(.vertical, 4)
    }
}

/// The link onto the picker, drawn on every device page.
///
/// On the device side of Music and Files it is how the folders get there at
/// all; on Videos it is how a folder of clips gets added beside the photo
/// library. One sheet, held here, so the page underneath keeps its scroll.
struct PhoneFolderPickerLink: View {

    let device: PhoneDevicePage
    let model: ShellModel

    @State private var open = false

    var body: some View {
        HomeLink(PhoneMediaStrings.addAFolder) { open = true }
            .sheet(isPresented: $open) {
                PhoneFolderPickerView(folders: device.folders) {
                    // The folder half of the source is replaced FIRST and the
                    // libraries re-read after — the order matters, and
                    // ``PhoneSwappableSource`` says why.
                    device.foldersChanged()
                }
            }
    }
}
