import DomovoiKit
import SwiftUI

/// Files: every library in the house, a folder at a time.
///
/// `android/.../ui/screens/files/FilesScreen.kt`, with what this app does not
/// build **left out rather than stubbed**.
///
/// ### What is here
///
/// A library picker over the core folders, each enabled plugin's library and
/// any drive plugged into the server; a breadcrumb; a filter over the whole
/// folder; and the folder itself, a page at a time, folders first. Tapping a
/// row does what the row says: a folder opens, a document is read, a picture
/// is looked at, anything else is saved onto the phone. A file can be moved
/// into another folder or library, and a file on a drive can be imported into
/// one. "rescan this library" re-walks the folders past the server's cached
/// listing.
///
/// ### What is not, and why there is no control for it
///
/// **Uploading**, and the phone's own files. Android's Files is one screen
/// over two sources and the phone half needs the photo library, a document
/// picker and persisted folder grants — the phone-media milestone that Videos
/// and Music are also waiting on.
///
/// **Deleting.** `/api/files/delete` needs an admin session and this app has
/// none, so a delete from a phone is a 401 every time. Android draws the
/// control anyway and reports the failure afterwards; this screen says where
/// deleting is done instead (``FileStrings/deleteIsAnAdminJob``), under the
/// owner's standing rule that a control which cannot do what it says is not
/// drawn ([[no-misleading-controls]]).
///
/// **The editors and "+ New" are here now.** This comment used to say
/// writing a document was the dashboard's job; Android has the editors, the
/// save routes are **device** tier and not admin (`files.py:523`), so a phone
/// really can write — the old note was about a batch that had not built them,
/// not about a refusal. Tapping a `.txt`, a `.md`, a `.csv` or an `.xlsx` in
/// Documents opens an editor (``DocumentsEditorCover``), and the header
/// offers `+ New` there. What is still **not** built: the
/// formatted-document/Word editor, which is the dashboard's own page in a
/// WebView, and with it pictures-in-a-document, co-editing and the
/// print/share-as-Word downloads; `+ New → Document`, which would make an
/// `.html` with nowhere to open; and the Documents upload, bulk zip and
/// delete. A `.html` or `.docx` row therefore still opens the **reader**,
/// whose "read-only — editing is on the dashboard" line stays true of exactly
/// the files that reach it. See `overnight/documents/DECISIONS.md`.
///
/// **Printing and scanning are here now.** A printable row offers `Print`
/// (``PrintSheetView``) and Documents' header offers `scan`
/// (``ScanSheetView``) where this phone may write — both device tier, so a
/// phone really can do them, and the row for a Word-like file gets the
/// server's "save it as PDF first" sentence instead of a Print that would
/// answer 415. What is still **not** built: **"Ask in chat"**, which needs
/// Chat to take a file.
///
/// So a row's menu has at most five items rather than Android's nine, and
/// each one is something that happens.
struct FilesView: View {

    let model: ShellModel

    @State private var files: FilesModel?

    /// Which document editor is open over the list, if any. A
    /// `.fullScreenCover` and **not** a sixth ``FileSheet`` case — see
    /// ``DocumentsEditorCover``.
    @State private var editing: DocumentsEditorCover?

    /// `+ New`'s state: the kind being named, the name, and the refusal that
    /// keeps the card open. Built in the same `.task` as ``files``, because
    /// it needs the container too.
    @State private var newDoc: DocumentsNewModel?

    /// What this screen knows about the household's printers: one read, and
    /// two predicates over it (``PrintingFilesOffer``). Built in the same
    /// `.task` as ``files``, because it needs the container too.
    @State private var printing: PrintingFilesOffer?

    /// The file whose Print sheet is open, and whether the Scan sheet is.
    ///
    /// Held here rather than in ``FilesModel``: they are this screen's own
    /// two sheet cases, and ``FilesModel`` is shared with the editors and
    /// knows nothing about printers.
    @State private var printingFile: FileEntry?
    @State private var scanning = false

