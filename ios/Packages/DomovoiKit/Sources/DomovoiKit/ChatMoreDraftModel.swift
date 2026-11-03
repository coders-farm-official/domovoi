import Foundation
import Observation

/// One draft, open in the canvas: its versions, the changes switch, a hand
/// edit, a save and the file.
///
/// `DraftCanvas` (`DraftUi.kt:291`) and `ServerDraftDoc` (`ServerDrafts.kt:46`)
/// as one model, because every decision in them is one a view cannot be asked
/// about: which version a card opens at, whether the changes switch starts on,
/// what a 409 means, and which format a save writes.
///
/// ### What is not here, and why there is no control for it
///
/// * **Print.** Android draws no print at all without
///   `GET /api/printers` saying the house has a printer (`canPrint`,
///   `ChatScreen.kt:1013`; the canvas's `onPrint` is nil without one,
///   `:970`). That read belongs to the Printing item, so drawing nothing is
///   **Android's own behaviour on a house with no printer**, not a
///   divergence — and an AirPrint sheet on the exported PDF is not a
///   substitute, because Android's print comes out of the printer at home.
/// * **The Revise box** (`DraftUi.kt:263`), which sends `revise_draft` on a
///   message — a send-body key this item cannot write. Hand editing reaches
///   the same place through a route of its own, so the feature is not lost,
///   only the "ask it to rewrite this" shortcut.
/// * **"Send to home"** (`ServerDrafts.kt:171`), which posts to
///   `/api/documents/upload` — the Documents item's route, and ``save(format:)``
///   already puts the file in the house.
@MainActor
@Observable
public final class ChatMoreDraftModel {

    private let container: AppContainer
    private let deviceID: String?
    private let seenStore: ChatMoreDraftSeenStore

    /// The draft open, or nil.
    public private(set) var draftID: Int64?

    public private(set) var view: ChatMoreDraftView?

    /// Which version is on screen.
    public private(set) var selected = 0

    /// Whether the changes of the selected version are shown.
    public private(set) var showChanges = false

    /// The text being edited, or nil when nothing is.
    public private(set) var editing: String?

    /// A read, a save or an edit is in flight.
    public private(set) var busy = false

    /// What the canvas says went wrong reading the draft.
    public private(set) var error: String?

    /// What a save put where.
    public private(set) var saved: ChatMoreDraftSaveResult?

    public private(set) var toast: String?

    /// The house, as the seen key spells it (`ServerDrafts.kt:47`).
    private var server: String { ServerCredentials.normalize(container.store.serverURL) }

    /// Houses **and** drafts, for the reason ``ChatMoreModel`` gives: an
    /// answer for draft 4 must not land in draft 9's canvas.
    private var epoch = 0

    private func stillHere(_ asked: Int) -> Bool { asked == epoch }

    public init(
        in container: AppContainer,
        deviceID: String?,
        seen: ChatMoreDraftSeenStore = ChatMoreDraftSeenStore()
    ) {
        self.container = container
        self.deviceID = deviceID
        self.seenStore = seen
    }

    /// The draft's own key, which is where "already seen" is kept.
    public var seenKey: String? {
        guard let draftID else { return nil }
        return ChatMoreDraftSeenStore.key(server: server, draft: draftID)
    }

    // MARK: - opening

    /// Open `id`, at the version a card named when the draft still has it.
    ///
    /// A card for v2 of a draft now at v3 opens **v2**: the reply the card
    /// sits under is about v2, and opening v3 would show words that reply
    /// never wrote (``ChatMoreDraftRules/opensAt(_:asked:)``).
    public func open(draft id: Int64, at version: Int? = nil) {
        epoch += 1
        draftID = id
        view = nil
        editing = nil
        error = nil
        saved = nil
        selected = version ?? 0
        showChanges = false
        load(jumpTo: version)
    }

    public func close() {
        epoch += 1
        draftID = nil
        view = nil
        editing = nil
        busy = false
    }

