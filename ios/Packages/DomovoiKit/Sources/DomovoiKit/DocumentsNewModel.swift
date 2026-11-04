import Foundation
import Observation

/// What `+ New` can make: the kinds this app has an editor for.
///
/// `NewDocument.kt:22-34`, which is the dashboard's `NEW_KINDS` narrowed the
/// same way — and narrowed once more here:
///
/// * **Drawing** is left out on both sides: neither app has a drawing editor,
///   and a new `.excalidraw` would open in nothing (`NewDocument.kt:17-21`).
/// * **Document** (`kind: "doc"`, which makes an `.html`) is left out **on
///   iOS only**, because the formatted-document editor is the WebView host
///   that is not ported: the file would be made and then have nowhere to
///   open. A person who wants a formatted document makes it on the
///   dashboard; a person who wants a markdown one types `notes.md` into Text
///   file, which the server takes verbatim (`documents.py:772-785`) and this
///   app opens in the markdown editor. Recorded in
///   `overnight/documents/DECISIONS.md`.
public enum DocumentsNewKind: String, CaseIterable, Equatable, Sendable, Identifiable {
    case spreadsheet
    case text

    public var id: String { rawValue }

    /// `kind` for `POST /api/documents/create`.
    public var api: String {
        switch self {
        case .spreadsheet: return "sheet"
        case .text: return "text"
        }
    }

    public var label: String {
        switch self {
        case .spreadsheet: return "Spreadsheet"
        case .text: return "Text file"
        }
    }

    /// The line under the label in the menu.
    public var hint: String {
        switch self {
        case .spreadsheet: return "Workbook · .xlsx"
        case .text: return "Plain text · name as typed"
        }
    }

    /// The naming dialog's title — "New <kind> name", as the dashboard asks
    /// it.
    public var prompt: String {
        switch self {
        case .spreadsheet: return "new spreadsheet"
        case .text: return "new text file"
        }
    }

    public var placeholder: String {
        switch self {
        case .spreadsheet: return "e.g. Budget"
        case .text: return "e.g. shopping.txt"
        }
    }

    /// The SF Symbol the menu row draws. Android's are
    /// `Outlined.TableChart` and `AutoMirrored.Outlined.Article`
    /// (`FilesScreen.kt:564-566`); these are the closest Apple glyphs, and
    /// the same two ``FileRowView`` already uses for a sheet and a text file,
    /// so one file does not get two different pictures in one screen.
    public var glyph: String {
        switch self {
        case .spreadsheet: return "tablecells"
        case .text: return "doc.text"
        }
    }
}

/// Making a new file in the household's Documents, and opening it.
///
/// `FilesScreen.kt:542-591` (the menu) and `:849-875` (the dialog), as a
/// model — the dialog's `remember`s, the in-flight flag and the refusal that
/// keeps it open all live here, because a sheet that goes away mid-create
/// must not take the create with it.
///
/// ### What is not, and why there is no control for it
///
/// * `+ New` is drawn **only** in Documents, and only where this phone may
///   write: the library is editable and an admin has not blocked this device.
///   Android draws it for a blocked device and toasts the block's reason when
///   a kind is picked (`FilesScreen.kt:582`); here the card above the list
///   already says who to ask, so the dead control is simply not there
///   ([[no-misleading-controls]]).
/// * No folder is created and nothing is uploaded: `POST /create` makes one
///   blank file at the **top** of Documents, wherever the browser happens to
///   be looking — which is what ``note(inSubfolder:)`` exists to say out
///   loud before the person taps create.
@MainActor
@Observable
public final class DocumentsNewModel {

    /// The kind being named, or nil when no dialog is up.
    public private(set) var kind: DocumentsNewKind?

    /// The name field. `var`: the field writes into it.
    public var name = ""

    public private(set) var creating = false

    /// The server's refusal, under the field, with the dialog still open so
    /// the name can be fixed — `dismissOnConfirm = false`
    /// (`FilesScreen.kt:857`).
    public private(set) var failure: String?

    /// The file that was just made, as a Files row.
    ///
    /// Files opens it **exactly as tapping its row would**
    /// (`FilesScreen.kt:862-867`), which is the whole reason the created row
    /// is turned into a ``FileEntry`` rather than a special case: one
    /// routing table, one answer, and a "new spreadsheet" that opens in the
    /// grid editor because that is what a `.xlsx` row does.
    public private(set) var created: FileEntry?