    /// `onAppear`/`onDisappear`, for ``HomeView``'s reason: this screen is
    /// inside the workspace's `NavigationStack`, so a pushed destination
    /// fires `onDisappear` while this view stays in the hierarchy.
    @State private var visible = false

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                header
                if let files {
                    libraryPicker(files)
                    if files.blocked {
                        blockedCard(files)
                    }
                    if let newDoc {
                        DocumentsNewPrompt(new: newDoc, inSubfolder: !files.path.isEmpty)
                    }
                    trail(files)
                    filterField(files)
                    saving(files)
                    list(files)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task {
            // Built here and not in `init`, for ``HomeView``'s reason: the
            // model needs the container and a `@State` initialiser runs
            // before the view has one.
            if files == nil {
                files = FilesModel(in: model.container, deviceID: model.deviceID)
            }
            if newDoc == nil {
                newDoc = DocumentsNewModel(deviceID: model.deviceID, in: model.container)
            }
            if printing == nil {
                printing = PrintingFilesOffer(in: model.container)
            }
            files?.start()
            printing?.refresh()
        }
        .onAppear { visible = true }
        .onDisappear {
            visible = false
            files?.stop()
            printing?.stop()
        }
        // **The toast is the shell's**, drawn over the whole app, because a
        // toast about a move that failed must outlive a scroll and does
        // outlive this screen.
        .onChange(of: files?.toast) { _, said in
            guard let said else { return }
            model.showToast(said)
            files?.setToast(nil)
        }
        // A save that failed is a toast too, and the state goes back to idle
        // with it: the share sheet is for a file that exists.
        .onChange(of: files?.saves.state) { _, state in
            guard case .failed(let why) = state else { return }
            model.showToast(why)
            files?.saves.taken()
        }
        .onChange(of: model.inputs.serverURL) { _, _ in
            guard visible else { return }
            files?.serverChanged()
        }
        // **One `.sheet`, over five cases**, which is ``VideoFeedSheetView``'s
        // shape and not a tidiness preference: several `.sheet` modifiers on
        // one view do not reliably coexist in SwiftUI — the last one attached
        // wins and the others silently never present. A screen with five
        // sheets would have looked right in review and shipped four dead
        // controls.
        .sheet(item: openSheet) { which in
            if let files {
                FileSheetView(
                    which: which,
                    files: files,
                    model: model,
                    printing: printing,
                    closePrinting: {
                        printingFile = nil
                        scanning = false
                    }
                )
            }
        }
        // A **cover**, not a sixth `.sheet` case: a different modifier, so
        // the five-case sheet above is untouched (two `.sheet`s on one view
        // are the trap this screen already documents), and an editor wants
        // the whole window because the keyboard takes half of it.
        .fullScreenCover(item: $editing) { open in
            if let files {
                DocumentsEditorCoverView(
                    open: open, files: files, model: model, onClose: { editing = nil }
                )
            }
        }
        // A file `+ New` just made opens **exactly as tapping its row
        // would** — Android's `pager.reload(); onEntryPrimary(row.asEntry())`
        // (`FilesScreen.kt:862-867`). One routing table, so a new
        // spreadsheet lands in the grid editor because that is what a
        // `.xlsx` row does, and a `notes.md` typed into Text file lands in
        // the markdown editor.
        .onChange(of: newDoc?.created) { _, made in
            guard let made, let files else { return }
            files.entries.reload(fresh: true)
            // "Exactly as tapping its row would" means the row's own tap when
            // there is no editor for it. `+ New -> Spreadsheet` keeps an
            // extension already in `_SHEET_EXTS` (`documents.py:779`), so a
            // name typed as `Budget.xls` really creates `Budget.xls` — whose
            // category is `download`, so the cover is nil. Setting `editing`
            // to nil there closed the prompt and did nothing at all. The
            // fallback is ``FilesModel/tapped(_:)``, which is what
            // ``FileRowView/tap()`` reaches for the same file: for a `.xls`,
            // the save sheet.
            if let cover = DocumentsEditorCover(for: made, in: files.selectedID) {
                editing = cover
            } else {
                files.tapped(made)
            }
            newDoc?.opened()
        }
    }

