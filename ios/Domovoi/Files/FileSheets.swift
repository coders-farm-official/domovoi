import DomovoiKit
import SwiftUI

/// The one sheet the Files screen puts up, over the five things that can ask
/// for one.
///
/// One enum rather than five `.sheet` modifiers, which ``FilesView`` says
/// why: several sheets attached to one view do not reliably coexist.
enum FileSheet: Identifiable {
    case read(FileEntry)
    case look(FileEntry)
    case move(FileEntry)
    /// Importing off a drive. `bring`, because `import` is a keyword.
    case bring(FileEntry)
    case share(URL, String)
    /// Printing a server file on one of the household's printers. Appended
    /// **after** the five that were here, so nothing already on screen is
    /// displaced by the new cases' place in ``FilesView``'s `current` chain.
    case printFile(FileEntry)
    /// Scanning into Documents. No file of its own — it makes one.
    case scanIn

    /// The case **and** the file, so that opening a second file of the same
    /// kind replaces the sheet's content rather than keeping the first one's:
    /// `.sheet(item:)` rebuilds on a changed id and not otherwise.
    var id: String {
        switch self {
        case .read(let entry): return "read:\(entry.rel)"
        case .look(let entry): return "look:\(entry.rel)"
        case .move(let entry): return "move:\(entry.rel)"
        case .bring(let entry): return "bring:\(entry.rel)"
        case .share(_, let name): return "share:\(name)"
        case .printFile(let entry): return "print:\(entry.rel)"
        case .scanIn: return "scan"
        }
    }
}

struct FileSheetView: View {

    let which: FileSheet
    let files: FilesModel
    let model: ShellModel

    /// The printers read, when the screen has one. The hook the Printing item
    /// adds: nil here leaves every other case exactly as it was, and the two
    /// printing cases cannot be reached without it because the controls that
    /// open them are only drawn from the same read.
    var printing: PrintingFilesOffer? = nil

    /// Close whichever printing sheet is up. The other five close through
    /// ``FilesModel``, which owns their state; these two are the screen's.
    var closePrinting: () -> Void = {}

    var body: some View {
        switch which {
        case .read(let entry):
            FileTextSheet(entry: entry, files: files, model: model)
        case .look(let entry):
            FileImageSheet(entry: entry, files: files, model: model)
        case .move(let entry):
            FileMoveSheet(entry: entry, files: files, model: model)
        case .bring(let entry):
            FileImportSheet(entry: entry, files: files)
        case .share(let url, _):
            FileShareSheet(url: url)
        case .printFile(let entry):
            // Both are nil-guarded rather than force-unwrapped: a sheet case
            // that could be reached with no read behind it would crash, and
            // this screen's rule is that a control nothing can answer is not
            // drawn in the first place.
            if let printers = printing?.data, let library = files.selectedID {
                PrintSheetView(
                    entry: entry,
                    libraryID: library,
                    printers: printers,
                    model: model,
                    onClose: closePrinting
                )
            }
        case .scanIn:
            if let printers = printing?.data {
                ScanSheetView(
                    printers: printers,
                    model: model,
                    // The saved scan's row is in the folder because the
                    // folder is re-read, not because the sheet guessed what
                    // the server wrote.
                    onSaved: { files.entries.reload(fresh: true) },
                    onClose: closePrinting
                )
            }
        }
    }
}

// MARK: - where a file should go

/// The destination picker: a library, then a folder inside it.
///
/// `FilesMoveSheet.kt`. It walks folders with the same browse the screen
/// uses, asked for folders only, and it reads **one page** per level — the
/// sheet says when a folder has more than it is showing
/// (``FileStrings/showingFirstFolders(_:of:)``) rather than paging inside a
/// sheet somebody is only passing through.
struct FileMoveSheet: View {

    let entry: FileEntry
    let files: FilesModel
    let model: ShellModel

    @State private var picker: FileMoveModel?

