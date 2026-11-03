import Foundation
import Observation

/// What the text or markdown editor got for a file.
///
/// `DocumentsEditor.kt:51`'s `EditorStatus`, with its four states and the
/// same meanings: the one that is not a failure is ``notEditable(_:)`` — the
/// server **answered**, with a 415 or a 413, and said the file is not one
/// this editor opens.
public enum DocumentsEditorLoad: Equatable, Sendable {
    case loading
    case ready
    /// The sentence to draw, already in words
    /// (``DocumentsStrings/cannotPreview(_:reason:)``,
    /// ``DocumentsStrings/tooBigToEdit``, or the server's own detail).
    case notEditable(String)
    case failed(String)
}

/// Editing one of the household's documents as text: the plain-text editor
/// and the markdown editor, which differ only in the route they use.
///
/// `DocumentsEditor.kt:248`'s `TextEditorOverlay`, as a model — the state it
/// keeps in `remember`s lives here instead, for the reason every other
/// screen in this app keeps its writes off the view: a row or a sheet that
/// goes away mid-save must not take the save with it, and a rule that only
/// exists inside a `body` cannot be tested.
///
/// ### The two routes, and why markdown is not the text one
///
/// * ``DocumentsEditorKind/text``: `GET`/`PUT /api/documents/text`,
///   `{text: …}`.
/// * ``DocumentsEditorKind/markdown``: `GET`/`PUT /api/documents/doc`,
///   `{markdown: …}` — the route the dashboard's own editor uses for a `.md`,
///   which sanitises nothing a markdown file needs sanitising but does carry
///   the one guard a phone cannot replace: `_assert_not_blanking`
///   (`documents.py:1861`) refuses an **empty** save over a file that says
///   something, with a 409 and a sentence. A phone editor that has not
///   finished loading, or is being dismissed, is exactly the "passing state"
///   that guard is written about. `PUT /text` would take the same save and
///   truncate the file.
///
/// ### What is not, and why there is no control for it
///
/// * **No autosave.** The save is the button, as Android's is
///   (`DocumentsEditor.kt:275`), and closing with edits in hand asks first.
///   Autosave without the dashboard's version history is a feature that can
///   only lose work.
/// * **No Save at all** where this phone may not write — a read-only
///   library, or a device an admin has blocked. ``canWrite`` is false, the
///   editor says ``DocumentsStrings/cannotChangeFilesHere`` and draws no
///   button ([[no-misleading-controls]]).
/// * **No `allow_empty`.** See ``DocumentsPath/docBody(markdown:deviceID:)``.
/// * **No co-editing notice.** `/api/documents/presence` exists and is not
///   called: this app has no way to act on "somebody else has this open".
@MainActor
@Observable
public final class DocumentsEditorModel {

    /// The file open now, and the only one this model ever has: one model is
    /// built per presentation of ``DocumentsTextEditorView`` and nothing
    /// reassigns these two. See ``generation`` for what was removed and why.
    public private(set) var entry: FileEntry

    public private(set) var kind: DocumentsEditorKind

    public private(set) var load: DocumentsEditorLoad = .loading

    /// The buffer the field is bound to.
    ///
    /// `var`, because a `TextEditor` writes straight into it; everything that
    /// depends on it (``dirty``, ``canSave``) is derived rather than kept, so
    /// there is no second copy to fall out of step with what is on screen.
    public var text: String = ""

    /// The text as the server last gave it, or as the last save left it —
    /// what ``dirty`` compares against.
    public private(set) var loadedText: String = ""

    /// Whether this phone may write here at all: the library is editable and
    /// an admin has not blocked this device (``FilesModel/canWrite``).
    public let canWrite: Bool

    public private(set) var saving = false

    /// The refusal the last save came back with, in the server's own words
    /// where it had any — the 409 `empty_over_content` message especially,
    /// which is the one refusal a person can act on by typing something.
    ///
    /// Gone as soon as the buffer is not the one that was refused, which is
    /// what the sentence is about: after `empty_over_content` the person
    /// types the content back in, and a red line still saying the save would
    /// have emptied the file describes a buffer that no longer exists. It is
    /// **computed** rather than cleared on an edit, because ``text`` is a
    /// plain `var` a `UITextView` writes straight into — and the
    /// `@Observable` macro rejects a `didSet` on a tracked stored property,
    /// so there is no observer to hang the clearing on.
    /// ``DocumentsSheetModel/setCell(row:column:to:)`` clears its own in the
    /// setter it does have; these two disagreed, and this comment described
    /// the sheet's behaviour rather than this one's.
    public var saveFailure: String? { text == refusedText ? storedSaveFailure : nil }