    // MARK: - what is open

    /// The one sheet that is up, from the five pieces of model state that can
    /// ask for one.
    ///
    /// The four screen sheets are mutually exclusive by construction — each
    /// is opened from a row and every path closes the one before it — so the
    /// order below only ever settles a race. A finished save comes first so
    /// that a file sitting in the spool cannot be stranded behind a sheet
    /// somebody left open: the copy is deleted when its sheet is dismissed
    /// (``FileSaveModel/taken()``), and a save that is never handed over is a
    /// file left in the container for the next launch to find.
    private var current: FileSheet? {
        guard let files else { return nil }
        if case .ready(let url, let name) = files.saves.state { return .share(url, name) }
        if let entry = files.reading { return .read(entry) }
        if let entry = files.viewing { return .look(entry) }
        if let entry = files.moving { return .move(entry) }
        if let entry = files.importing { return .bring(entry) }
        // **Appended after the existing five**, so nothing that was already
        // on screen is displaced: a save, a read, a look, a move and an
        // import all keep the places they had, and these two can only win a
        // race against each other.
        if let entry = printingFile { return .printFile(entry) }
        if scanning { return .scanIn }
        return nil
    }

    private var openSheet: Binding<FileSheet?> {
        Binding(
            get: { current },
            // Closing is **per case**, read from what is actually open rather
            // than clearing all five: a swipe-down that reset everything
            // would throw away a save in flight behind the sheet.
            set: { next in
                guard next == nil, let open = current else { return }
                close(open)
            }
        )
    }

    private func close(_ which: FileSheet) {
        switch which {
        case .read: files?.doneReading()
        case .look: files?.doneViewing()
        case .move: files?.cancelMove()
        case .bring: files?.cancelImport()
        // Dismissing the share sheet is what deletes the copy, so this is
        // not a tidy-up that could be skipped: it is the only path back to
        // idle, and without it a second save would be ignored for ever (one
        // at a time).
        case .share: files?.saves.taken()
        case .printFile: printingFile = nil
        case .scanIn: scanning = false
        }
    }

    // MARK: - the header

