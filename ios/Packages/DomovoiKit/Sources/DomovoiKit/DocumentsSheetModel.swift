import Foundation
import Observation

/// One cell as the server's grid model has it: `{v, f}` — the stored value
/// as text, and the formula string with its leading `=` when there is one
/// (`documents.py`'s `_cell_model`, `:938`).
///
/// `SheetModel.kt:12`'s `SheetCell`. Both fields are optional and both can be
/// null at once, which is a blank cell.
public struct DocumentsSheetCell: Equatable, Sendable, Decodable {

    public let v: String?
    public let f: String?

    public init(v: String? = nil, f: String? = nil) {
        self.v = v
        self.f = f
    }

    /// What the editor shows: **the formula when there is one**, else the
    /// value — `SheetModel.kt:35`'s `display()`.
    ///
    /// This way round, and it matters: a cell holding `=SUM(B1:B2)` with a
    /// cached `42` shows the formula, because that is what editing it has to
    /// change. Ported backwards, every formula in a household's sheet would
    /// silently become its own last value the next time anybody saved.
    public var display: String { f ?? v ?? "" }
}

/// `GET /api/documents/sheet/{rel}`: the grid and the window it was read in
/// (`documents.py:1260`).
///
/// Rows are **ragged** — trailing empty cells are not sent — which is the
/// whole reason the editor pads (`_read_sheet_grid`'s docstring says an
/// earlier version padded every row to the column cap and showed every
/// `.xlsx` sixty columns wide).
public struct DocumentsSheetGrid: Equatable, Sendable, Decodable {

    public let relPath: String
    public let rows: [[DocumentsSheetCell?]]
    /// `_SHEET_MAX_ROWS` / `_SHEET_MAX_COLS` as **this** server has them, not
    /// as this app remembers them: the window is the server's to choose, and
    /// a save past it is a 413 the person would otherwise not understand.
    public let maxRows: Int
    public let maxCols: Int

    public init(relPath: String = "", rows: [[DocumentsSheetCell?]] = [], maxRows: Int = 1000, maxCols: Int = 60) {
        self.relPath = relPath
        self.rows = rows
        self.maxRows = maxRows
        self.maxCols = maxCols
    }

    private enum CodingKeys: String, CodingKey {
        case rows
        case relPath = "rel_path"
        case maxRows = "max_rows"
        case maxCols = "max_cols"
    }

    public init(from decoder: any Decoder) throws {
        guard let values = try? decoder.container(keyedBy: CodingKeys.self) else {
            self.init()
            return
        }
        self.init(
            relPath: try values.decodeIfPresent(String.self, forKey: .relPath) ?? "",
            rows: try values.decodeIfPresent([[DocumentsSheetCell?]].self, forKey: .rows) ?? [],
            maxRows: try values.decodeIfPresent(Int.self, forKey: .maxRows) ?? 1000,
            maxCols: try values.decodeIfPresent(Int.self, forKey: .maxCols) ?? 60
        )
    }

    /// The grid the editor draws: display strings, padded to at least eight
    /// rows and four columns, every row the same width.
    ///
    /// `SheetEditor.kt:80-86`, number for number. The padding is what gives a
    /// one-cell `.csv` somewhere to type, and the rectangle is what lets the
    /// save send the whole grid back as the server's diff expects.
    public var padded: [[String]] {
        let columns = max(4, rows.map(\.count).max() ?? 0)
        let height = max(8, rows.count)
        return (0..<height).map { row in
            let source = row < rows.count ? rows[row] : []
            return (0..<columns).map { column in
                column < source.count ? (source[column]?.display ?? "") : ""
            }
        }
    }
}

/// What the sheet editor got for a file.
public enum DocumentsSheetLoad: Equatable, Sendable {
    case loading
    case ready
    /// The server answered **415**: a sheet type it cannot round-trip
    /// (`.xls`, `.ods`). ``DocumentsStrings/sheetUnsupported`` is the
    /// sentence, and the only control under it is Close —
    /// `SheetEditor.kt:147`.
    case unsupported
    case failed(String)
}