    /// The refusal itself, and the buffer it was about.
    private var storedSaveFailure: String?
    private var refusedText: String?

    /// One line for the shell's toast: `saved`, or `save failed: …`.
    /// Android's two toasts (`DocumentsEditor.kt:283`, `:285`) in the one
    /// this shell draws at a time.
    public private(set) var toast: String?

    /// Called after a save landed, so Files can reload the folder —
    /// Android's `pager.reload()` (`FilesScreen.kt:794`).
    public var onSaved: (@MainActor () -> Void)?

    private let deviceID: String?
    private let send: @Sendable (String, String, Data?) async throws -> Data

    /// How many times this model has been handed a document's text.
    ///
    /// A read and a save can be in flight at once — the retry link calls
    /// ``read()`` again — and when the read wins the race the buffer on
    /// screen is **newer** than what was sent. Calling that newer text saved
    /// would make an unsent edit look safe, so the baseline is left alone;
    /// the save still says `saved`, because it did save.
    ///
    /// There was a file **epoch** beside this, with an `open(_:kind:)` that
    /// bumped it and two package tests that drove it, and none of it could
    /// ever run: `editing` in Files is only ever assigned with the cover
    /// down, and `.fullScreenCover(item:)` builds a fresh view — and so a
    /// fresh model — per presentation. A guard that cannot fire, with tests
    /// that pin it, is worse than no guard at all: the night's negative
    /// control could break it and nothing would fail. Removed rather than
    /// wired up, because wiring it up would mean one model surviving across
    /// files, which this screen does not do ([[reasoning-that-hides-a-bug]];
    /// see `overnight/documents/DECISIONS.md`).
    private var generation = 0

    public init(
        entry: FileEntry,
        kind: DocumentsEditorKind,
        canWrite: Bool,
        deviceID: String? = nil,
        send: @Sendable @escaping (String, String, Data?) async throws -> Data
    ) {
        self.entry = entry
        self.kind = kind
        self.canWrite = canWrite
        self.deviceID = deviceID
        self.send = send
    }

    public convenience init(
        entry: FileEntry,
        kind: DocumentsEditorKind,
        canWrite: Bool,
        deviceID: String? = nil,
        in container: AppContainer
    ) {
        let client = container.client
        self.init(entry: entry, kind: kind, canWrite: canWrite, deviceID: deviceID, send: { method, path, body in
            // `trackPairing: false`: a 415 from one document is not this
            // phone losing its pairing, and raising the pairing screen from a
            // file that would not decode is the defect
            // [[reasoning-that-hides-a-bug]] records — ``FileTextModel`` says
            // the same about the same route.
            try await client.raw(method: method, path: path, body: body, trackPairing: false, timeout: 20)
        })
    }

    // MARK: - what the chrome draws

    public var dirty: Bool { text != loadedText }

    /// Save is drawn, and enabled, only when it would do something: there is
    /// a change, nothing is in flight, the file loaded, and this phone may
    /// write.
    public var canSave: Bool {
        canWrite && dirty && !saving && load == .ready
    }

    /// Whether a Save button is drawn at all. False means the editor says
    /// ``DocumentsStrings/cannotChangeFilesHere`` instead.
    public var offersSave: Bool { canWrite && load == .ready }

    public var pill: String { kind.pill }

    /// The title bar: the file's own name, as a list shows it.
    public var title: String { entry.displayTitle }

    public func clearToast() { toast = nil }

    // MARK: - loading

    public func read() {
        load = .loading
        let path = kind == .markdown ? DocumentsPath.doc(rel: entry.rel) : DocumentsPath.text(rel: entry.rel)
        let shown = entry.displayTitle
        let asked = kind
        let call = send
        Task { @MainActor [weak self] in
            do {
                let body = try await call("GET", path, nil)
                guard let self else { return }
                guard let loaded = Self.body(body, for: asked) else {
                    self.load = .failed(DocumentsStrings.couldNotLoad(Self.notDomovoi))
                    return
                }
                self.text = loaded
                self.loadedText = loaded
                self.generation += 1
                self.load = .ready
            } catch {
                guard let self else { return }
                self.load = Self.refusal(error, named: shown, kind: asked)
            }
        }
    }