    private var header: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(FileStrings.title)
                    .font(.title2)
                    .accessibilityAddTraits(.isHeader)
                    .fixedSize(horizontal: false, vertical: true)
                Text(FileStrings.subtitle)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            // `+ New`, in Documents only and only where this phone may
            // write. Its own control beside the `•••` rather than an item
            // inside it, as Android has it (`FilesScreen.kt:556`): making a
            // file is not the same kind of thing as re-walking a folder.
            if let files, let newDoc,
               files.selectedID == FileLibrary.documents, files.canWrite {
                DocumentsNewMenu(new: newDoc)
            }
            // `scan`, in Documents only, only where a scanner exists and only
            // where this phone may change files
            // (``printingOffersScan(printers:libraryID:canWrite:)``). The
            // `canWrite` half is not decoration: the scan routes enforce the
            // per-device Documents block twice and answer 403 with the
            // server's own sentence, so a button drawn without it would fail
            // every time from a blocked phone ([[no-misleading-controls]]).
            if let files, let printing,
               printing.offersScan(libraryID: files.selectedID, canWrite: files.canWrite) {
                Button(PrintingStrings.scanHeaderButton) { scanning = true }
                    .buttonStyle(.bordered)
                    .tint(Color.brandInk)
                    .frame(minHeight: 44)
            }
            if let files {
                // A word, not Android's circular-arrow icon: an arrow in a
                // header could mean reload, undo or sync, and this one
                // re-walks a folder on the server.
                Menu {
                    Button(FileStrings.rescan) { files.rescan() }
                } label: {
                    Image(systemName: "ellipsis.circle")
                        .frame(minWidth: 44, minHeight: 44)
                        .contentShape(.rect)
                }
                .accessibilityLabel(FileStrings.title)
            }
        }
    }

    // MARK: - the library

    private func libraryPicker(_ files: FilesModel) -> some View {
        Menu {
            ForEach(files.libraryList) { library in
                Button {
                    files.select(library.id)
                } label: {
                    // Two lines in the menu row, as Android's dropdown has:
                    // the name, and what kind of library it is.
                    VStack(alignment: .leading) {
                        Text(library.label)
                        Text(library.pickerDetail)
                    }
                }
            }
        } label: {
            HStack(spacing: 10) {
                Image(systemName: Self.libraryGlyph(files.currentLibrary?.icon ?? "folder"))
                    .foregroundStyle(Color.brandInk)
                Text(files.currentLibrary?.label ?? FileStrings.selectLibrary)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 4)
                Image(systemName: "chevron.down")
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
            }
            .padding(.horizontal, 12)
            .frame(minHeight: 44)
            .frame(maxWidth: .infinity, alignment: .leading)
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(Color.fgMuted.opacity(0.35), lineWidth: 1)
            )
            .contentShape(.rect)
        }
        .accessibilityLabel(FileStrings.chooseLibrary)
        .accessibilityValue(files.currentLibrary?.label ?? FileStrings.selectLibrary)
    }

    /// Said once, above the list, rather than on every row: this device can
    /// look but not touch, and here is who to ask.
    private func blockedCard(_ files: FilesModel) -> some View {
        Text(FileStrings.blocked(files.blockedReason))
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color.fgMuted.opacity(0.08))
            )
    }

    // MARK: - the trail

    /// The library's root, then a crumb per folder.
    ///
    /// It scrolls sideways, as Android's does: a path four folders deep does
    /// not fit across a phone, and the alternative — eliding the middle — hides
    /// exactly the segment somebody is trying to tap back to.
    private func trail(_ files: FilesModel) -> some View {
        ScrollView(.horizontal) {
            HStack(spacing: 4) {
                Button {
                    files.goToRoot()
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "house.fill").font(.caption)
                        Text(files.currentLibrary?.label ?? FileStrings.libraryRoot)
                            .font(.caption)
                            .lineLimit(1)
                    }
                    .frame(minHeight: 44)
                    .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .foregroundStyle(files.breadcrumb.isEmpty ? Color.primary : Color.brandInk)
                .accessibilityLabel(FileStrings.libraryRoot)

                ForEach(Array(files.breadcrumb.enumerated()), id: \.offset) { index, segment in
                    Text("/")
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                    Button {
                        files.goToCrumb(index)
                    } label: {
                        Text(segment)
                            .font(.caption)
                            .lineLimit(1)
                            .frame(minHeight: 44)
                            .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(
                        index == files.breadcrumb.count - 1 ? Color.primary : Color.brandInk
                    )
                }
            }
        }
        .scrollIndicators(.hidden)
    }

    @ViewBuilder
    private func filterField(_ files: FilesModel) -> some View {
        if files.selectedID != nil {
            TextField(
                FileStrings.filterPlaceholder,
                text: Binding(get: { files.filter }, set: { files.typed($0) })
            )
            .textFieldStyle(.roundedBorder)
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
            .submitLabel(.search)
            .accessibilityLabel(FileStrings.filterPlaceholder)
        }
    }

    /// A save in flight, said on the page rather than in a toast: it is about
    /// to put a sheet up, and a toast that had already faded would leave the
    /// sheet unexplained.
    @ViewBuilder
    private func saving(_ files: FilesModel) -> some View {
        if case .fetching(let name) = files.saves.state {
            HStack(spacing: 8) {
                ProgressView()
                Text(FileStrings.saving(name))
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - the folder

    @ViewBuilder
    private func list(_ files: FilesModel) -> some View {
        if let count = files.countText {
            Text(count)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        // Three states, not two: a list that has not answered is not an empty
        // list (``PagedState/loaded``), and a library that has not been picked
        // has not got a list at all.
        if files.selectedID == nil || !files.entries.state.loaded {
            if files.hasNoLibraries {
                note(FileStrings.noLibraries, FileStrings.noLibrariesSub)
            } else {
                ProgressView()
                    .frame(maxWidth: .infinity, alignment: .center)
                    .accessibilityLabel(FileStrings.title)
            }
        }
        ForEach(Array(files.entries.state.items.enumerated()), id: \.element.rel) { index, entry in
            FileRowView(
                entry: entry,
                files: files,
                onEdit: { opened in
                    editing = DocumentsEditorCover(for: opened, in: files.selectedID)
                },
                // One question per row, asked of the one read this screen
                // holds: ``PrintingOffer/print``, the server's "save it as
                // PDF first" sentence, or nothing at all.
                printOffer: printing?.offer(for: entry) ?? .none,
                onPrint: { printingFile = entry }
            )
                .onAppear {
                    // The guarded question, never a bare "the last row
                    // appeared" (``Paging/nearEnd(totalItems:lastVisible:threshold:)``).
                    if files.shouldLoadMore(lastVisible: index) {
                        files.entries.loadMore()
                    }
                }
            Divider()
        }
        if let empty = files.emptyNote {
            note(empty.title, empty.detail)
        }
        footer(files)
        whereDeletingLives(files)
    }

    /// Where deleting is done, said once at the foot of the list.
    ///
    /// **Not in each row's menu**, which is where the string's own note first
    /// put it: a `Text` is not a menu element — SwiftUI's `Menu` takes
    /// buttons, links and sections, and anything else is silently dropped —
    /// so a sentence in there would have been written, reviewed and shipped
    /// as a line nobody ever saw. The alternative, a disabled `Button`, is a
    /// greyed-out Delete: the exact control this screen is built not to draw.
    ///
    /// It is drawn only where somebody would go looking for a Delete — a
    /// library that takes changes at all — and not under a read-only one.
    @ViewBuilder
    private func whereDeletingLives(_ files: FilesModel) -> some View {
        if files.currentLibrary?.editable == true, !files.entries.state.items.isEmpty {
            Text(FileStrings.deleteIsAnAdminJob)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 8)
        }
    }

    private func note(_ title: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            Text(detail)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 8)
    }

    @ViewBuilder
    private func footer(_ files: FilesModel) -> some View {
        let list = files.entries.state
        if list.loading, !list.items.isEmpty {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
        } else if let trouble = list.error {
            VStack(alignment: .leading, spacing: 8) {
                Text(FileStrings.unreachable)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(trouble)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(FileStrings.retry) { files.entries.retry() }
            }
        } else if list.canLoadMore {
            // Drawn as well as the scroll trigger, not instead of it: the
            // trigger cannot fire for a list the person has stopped
            // scrolling, and VoiceOver's reader does not scroll at all.
            HomeLink(FileStrings.loadMore) { files.entries.loadMore() }
        }
    }

    // MARK: - the glyphs

    /// The server's **lucide** icon name → an SF Symbol.
    ///
    /// Translated here, in the view, rather than in the model: the server's
    /// vocabulary is the dashboard's icon set, and a model that stored SF
    /// Symbol names would hide which end chose them.
    static func libraryGlyph(_ lucide: String) -> String {
        switch lucide {
        case "music": return "music.note"
        case "book-open": return "book"
        case "podcast": return "antenna.radiowaves.left.and.right"
        case "file-text": return "doc.text"
        case "hard-drive": return "externaldrive"
        case "disc": return "opticaldisc"
        case "clapperboard": return "film"
        case "puzzle": return "puzzlepiece"
        case "image": return "photo"
        default: return "folder"
        }
    }

    /// An entry's `kind` → its row glyph. A formatted document takes the
    /// document glyph rather than the text one, as its name takes the word
    /// `Document`.
    static func entryGlyph(_ entry: FileEntry) -> String {
        if FileKinds.isFormattedDocument(entry.name, isDir: entry.isDir) { return "doc.text" }
        switch entry.kind {
        case "folder": return "folder.fill"
        case "audio": return "music.note"
        case "video": return "film"
        case "image": return "photo"
        case "pdf": return "doc.richtext"
        case "doc-office": return "tablecells"
        case "doc-text": return "doc.plaintext"
        default: return "doc"
        }
    }
}