/// The grid editor: read `/api/documents/sheet`, edit cells, PUT the grid
/// back.
///
/// `SheetEditor.kt:61`'s `SheetEditorOverlay`, as a model.
///
/// ### Nothing is parsed on this phone
///
/// Android has 1,220 lines of spreadsheet code — `XlsxSheet.kt`,
/// `MiniXml.kt`, `CsvSheet.kt` — and **none of it is ported**, because its
/// only Files-side caller there is `PhoneFileSource.kt` and this app has no
/// phone-folder file source yet. For a file in the house the server sends the
/// grid and takes the grid, patching the workbook cell by changed cell at its
/// end so formatting, other sheets and charts survive. Porting a parser with
/// no call site is the seam-with-no-caller defect, not a feature; see
/// `overnight/documents/DECISIONS.md`.
///
/// ### What is not, and why there is no control for it
///
/// * **No formula evaluation.** A typed `=SUM(A1:A2)` is stored as a formula
///   and the server's workbook evaluates it when it is opened; this grid
///   shows the formula text, as Android's does. A number this app computed
///   itself could disagree with the file.
/// * **No row or column insertion, no formatting, no second worksheet.** The
///   route is a window onto the first sheet (`_read_sheet_grid`), and a
///   control for something outside that window would be one the save drops.
/// * **No Save** where this phone may not write — see
///   ``DocumentsEditorModel``, same rule.
@MainActor
@Observable
public final class DocumentsSheetModel {

    public private(set) var entry: FileEntry
    public private(set) var load: DocumentsSheetLoad = .loading

    /// The grid being edited: rows of display strings, padded.
    public private(set) var grid: [[String]] = []

    /// The grid as it was first shown — what ``dirty`` compares against, and
    /// what a save replaces on success (`SheetEditor.kt:70`).
    public private(set) var loadedGrid: [[String]] = []

    /// The window this server reads and writes in.
    public private(set) var maxRows = 1000
    public private(set) var maxCols = 60

    public let canWrite: Bool
    public private(set) var saving = false
    public private(set) var saveFailure: String?
    public private(set) var toast: String?

    /// After a save landed: Files reloads the folder (a save changes the
    /// file's size and time).
    public var onSaved: (@MainActor () -> Void)?

    private let deviceID: String?
    private let send: @Sendable (String, String, Data?) async throws -> Data
    private var epoch = 0

    /// How many grids this model has been handed.
    ///
    /// A save and a read can be in flight at once — Files reloads the folder,
    /// or somebody else's save landed and the person pulled the sheet again —
    /// and when the read wins the race the grid on screen is **newer** than
    /// the one that was sent. Marking the newer grid "as saved" then would
    /// make an edit that is still unsent look saved. The save still says
    /// `saved`, because it did save; it just does not touch the baseline.
    private var generation = 0

    public init(
        entry: FileEntry,
        canWrite: Bool,
        deviceID: String? = nil,
        send: @Sendable @escaping (String, String, Data?) async throws -> Data
    ) {
        self.entry = entry
        self.canWrite = canWrite
        self.deviceID = deviceID
        self.send = send
    }

    public convenience init(entry: FileEntry, canWrite: Bool, deviceID: String? = nil, in container: AppContainer) {
        let client = container.client
        self.init(entry: entry, canWrite: canWrite, deviceID: deviceID, send: { method, path, body in
            // Sixty seconds on the save: an `.xlsx` is patched on the server
            // in a one-thread pool (`documents.py:1297`), so a big workbook's
            // save can queue behind another.
            try await client.raw(method: method, path: path, body: body, trackPairing: false, timeout: 60)
        })
    }

    // MARK: - what the chrome draws

    public var dirty: Bool { grid != loadedGrid }

    public var canSave: Bool { canWrite && dirty && !saving && load == .ready }

    public var offersSave: Bool { canWrite && load == .ready }

    public var title: String { entry.displayTitle }

    public var columns: Int { grid.first?.count ?? 0 }

    public func clearToast() { toast = nil }

    /// `0 → "A"`, `26 → "AA"`, `27 → "AB"` — `colName`
    /// (`SheetModel.kt:128`), which is the column header and the first half
    /// of a cell's address.
    public static func columnName(_ index: Int) -> String {
        guard index >= 0 else { return "" }
        var n = index + 1
        var out = ""
        while n > 0 {
            let remainder = (n - 1) % 26
            out.append(Character(UnicodeScalar(UInt8(65 + remainder))))
            n = (n - 1) / 26
        }
        return String(out.reversed())
    }

