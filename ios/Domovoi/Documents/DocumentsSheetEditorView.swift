import DomovoiKit
import SwiftUI

/// The household's spreadsheets, as a grid.
///
/// `SheetEditor.kt:61`'s `SheetEditorOverlay`. Everything about the data —
/// the decode, the padding, which cell shows its formula, the whole-grid save
/// and the three refusals — is ``DocumentsSheetModel``'s; this file is the
/// chrome and the cells.
///
/// ### Nothing is parsed on this phone
///
/// Android carries 1,220 lines of spreadsheet code (`XlsxSheet.kt`,
/// `MiniXml.kt`, `CsvSheet.kt`) and **none of it is ported**: its only
/// Files-side caller there is `PhoneFileSource.kt`, and this app has no
/// phone-folder file source. The server sends the grid and takes the grid,
/// patching the workbook cell by changed cell so formatting, other sheets and
/// charts survive — `/api/documents/sheet` is the route it keeps "for the
/// phone app's own grid editor" (`documents.py:1314`). See
/// `overnight/documents/DECISIONS.md`.
///
/// ### What is not, and why there is no control for it
///
/// * **No formula evaluation.** A typed `=SUM(A1:A2)` is stored as a formula
///   and the workbook on the server evaluates it; a number this app computed
///   itself could disagree with the file.
/// * **No row or column insertion, no formatting, no second worksheet.** The
///   route is a window onto the first sheet, and a control for anything
///   outside that window is one the save would drop.
/// * **No co-editing notice.** `/api/documents/presence` exists and is not
///   called: a "somebody else has this open" line this app cannot act on is
///   noise, and the heartbeat behind it would have a phone writing to the
///   house every fifteen seconds.
/// * **No Save** in a read-only library or on a blocked device —
///   ``DocumentsNoSaveNote`` in its place.
struct DocumentsSheetEditorView: View {

    let entry: FileEntry
    let files: FilesModel
    let model: ShellModel
    let onClose: () -> Void