    private let deviceID: String?
    private let send: @Sendable (String, Data) async throws -> Data

    public init(
        deviceID: String? = nil,
        send: @Sendable @escaping (String, Data) async throws -> Data
    ) {
        self.deviceID = deviceID
        self.send = send
    }

    public convenience init(deviceID: String? = nil, in container: AppContainer) {
        let client = container.client
        self.init(deviceID: deviceID, send: { path, body in
            try await client.raw(method: "POST", path: path, body: body, trackPairing: false, timeout: 20)
        })
    }

    // MARK: - the dialog

    public func pick(_ picked: DocumentsNewKind) {
        kind = picked
        name = ""
        failure = nil
    }

    public func cancel() {
        kind = nil
        name = ""
        failure = nil
    }

    /// The created row has been opened: forget it, so a second create is a
    /// second open and not a repeat of the first.
    public func opened() { created = nil }

    public var canCreate: Bool {
        !creating && !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// The line under the field when the browser is **not** at the library's
    /// root: the file does not go where you are looking
    /// (`FilesScreen.kt:850`).
    public func note(inSubfolder: Bool) -> String? {
        inSubfolder ? DocumentsStrings.goesAtTheTop : nil
    }

    // MARK: - creating

    public func create() {
        guard canCreate, let kind else { return }
        creating = true
        failure = nil
        // Both captured before the await, as every write in this item is.
        let asked = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let body = DocumentsPath.createBody(name: asked, kind: kind, deviceID: deviceID)
        let call = send
        Task { @MainActor [weak self] in
            do {
                let said = try await call(DocumentsPath.create, body)
                guard let self else { return }
                self.creating = false
                let row = (try? DomovoiClient.decode(said, as: DocumentsRow.self)) ?? DocumentsRow()
                guard let entry = Self.entry(from: row) else {
                    // A 200 whose body is not a `DocumentRow` — the file may
                    // well have been made, so this says it could not be
                    // opened rather than that nothing happened, and the
                    // dialog stays up with the name still in it.
                    self.failure = DocumentsStrings.couldNotCreate
                    return
                }
                self.kind = nil
                self.name = ""
                self.created = entry
            } catch {
                guard let self else { return }
                self.creating = false
                self.failure = Self.failureText(error)
            }
        }
    }

    /// The created `DocumentRow` as a Files row — `asEntry`
    /// (`NewDocument.kt:56`).
    ///
    /// The name comes from **`rel_path`'s last component**, not from `name`:
    /// the server answers `name: target.stem`, so a new `Budget.xlsx` has
    /// `name: "Budget"` and a row built from that would be classified as an
    /// extensionless file and open in the text editor.
    static func entry(from row: DocumentsRow) -> FileEntry? {
        let rel = row.relPath
        guard !rel.isEmpty else { return nil }
        let fileName = rel.split(separator: "/").last.map(String.init) ?? rel
        return FileEntry(
            name: fileName,
            rel: rel,
            isDir: false,
            size: row.size,
            mtime: row.modifiedAt,
            kind: FileKinds.kind(of: fileName, isDir: false)
        )
    }

    /// Why a create was refused, in plain words — `createFailureText` and
    /// `createFailureMessage` (`NewDocument.kt:64-88`) in one, because the
    /// Kotlin's split is only there to pull `detail` out of an OkHttp body.
    static func failureText(_ error: any Error) -> String {
        guard let failure = (error as? DomovoiError)?.failure else {
            return FilesModel.why(error)
        }
        return failureMessage(
            status: failure.status,
            detail: DocumentsEditorModel.detail(in: failure.body)
        )
    }

    /// `createFailureMessage` (`NewDocument.kt:64`), rule for rule.
    ///
    /// A 409's detail is `"<name> already exists"` (`documents.py:787`), and
    /// the name is pulled back out of it so the sentence can name the file
    /// the person must rename — which is the one refusal here they can fix
    /// without asking anybody.
    static func failureMessage(status: Int?, detail: String?) -> String {
        let said = detail?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if status == 409 {
            let suffix = " already exists"
            if said.hasSuffix(suffix) {
                let taken = String(said.dropLast(suffix.count))
                if !taken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    return DocumentsStrings.nameTaken(taken)
                }
            }
            return DocumentsStrings.someNameTaken
        }
        if !said.isEmpty { return said }
        if status == 401 || status == 403 { return DocumentsStrings.notAllowedToCreate }
        return DocumentsStrings.couldNotCreate
    }
}
