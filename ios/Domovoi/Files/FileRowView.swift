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

    /// Open this row in one of the document editors instead of doing what
    /// ``FilesModel/tapped(_:)`` would.
    ///
    /// The hook the Documents item adds, and deliberately the **only** one:
    /// ``DocumentsKinds/editor(for:in:)`` is the single question asked, it
    /// answers nil for every library but Documents and for everything the
    /// port leaves out, and where it answers nil this row behaves exactly as
    /// it did before. Nil here — the screen is not Files' own — leaves the
    /// row unchanged too.
    ///
    /// **No second `Edit` in the row's menu.** The tap is already the edit,
    /// and this screen's rule is the one `testWhatEachLibraryOffersOnARow`
    /// pins: a row whose tap is already a save does not offer a second one.
    var onEdit: ((FileEntry) -> Void)? = nil

    /// What this row offers about printing: a `Print` item, the server's
    /// "save it as PDF first" sentence **instead of** a control, or nothing.
    ///
    /// The hook the Printing item adds, and the one question it asks
    /// (``printingOffer(name:isDir:printers:)``). `.none` — this screen has
    /// no printers read, or the house has no printer, or the kind never
    /// prints — leaves the row exactly as it was.
    var printOffer: PrintingOffer = .none

    /// Open the Print sheet for this row.
    var onPrint: (() -> Void)? = nil

    /// The icon column's width, which **grows with the type size**.
    ///
    /// It was a flat 26, and `Image.font(.body)` scales: at AX5 the glyph is
    /// about twice that wide, a `frame(width:)` does not clip, and the folder
    /// was drawn straight across the row's own words. The picture from the
    /// Simulator is the only reason this was found — Apple's audit reported
    /// the row as a Dynamic Type finding and said nothing about an overlap,
    /// and every automated check passed.
    @ScaledMetric(relativeTo: .body) private var glyphWidth: CGFloat = 26

    var body: some View {
        HStack(spacing: 8) {
            if let action = tappable {
                Button {
                    tap()
                } label: {
                    face
                }
                .buttonStyle(.plain)
                .accessibilityLabel(entry.displayTitle)
                .accessibilityValue(files.meta(for: entry))
                // The hint says what the tap does, so it has to follow the
                // tap: `edit` where an editor opens, Android's own answer for
                // the same rows (`primaryVerb`, `FileKinds.kt:122`).
                .accessibilityHint(edits ? DocumentsStrings.verbEdit : (FileKinds.verb(action) ?? ""))
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

    /// Whether this row opens a document editor. One question, asked once —
    /// the tap and the hint must never disagree about it.
    private var edits: Bool {
        onEdit != nil && DocumentsKinds.editor(for: entry, in: files.selectedID) != nil
    }

    private func tap() {
        if edits, let onEdit {
            onEdit(entry)
        } else {
            files.tapped(entry)
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
                .frame(width: glyphWidth, alignment: .center)
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
                // **The server's "save it as PDF first" sentence goes HERE,
                // not in the `Menu`.** A `Text` inside SwiftUI's `Menu` is
                // silently dropped — the trap this screen already learned
                // once over its missing Delete (`FilesView.swift:436-445`) —
                // so a sentence in there would have shipped as a line nobody
                // ever saw. This is the one place on the row that already
                // says "this file can't do the thing" in prose, beside
                // `tooBigToSave`.
                if case .pdfFirst(let said) = printOffer {
                    Text(said)
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
        if files.offersSave(entry) || files.canWrite || canImport || offersPrint {
            Menu {
                // **Absent for a kind that never prints, never greyed.** The
                // route answers 415 for an extension outside the server's own
                // `printable.exts` table (`jobs.py:41-51`), so a `Print` on a
                // `.svg` would be a control that cannot do what it says.
                if offersPrint {
                    Button {
                        onPrint?()
                    } label: {
                        Label(PrintingStrings.printMenuItem, systemImage: "printer")
                    }
                }
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
            .accessibilityLabel(FileStrings.moreFor(entry.displayTitle))
        }
    }

    /// Whether the menu has a `Print`. One question, asked once, so the
    /// menu's presence and its contents cannot disagree — a `•••` that opened
    /// on nothing is a control that does nothing.
    private var offersPrint: Bool {
        guard onPrint != nil else { return false }
        return printOffer == .print
    }

    /// Import is off a drive and into a library, so it is offered on a
    /// removable library's rows and nowhere else.
    private var canImport: Bool {
        files.isRemovable && !files.blocked && !files.importTargets.isEmpty
    }
}