    /// `{"text": …}` from `/text`, `{"markdown": …}` from `/doc`.
    ///
    /// A 200 with neither key is **not an empty document**: a captive portal
    /// or a proxy's page would otherwise be shown as a file with nothing in
    /// it, and the person would believe their notes had been emptied — then
    /// save the emptiness back. ``FileTextModel/text(of:)`` refuses the same
    /// body for the same reason.
    static func body(_ data: Data, for kind: DocumentsEditorKind) -> String? {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }
        return object[kind == .markdown ? "markdown" : "text"] as? String
    }

    /// What a 200 that is not this route's answer is called, in the words
    /// ``DomovoiClient`` uses for the same thing.
    static let notDomovoi = "that isn't Domovoi"

    /// The server's refusals, told apart — because two of the three are
    /// answers about the file and not failures of the request.
    ///
    /// * **415** from `/text`: `{"editable": false, "reason":
    ///   "too_large"|"binary"}` (`documents.py:876`), which becomes Android's
    ///   own sentence with the file's name in it.
    /// * **415** from `/doc`: a plain `detail` string — the extension is not
    ///   one the document editor opens. Shown as the server wrote it.
    /// * **413** from `/doc`: over 32 MiB (`_DOC_MAX_BYTES`).
    static func refusal(_ error: any Error, named: String, kind: DocumentsEditorKind) -> DocumentsEditorLoad {
        guard let failure = (error as? DomovoiError)?.failure else {
            return .failed(DocumentsStrings.couldNotLoad(FilesModel.why(error)))
        }
        if failure.status == 413 { return .notEditable(DocumentsStrings.tooBigToEdit) }
        guard failure.status == 415 else {
            return .failed(DocumentsStrings.couldNotLoad(failure.message))
        }
        if let reason = Self.field("reason", in: failure.body) {
            return .notEditable(DocumentsStrings.cannotPreview(named, reason: reason))
        }
        if let detail = Self.detail(in: failure.body) {
            return .notEditable(detail)
        }
        // A 415 whose body this app cannot read still means "not as text",
        // which is the useful half of it — the same fallback
        // ``FileTextModel/refusal(_:)`` takes.
        return .notEditable(DocumentsStrings.cannotPreview(named, reason: "binary"))
    }

    // MARK: - saving

    /// Write the buffer back. One at a time, and only when there is a change
    /// — Android's `if (saving || !dirty) return` (`DocumentsEditor.kt:276`).
    public func save() {
        guard canSave else { return }
        saving = true
        storedSaveFailure = nil
        refusedText = nil
        // Captured **before** the await: the path, the bytes being sent, and
        // the text they are. A read that lands first makes the buffer newer
        // than what was sent, and only the sent text may become the baseline.
        let sent = text
        let base = generation
        let path = kind == .markdown ? DocumentsPath.doc(rel: entry.rel) : DocumentsPath.text(rel: entry.rel)
        let body = kind == .markdown
            ? DocumentsPath.docBody(markdown: sent, deviceID: deviceID)
            : DocumentsPath.textBody(text: sent, deviceID: deviceID)
        let call = send
        Task { @MainActor [weak self] in
            do {
                _ = try await call("PUT", path, body)
                guard let self else { return }
                self.saving = false
                // Only when nothing newer has been read since: see
                // ``generation``.
                if base == self.generation { self.loadedText = sent }
                self.toast = DocumentsStrings.saved
                self.onSaved?()
            } catch {
                guard let self else { return }
                self.saving = false
                let said = Self.whySaveFailed(error)
                // Remembered with the buffer it was about, so the sentence
                // goes the moment that buffer is typed over. See
                // ``saveFailure``.
                self.storedSaveFailure = said
                self.refusedText = sent
                self.toast = DocumentsStrings.saveFailed(said)
            }
        }
    }

    /// Why a save was refused, in the server's own words where it had any.
    ///
    /// The one that matters is the **409** from `_assert_not_blanking`, whose
    /// `detail` is an object: `{"reason": "empty_over_content", "message":
    /// "Not saved: this would have replaced the document with an empty
    /// one."}`. Showing `409: {…}` for that would turn the one refusal a
    /// person can act on into noise.
    static func whySaveFailed(_ error: any Error) -> String {
        guard let failure = (error as? DomovoiError)?.failure else {
            return FilesModel.why(error)
        }
        if let message = Self.field("message", in: failure.body, insideDetail: true) {
            return message
        }
        if let detail = Self.detail(in: failure.body) { return detail }
        return failure.message
    }

    // MARK: - reading a field out of an error body

    /// A top-level string field of an error body — or one inside its
    /// `detail` object, which is where FastAPI puts a dictionary detail.
    static func field(_ name: String, in body: String, insideDetail: Bool = false) -> String? {
        guard let data = body.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        if insideDetail {
            guard let detail = object["detail"] as? [String: Any] else { return nil }
            return detail[name] as? String
        }
        return object[name] as? String
    }

    /// `{"detail": "…"}` — a plain-string detail, which is what every guard
    /// in `documents.py` but the blanking one raises.
    static func detail(in body: String) -> String? {
        guard let data = body.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let detail = object["detail"] as? String,
              !detail.isEmpty else { return nil }
        return detail
    }
}