    public func serverChanged() {
        close()
    }

    /// Re-read the draft.
    ///
    /// `keepEdit` exists for one caller. ``select(_:)`` clears ``editing``,
    /// because picking a version throws an edit of a different one away — so a
    /// plain reload after a refused save would also throw the typed words
    /// away, and the 409's sentence promises the opposite. The words are held
    /// across the round trip and put back.
    private func load(jumpTo: Int?, keepEdit: Bool = false) {
        guard let id = draftID else { return }
        let client = container.client
        let asked = epoch
        busy = true
        Task { @MainActor [weak self] in
            guard let self else { return }
            do throws(DomovoiError) {
                let answer = try await client.get(ChatPath.draft(id), as: ChatMoreDraftView.self)
                guard self.stillHere(asked) else { return }
                self.view = answer
                self.error = nil
                self.busy = false
                let pick = ChatMoreDraftRules.opensAt(answer, asked: jumpTo)
                let held = keepEdit ? self.editing : nil
                self.select(pick)
                if let held { self.editing = held }
            } catch {
                guard self.stillHere(asked) else { return }
                self.busy = false
                self.error = ChatMoreStrings.couldNotReadTheDraft(error.message)
            }
        }
    }

    // MARK: - the versions

    public var versions: [ChatMoreDraftVersion] { view?.versions ?? [] }

    public var version: ChatMoreDraftVersion? {
        view?.version(selected) ?? view?.latest
    }

    public var latest: ChatMoreDraftVersion? { view?.latest }

    /// The format a save writes by default, and the other button's.
    public var format: String { view?.draft.format ?? "docx" }
    public var otherFormat: String { view?.otherFormat ?? "pdf" }

    /// Which version this is, in words — "edited by you" only on the install
    /// that made the edit (``ChatMoreDraftRules/sourceLabel(_:thisDeviceID:)``).
    public var sourceLabel: String {
        ChatMoreDraftRules.sourceLabel(version, thisDeviceID: deviceID)
    }

    /// Pick a version: the changes switch follows what this phone has already
    /// seen, and the version is marked seen (`DraftUi.kt:328-331`).
    public func select(_ wanted: Int) {
        guard wanted > 0 else { return }
        selected = wanted
        editing = nil
        let seen = seenKey.map { seenStore.seen($0) } ?? 0
        // `changesOn` reads what was seen BEFORE this version is marked, or
        // the switch would be off the first time every version is opened —
        // the inverted version of this check is the bug the UI case names.
        showChanges = ChatMoreDraftRules.changesOn(version: wanted, seen: seen)
            && view?.version(wanted)?.changes != nil
        if let key = seenKey { seenStore.mark(key, wanted) }
    }

    /// The switch is live only for a version that has changes, and only while
    /// nothing is being edited (`DraftUi.kt:440`).
    public var changesSwitchLive: Bool {
        ChatMoreDraftRules.changesSwitchLive(version, editing: editing != nil)
    }

    public func setShowChanges(_ on: Bool) {
        guard changesSwitchLive else { return }
        showChanges = on
    }

    // MARK: - a hand edit

    /// Start editing the version on screen (`DraftUi.kt:382`).
    public func beginEdit() {
        guard let body = version?.body else { return }
        editing = body
        showChanges = false
    }

    public func setEditedText(_ text: String) {
        guard editing != nil else { return }
        editing = text
    }

    public func cancelEdit() { editing = nil }

    /// What the edit's own button says: the version this would become
    /// (`DraftUi.kt:491`).
    public var nextVersion: Int { (latest?.version ?? 0) + 1 }