    var body: some View {
        NavigationStack {
            Group {
                if let picker {
                    if picker.hasNoTargets {
                        emptyState(FileStrings.noMoveTargets)
                    } else {
                        picking(picker)
                    }
                } else {
                    ProgressView()
                }
            }
            .navigationTitle(FileStrings.moveTitle(entry.displayTitle))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(FileStrings.close) { files.cancelMove() }
                }
            }
        }
        .presentationDetents([.large])
        .task {
            if picker == nil {
                let built = FileMoveModel(
                    entry: entry,
                    sourceLibraryID: files.selectedID ?? "",
                    sourcePath: files.path,
                    targets: files.moveTargets,
                    deviceID: model.deviceID,
                    in: model.container
                )
                picker = built
                built.list()
            }
        }
    }

    /// Named `picking` and not `body(_:)`: a method of that name beside
    /// `View`'s own `body` compiles and is a trap for the next reader.
    private func picking(_ picker: FileMoveModel) -> some View {
        VStack(spacing: 0) {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    libraryPicker(picker)
                    hereLine(picker)
                    if !picker.trail.isEmpty {
                        HomeLink(FileStrings.upOneLevel) { picker.up() }
                    }
                    folders(picker)
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            Divider()
            // The refusal is **in the button's own label** when there is one
            // (``FileMoveModel/actionLabel``), so a button that cannot be
            // pressed is never unexplained.
            Button {
                if let target = picker.targetLibrary {
                    files.move(entry, into: target, at: picker.targetPath)
                }
            } label: {
                Text(picker.actionLabel)
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .frame(minHeight: 44)
                    .fixedSize(horizontal: false, vertical: true)
            }
            // Android's filled `Button` (its theme's `primary`/`onPrimary`, `brand` and
            // `brandFg`). Until unit ix-fill this was a `.borderedProminent` with no
            // tint, so it filled with the app's ``Color/brandInk`` and drew a white
            // label: 5.65:1 light and **1.91:1** dark, and grey on grey disabled.
            // The disabled look matters most here: the refusal is this
            // button's label, and ``BrandFillButtonStyle`` draws it in
            // ``Color/fgMuted`` on the sheet (7.43:1) rather than dimmed on a
            // grey fill.
            .buttonStyle(BrandFillButtonStyle())
            .disabled(!picker.canMoveHere)
            .padding(16)
        }
    }

    private func libraryPicker(_ picker: FileMoveModel) -> some View {
        Menu {
            ForEach(picker.targets) { target in
                Button(target.label) { picker.pick(target.id) }
            }
        } label: {
            HStack(spacing: 8) {
                Text(FileStrings.library)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                Text(picker.targetLibrary?.label ?? FileStrings.selectLibrary)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Image(systemName: "chevron.down")
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                Spacer(minLength: 0)
            }
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        .accessibilityLabel(FileStrings.chooseLibrary)
        .accessibilityValue(picker.targetLibrary?.label ?? FileStrings.selectLibrary)
    }

    private func hereLine(_ picker: FileMoveModel) -> some View {
        Text(picker.here)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }

    @ViewBuilder
    private func folders(_ picker: FileMoveModel) -> some View {
        if picker.loading {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
        } else if let trouble = picker.error {
            VStack(alignment: .leading, spacing: 8) {
                Text(FileStrings.couldNotOpenFolder)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(trouble)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(FileStrings.retry) { picker.list() }
            }
        } else if picker.isEmptyHere {
            emptyState(FileStrings.noSubfolders, FileStrings.noSubfoldersSub)
        } else {
            ForEach(picker.folders) { folder in
                folderRow(folder, picker)
                Divider()
            }
            if let total = picker.moreFolders {
                Text(FileStrings.showingFirstFolders(picker.folders.count, of: total))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder
    private func folderRow(_ folder: FileEntry, _ picker: FileMoveModel) -> some View {
        // The folder being moved is shown among its own siblings, greyed,
        // with `(this one)` after it: a folder that vanished from its own
        // parent's listing reads as a bug.
        if picker.isSelf(folder) {
            HStack(spacing: 12) {
                Image(systemName: "folder")
                    .foregroundStyle(Color.fgMuted)
                Text(folder.displayTitle)
                    .font(.subheadline)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                Text(FileStrings.thisOne)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
            }
            .frame(minHeight: 44)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityElement(children: .combine)
        } else {
            Button {
                picker.into(folder)
            } label: {
                HStack(spacing: 12) {
                    Image(systemName: "folder.fill")
                        .foregroundStyle(Color.brandInk)
                    Text(folder.displayTitle)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right")
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                }
                .frame(minHeight: 44)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
        }
    }

    private func emptyState(_ title: String, _ detail: String? = nil) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            if let detail {
                Text(detail)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - copying off a drive

/// Which library an imported file should land in.
///
/// A list of libraries and nothing else — there is no folder to pick, because
/// `/api/files/import` decides where inside the library a file belongs from
/// what kind of file it is (`FilesScreen.kt:1162`). The copy happens on the
/// server: the phone never carries the bytes, which is why importing a film
/// off a stick is something a phone can ask for at all.
struct FileImportSheet: View {

    let entry: FileEntry
    let files: FilesModel

    /// The icon column, for the reason in ``FileRowView``: a `frame(width:)`
    /// does not clip, and these glyphs scale with the type size.
    @ScaledMetric(relativeTo: .body) private var glyphWidth: CGFloat = 26

    var body: some View {
        NavigationStack {
            Group {
                if files.importTargets.isEmpty {
                    Text(FileStrings.noImportTargets)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(16)
                        .frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 0) {
                            Text(entry.displayTitle)
                                .font(.footnote)
                                .foregroundStyle(Color.fgMuted)
                                .fixedSize(horizontal: false, vertical: true)
                                .padding(.bottom, 12)
                            ForEach(files.importTargets) { target in
                                Button {
                                    files.importInto(target, entry)
                                } label: {
                                    HStack(spacing: 12) {
                                        Image(systemName: FilesView.libraryGlyph(target.icon))
                                            .foregroundStyle(Color.brandInk)
                                            .frame(width: glyphWidth)
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(target.label)
                                                .font(.subheadline)
                                                .fixedSize(horizontal: false, vertical: true)
                                                .multilineTextAlignment(.leading)
                                            Text(target.pickerDetail)
                                                .font(.caption)
                                                .foregroundStyle(Color.fgMuted)
                                                .fixedSize(horizontal: false, vertical: true)
                                                .multilineTextAlignment(.leading)
                                        }
                                        Spacer(minLength: 0)
                                    }
                                    .frame(minHeight: 56)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .contentShape(.rect)
                                }
                                .buttonStyle(.plain)
                                Divider()
                            }
                        }
                        .padding(16)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            .navigationTitle(FileStrings.importTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(FileStrings.close) { files.cancelImport() }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }
}