    /// `"B12"` — the address a cell's accessibility label needs, because a
    /// grid of unlabelled text fields is unreadable to VoiceOver. Android's
    /// `refOf` (`SheetModel.kt:144`); its grid has a row-number gutter and
    /// no per-cell label at all, which is a gap on that side.
    public static func address(row: Int, column: Int) -> String {
        "\(columnName(column))\(row + 1)"
    }

    public func cell(row: Int, column: Int) -> String {
        guard row < grid.count, column < grid[row].count else { return "" }
        return grid[row][column]
    }

    /// Type into one cell. Out-of-range is ignored rather than trapping: a
    /// keystroke that arrives after a reload shrank the grid is a race, not a
    /// programming error, and a crash in a spreadsheet is the worst possible
    /// answer to it.
    public func setCell(row: Int, column: Int, to typed: String) {
        guard row < grid.count, column < grid[row].count else { return }
        guard grid[row][column] != typed else { return }
        grid[row][column] = typed
        saveFailure = nil
    }

    // MARK: - loading

    public func read() {
        load = .loading
        let mine = epoch
        let path = DocumentsPath.sheet(rel: entry.rel)
        let call = send
        Task { @MainActor [weak self] in
            do {
                let body = try await call("GET", path, nil)
                guard let self, mine == self.epoch else { return }
                guard let read = try? DomovoiClient.decode(body, as: DocumentsSheetGrid.self) else {
                    self.load = .failed(DocumentsStrings.couldNotLoad(DocumentsEditorModel.notDomovoi))
                    return
                }
                self.maxRows = read.maxRows
                self.maxCols = read.maxCols
                let padded = read.padded
                self.grid = padded
                self.loadedGrid = padded
                self.generation += 1
                self.load = .ready
            } catch {
                guard let self, mine == self.epoch else { return }
                // A 415 is the server's **answer**: this sheet type is not
                // one it round-trips (`_assert_sheet_editor_may_write`).
                // Android treats its own `SheetUnsupported` and a 415 the
                // same way (`SheetEditor.kt:88`).
                if (error as? DomovoiError)?.failure?.status == 415 {
                    self.load = .unsupported
                } else {
                    self.load = .failed(DocumentsStrings.couldNotLoad(FilesModel.why(error)))
                }
            }
        }
    }

    // MARK: - saving

    /// PUT the **whole** grid back.
    ///
    /// Not only the changed cells, although the model knows which they are:
    /// `PUT /sheet` diffs what it is given against the file
    /// (`documents.py:1275`), so a body holding just the edits would blank
    /// every cell left out of it. Android sends the whole grid for the same
    /// reason (`FileSource.kt:256`) and keeps `loadedGrid` only for the
    /// phone-file path, which is not ported.
    public func save() {
        guard canSave else { return }
        saving = true
        saveFailure = nil
        let mine = epoch
        // The grid, the path and the rows are all taken BEFORE the await: a
        // save that lands after this model moved on must not mark another
        // file's grid saved ([[read-after-await-wrong-key]]).
        let sent = grid
        let base = generation
        let path = DocumentsPath.sheet(rel: entry.rel)
        let body = DocumentsPath.sheetBody(rows: sent, deviceID: deviceID)
        let call = send
        Task { @MainActor [weak self] in
            do {
                _ = try await call("PUT", path, body)
                guard let self, mine == self.epoch else { return }
                self.saving = false
                // Only when nothing newer has been read since: see
                // ``generation``.
                if base == self.generation { self.loadedGrid = sent }
                self.toast = DocumentsStrings.saved
                self.onSaved?()
            } catch {
                guard let self, mine == self.epoch else { return }
                self.saving = false
                // 413 (a row past the column cap), 409 (an `.xlsx` holding
                // charts or pivots, which the server will not strip) and 415
                // all come with the server's own sentence, and each one is
                // something the person can act on — widen a sheet elsewhere,
                // or save this one on the dashboard.
                let said = DocumentsEditorModel.whySaveFailed(error)
                self.saveFailure = said
                self.toast = DocumentsStrings.saveFailed(said)
            }
        }
    }
}