    /// Save the edit as the next version (`POST …/versions`).
    ///
    /// **A 409 is not swallowed.** The route refuses an edit whose
    /// `base_version` is not the newest (`_stale`, `chat_drafts.py:481`), and
    /// that is the one answer that must reach a person: the words they typed
    /// are still in the editor, and somebody else's newer version is on the
    /// server. Android says
    /// ``ChatMoreStrings/newerVersionCameIn`` and reloads (`DraftUi.kt:484`).
    @discardableResult
    public func saveEdit() -> Bool {
        guard let id = draftID, let text = editing, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              let base = latest?.version, !busy else { return false }
        let client = container.client
        let asked = epoch
        busy = true
        let body = ChatPath.draftEditBody(text: text, baseVersion: base, deviceID: deviceID)
        Task { @MainActor [weak self] in
            guard let self else { return }
            do throws(DomovoiError) {
                let answer = try await client.post(
                    ChatPath.draftVersions(id),
                    body: body,
                    as: ChatMoreDraftEditResult.self
                )
                guard self.stillHere(asked) else { return }
                self.busy = false
                let written = answer.version?.version ?? base
                self.editing = nil
                self.toast = ChatMoreStrings.savedAsVersion(written)
                self.load(jumpTo: written)
            } catch {
                guard self.stillHere(asked) else { return }
                self.busy = false
                if error.failure?.status == 409 {
                    // The text stays in the editor, which is what the
                    // sentence promises -- and `keepEdit` is what makes that
                    // true: the reload picks a version, and picking a version
                    // is exactly what clears ``editing``. The comment used to
                    // claim the words survived; they did not.
                    self.toast = ChatMoreStrings.newerVersionCameIn
                    self.load(jumpTo: nil, keepEdit: true)
                    return
                }
                self.toast = failureText(ChatMoreStrings.verbSave, error)
            }
        }
        return true
    }

    // MARK: - saving and the file

    /// Save the version on screen into Documents → From Chat
    /// (`POST …/save`, `DraftUi.kt:447`).
    @discardableResult
    public func save(format wanted: String? = nil) -> Bool {
        guard let id = draftID, let version = version?.version, !busy else { return false }
        let client = container.client
        let asked = epoch
        busy = true
        let body = ChatPath.draftSaveBody(
            version: version,
            format: wanted ?? format,
            deviceID: deviceID
        )
        Task { @MainActor [weak self] in
            guard let self else { return }
            do throws(DomovoiError) {
                let result = try await client.post(
                    ChatPath.draftSave(id),
                    body: body,
                    as: ChatMoreDraftSaveResult.self
                )
                guard self.stillHere(asked) else { return }
                self.busy = false
                self.saved = result
                // Read back, so the version's own `saved` list shows the file.
                self.load(jumpTo: version)
            } catch {
                guard self.stillHere(asked) else { return }
                self.busy = false
                self.toast = failureText(ChatMoreStrings.verbSave, error)
            }
        }
        return true
    }

    /// The file itself, for the share sheet: its name and its bytes
    /// (`GET …/export`; `ServerDrafts.kt:111`).
    ///
    /// `async` and not a stored property, because the share sheet needs the
    /// bytes in hand and nothing else does — holding a .docx in the model
    /// until somebody shares it is a megabyte kept for a tap that may never
    /// come.
    public func export(version wanted: Int? = nil, format asked: String? = nil) async
        -> (name: String, bytes: Data)? {
        guard let id = draftID, let version = wanted ?? self.version?.version else { return nil }
        let chosen = asked ?? format
        let client = container.client
        let generation = epoch
        do throws(DomovoiError) {
            let bytes = try await client.raw(
                method: "GET",
                path: ChatPath.draftExport(id, version: version, format: chosen)
            )
            guard stillHere(generation) else { return nil }
            let name = ChatMoreDraftRules.exportName(
                title: view?.draft.title ?? "",
                version: version,
                format: chosen
            )
            return (name, bytes)
        } catch {
            guard stillHere(generation) else { return nil }
            toast = failureText(ChatMoreStrings.verbShare, error)
            return nil
        }
    }

    public func setToast(_ said: String?) { toast = said }
}