    @State private var sheet: DocumentsSheetModel?
    @State private var confirmingDiscard = false

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 0) {
                DocumentsEditorPill(
                    pill: DocumentsStrings.pillSheet,
                    dirty: sheet?.dirty ?? false,
                    saving: sheet?.saving ?? false
                )
                .padding(.horizontal, 16)
                .padding(.top, 8)

                if let said = sheet?.saveFailure {
                    // 413 (a row past the column cap), 409 (an `.xlsx` with
                    // charts or pivots) and 415 all arrive with the server's
                    // own sentence, and each one is something a person can
                    // act on — so it stays on screen, not only in a toast.
                    Text(said)
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 16)
                        .padding(.top, 6)
                }

                content
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .navigationTitle(entry.displayTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                // The text editor's bar, for the text editor's reason:
                // ``DocumentsEditorBarLabel``. The audit never reached this
                // screen in train S008 — the text editor's contrast finding
                // ended the case first — and the two bars were identical, so
                // this one carried the same defect unreported.
                ToolbarItem(placement: .cancellationAction) {
                    Button { closeRequested() } label: {
                        DocumentsEditorBarLabel(word: DocumentsStrings.close, live: true)
                    }
                    .buttonStyle(.plain)
                }
                ToolbarItem(placement: .confirmationAction) {
                    if sheet?.offersSave == true {
                        let live = sheet?.canSave ?? false
                        Button { sheet?.save() } label: {
                            DocumentsEditorBarLabel(word: DocumentsStrings.save, live: live)
                        }
                        .buttonStyle(.plain)
                        .disabled(!live)
                    }
                }
            }
        }
        .task {
            if sheet == nil {
                let built = DocumentsSheetModel(
                    entry: entry,
                    canWrite: files.canWrite,
                    deviceID: model.deviceID,
                    in: model.container
                )
                built.onSaved = { files.entries.reload(fresh: true) }
                sheet = built
                built.read()
            }
        }
        .onChange(of: sheet?.toast) { _, said in
            guard let said else { return }
            model.showToast(said)
            sheet?.clearToast()
        }
        .alert(DocumentsStrings.unsavedChangesTitle, isPresented: $confirmingDiscard) {
            Button(DocumentsStrings.discard, role: .destructive) { onClose() }
            Button(DocumentsStrings.keepEditing, role: .cancel) {}
        } message: {
            Text(DocumentsStrings.unsavedChangesBody)
        }
    }

    private func closeRequested() {
        if sheet?.dirty == true {
            confirmingDiscard = true
        } else {
            onClose()
        }
    }

    // MARK: - the four states

    @ViewBuilder
    private var content: some View {
        switch sheet?.load ?? .loading {
        case .loading:
            ProgressView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .accessibilityLabel(entry.displayTitle)
        case .ready:
            if let sheet {
                grid(sheet)
            }
        case .unsupported:
            // `SheetEditor.kt:147`: a sheet type the server will not
            // round-trip. The only control under it is the one in the bar.
            note(DocumentsStrings.sheetUnsupported, retry: false)
        case .failed(let why):
            note(why, retry: true)
        }
    }

    private func note(_ said: String, retry: Bool) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(said)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            if retry {
                HomeLink(FileStrings.retry) { sheet?.read() }
            } else if files.offersSave(entry) {
                HomeLink(FileStrings.verbSave) {
                    onClose()
                    files.saveToDevice(entry)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - the grid

    /// One scroll view over both axes, with a `LazyVStack` of rows inside.
    ///
    /// **Not a horizontal scroller per row**, which is the obvious shape and
    /// the wrong one: each row would then scroll on its own and column `C`
    /// would sit under column `A` one row down. Android gets the same effect
    /// from a `LazyColumn` whose rows share one `horizontalScroll` state
    /// (`SheetEditor.kt:173`).
    ///
    /// Lazy vertically because the server's window is a thousand rows
    /// (`max_rows`); a column is capped at sixty, so a row is built whole.
    private func grid(_ sheet: DocumentsSheetModel) -> some View {
        ScrollView([.horizontal, .vertical]) {
            LazyVStack(alignment: .leading, spacing: 0) {
                header(sheet)
                ForEach(0..<sheet.grid.count, id: \.self) { row in
                    DocumentsSheetRow(sheet: sheet, row: row)
                }
                if !sheet.offersSave {
                    DocumentsNoSaveNote()
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                }
            }
            // Android measures the same gap at the foot of its viewport
            // (`SheetEditor.kt:162-172`): without it the last row sits under
            // the keyboard's accessory and cannot be tapped into.
            .padding(.bottom, 32)
        }
    }

    private func header(_ sheet: DocumentsSheetModel) -> some View {
        HStack(spacing: 0) {
            DocumentsSheetGutterCell(label: "")
            ForEach(0..<sheet.columns, id: \.self) { column in
                Text(DocumentsSheetModel.columnName(column))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .frame(minHeight: 28)
                    .frame(width: cellWidth)
            }
        }
    }

    /// The same metric the cells use, and it has to be the same: column
    /// letters that scaled differently from the columns would stop sitting
    /// over them. See ``DocumentsSheetCellField/cellWidth``.
    @ScaledMetric(relativeTo: .footnote) private var cellWidth: CGFloat = 110
}

/// The row-number column on the left.
struct DocumentsSheetGutterCell: View {

    let label: String

    /// `34.dp` (`SheetEditor.kt:190`), scaled. See
    /// ``DocumentsSheetCellField/cellWidth`` for why this is a
    /// `@ScaledMetric` and not a jump at the accessibility sizes.
    @ScaledMetric(relativeTo: .caption) private var gutterWidth: CGFloat = 34

    var body: some View {
        Text(label)
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .frame(width: gutterWidth)
            .frame(minHeight: 28)
            // The number is already announced as part of each cell's address
            // (`B12`), so a reader that stopped on every gutter cell would
            // say every row's number twice.
            .accessibilityHidden(true)
    }
}

/// One row of cells.
///
/// A view of its own so the `LazyVStack` can build and drop it, and **it
/// holds no `Task`**: every keystroke goes straight into
/// ``DocumentsSheetModel/setCell(row:column:to:)``, and the save belongs to
/// the model, which outlives the row (NIGHT-RULES §3).
struct DocumentsSheetRow: View {

    let sheet: DocumentsSheetModel
    let row: Int

    var body: some View {
        HStack(spacing: 0) {
            DocumentsSheetGutterCell(label: "\(row + 1)")
            ForEach(0..<sheet.columns, id: \.self) { column in
                DocumentsSheetCellField(sheet: sheet, row: row, column: column)
            }
        }
    }
}

struct DocumentsSheetCellField: View {

    let sheet: DocumentsSheetModel
    let row: Int
    let column: Int

    /// `110.dp` (`SheetEditor.kt:179`), **scaled continuously**.
    ///
    /// A column has to have some width — a spreadsheet without aligned
    /// columns is not one — so this is the one figure in the item, and it is
    /// the one place a `frame(width:)` is set. It was a two-step jump on
    /// `isAccessibilitySize`, which meant that from `.large` to `.xxxLarge` —
    /// ordinary sizes, none of them accessibility sizes — the `.footnote`
    /// text inside grew by about a third while the cell stayed 110pt, so
    /// contents truncated and four-digit row numbers clipped in the gutter
    /// (NIGHT-RULES §3). The header's own cells and
    /// ``DocumentsSheetGutterCell`` carry the same two metrics, and the
    /// header must: column letters that stopped scaling with the columns
    /// would stop sitting over them.
    @ScaledMetric(relativeTo: .footnote) private var cellWidth: CGFloat = 110

    var body: some View {
        TextField(
            "",
            text: Binding(
                get: { sheet.cell(row: row, column: column) },
                set: { sheet.setCell(row: row, column: column, to: $0) }
            )
        )
        .font(.footnote)
        .autocorrectionDisabled()
        .textInputAutocapitalization(.never)
        .disabled(!sheet.canWrite)
        .padding(.horizontal, 6)
        // Width is set, height is not: `minHeight` is the floor and the
        // text decides the rest, so a cell grows with the type size.
        .frame(width: cellWidth)
        .frame(minHeight: 44)
        .overlay(
            Rectangle()
                .stroke(Color.fgMuted.opacity(0.25), lineWidth: 0.5)
        )
        // `B12`. Android's grid labels its cells with nothing at all, so
        // VoiceOver reads a wall of unnamed text fields — a gap on that side.
        .accessibilityLabel(DocumentsSheetModel.address(row: row, column: column))
    }
}
