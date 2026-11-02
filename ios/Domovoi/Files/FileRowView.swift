import DomovoiKit
import SwiftUI

/// One entry in a folder: what it is, what tapping it does, and the one or
/// two other things this app can do with it.
///
/// `FilesScreen.kt:1016`'s `FileRow`, whose menu has nine items. This one has
/// at most three, and the difference is not restraint for its own sake: four
/// of Android's nine are the editors, the upload and the print this app has
/// not built, one is a delete that fails every time from a phone, and one
/// asks Chat. What is left is what happens.
struct FileRowView: View {

    let entry: FileEntry
    let files: FilesModel

    var body: some View {
        HStack(spacing: 8) {
            if let action = tappable {
                Button {
                    files.tapped(entry)
                } label: {
                    face
                }
                .buttonStyle(.plain)
                .accessibilityLabel(entry.displayTitle)
                .accessibilityValue(files.meta(for: entry))
                .accessibilityHint(FileKinds.verb(action) ?? "")
            } else {
                // **Not a disabled button.** A file too big to pull into
                // memory has nothing to tap, and a greyed-out row would look
                // like a control that has stopped working; the row says what
                // it is and where to go instead ([[no-misleading-controls]]).
                face
                    .accessibilityElement(children: .combine)
            }
            menu
        }
    }

    /// Nil when tapping the row would do nothing — the one case
    /// (``FileKinds/FileAction/tooBig(_:)``) where ``FilesModel/tapped(_:)``
    /// deliberately has no answer.
    private var tappable: FileKinds.FileAction? {
        let action = files.action(for: entry)
        if case .tooBig = action { return nil }
        return action
    }

    private var face: some View {
        HStack(spacing: 12) {
            Image(systemName: FilesView.entryGlyph(entry))
                .font(.body)
                .foregroundStyle(entry.isDir ? Color.brandInk : Color.fgMuted)
                .frame(width: 26, alignment: .center)
            VStack(alignment: .leading, spacing: 2) {
                Text(entry.displayTitle)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Text(files.meta(for: entry))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                if case .tooBig(let size) = files.action(for: entry) {
                    Text(FileStrings.tooBigToSave(size))
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
            }
            Spacer(minLength: 0)
            if entry.isDir {
                Image(systemName: "chevron.right")
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
            }
        }
        .frame(minHeight: 56)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(.rect)
    }

    /// The extras, when there are any. The menu button is **not drawn at
    /// all** for a row with nothing in it — an empty menu is a control that
    /// does nothing.
    @ViewBuilder
    private var menu: some View {
        if files.offersSave(entry) || files.canWrite || canImport {
            Menu {
                if files.offersSave(entry) {
                    Button {
                        files.saveToDevice(entry)
                    } label: {
                        Label(FileStrings.verbSave, systemImage: "square.and.arrow.down")
                    }
                }
                if files.canWrite {
                    Button {
                        files.moveRequested(entry)
                    } label: {
                        Label(FileStrings.move, systemImage: "folder")
                    }
                }
                if canImport {
                    Button {
                        files.importRequested(entry)
                    } label: {
                        Label(FileStrings.importInto, systemImage: "tray.and.arrow.down")
                    }
                }
            } label: {
                Image(systemName: "ellipsis.circle")
                    .foregroundStyle(Color.fgMuted)
                    .frame(width: 44, height: 44)
                    .contentShape(.rect)
            }
            .accessibilityLabel(entry.displayTitle)
        }
    }

    /// Import is off a drive and into a library, so it is offered on a
    /// removable library's rows and nowhere else.
    private var canImport: Bool {
        files.isRemovable && !files.blocked && !files.importTargets.isEmpty
    }
}
