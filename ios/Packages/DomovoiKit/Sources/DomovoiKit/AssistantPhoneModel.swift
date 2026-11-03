import Foundation
import Observation

/// The phone as a Chat place: its conversations, its searches, its four
/// writes, and the conversation itself with the reply streaming into it.
///
/// Android's twin is `AssistantController.kt` (4,457 lines) **as
/// `PhoneChatPlace.kt` uses it** — this is milestone 1's slice of it: the
/// history list, the archived view, the search, rename/archive/delete/clear
/// all, sharing one chat as text, and a typed turn with a stop. The voice
/// loop, the model setup and download, the phone's facts and actions, the
/// documents, drafts, letters, lookups, photos and reply reports are M2 to M6
/// (`ANDROID-ASSISTANT-MAP.md` §4) and nothing here pretends they exist.
///
/// ### What this does not do, and why there is no control for it
///
///  - **No model setup, size, licence or backend chip.** Apple's model is the
///    system's: there is nothing to download, nothing to size and no CPU/GPU
///    choice, so a "download" button would have nothing to fetch. M4 brings
///    all of it back with a model file of our own.
///  - **No mic.** M2 is Apple's `Speech`; until then no microphone control is
///    drawn, which is also why this milestone adds no
///    `NSMicrophoneUsageDescription`.
///  - **No timings line.** Apple's session reports no token counts.
///    ``AssistantMessage/tokensPerS`` and ``AssistantMessage/backend`` decode
///    and nothing fills them, rather than a number being invented.
///  - **No pin.** ``assistantPinKey(place:id:server:)`` says why: the shared
///    pin book keys on the house's address and this milestone may not edit it.
///    ``actions(for:)`` leaves the word out of the menu.
///
/// ### Why every one of these is the model's decision and not a view's
///
/// Which of four emptinesses is on screen; whether a stopped reply keeps its
/// mark; whether a context overflow is a failure or a divider; whether a reply
/// that arrived after the person walked away lands in the conversation that
/// asked it. ``ChatModel`` says this of itself and the rule is the same here:
/// a view cannot be asked whether it got them right.
///
/// ### The key is taken before the `await`
///
/// [[read-after-await-wrong-key]] is the top defect class on this track, and
/// here the key is the **conversation**: a reply takes seconds, and in those
/// seconds the person can open another chat, start a new one, or go back to
/// the list. ``visits`` is bumped by every one of those, and every task takes
/// it *and* the conversation's id before its first `await` and asks
/// ``stillShowing(_:_:)`` after it. A task that lost the conversation still
/// **saves** its reply — the conversation that asked is the one it belongs to
/// — and touches nothing on screen. That is the half a cancel-only guard gets
/// wrong: cancelling the task would lose the answer the person asked for and
/// will come back to.
@MainActor
@Observable
public final class AssistantPhoneModel {

    private let engine: any AssistantEngine
    private let store: AssistantConversationStore
    private let nowMs: @Sendable () -> Int64
    private let stamp: @Sendable (Int64) -> String
    private let wait: @Sendable (Int64) async throws -> Void
    private let lastPlaceStore: any SavedState

    /// How many chats this app has used in this place, as a saved key — the
    /// shell's own blob is not touched (``ChatPinBook`` gives the argument at
    /// length: a second key under the same prefix, never a field in somebody
    /// else's blob).
    public static let lastPlaceKey = "domovoi.assistant.lastPlace"

    // MARK: - the list

    public private(set) var summaries: [AssistantConversationSummary] = []

    /// The list has been read once (`ChatPlace.loaded`, `ChatPlace.kt:258`):
    /// until then the list draws nothing rather than "no chats yet".
    public private(set) var loaded = false

    public private(set) var filter = ""
    public private(set) var hits: [ChatRow]?
    public private(set) var showArchived = false

    // MARK: - the conversation

    /// The conversation open, whole. Nil for the list.
    public private(set) var conversation: AssistantConversation?

    /// A reply is being written.
    public private(set) var sending = false

    /// The words of the reply so far, which is **not** in ``conversation``
    /// until it finishes: a message is saved once, with its mark on it.
    public private(set) var live: String?

    public private(set) var toast: String?
    public private(set) var renaming: ChatRow?
    public private(set) var deleting: ChatRow?
    public private(set) var confirmingClearAll = false
    public private(set) var sharing: ChatShare?

    /// Bumped by every opening, every new chat and every walk back to the
    /// list. See this type's note on the key.
    private var visits = 0
    private var reply: Task<Void, Never>?
    private var debounce: Task<Void, Never>?
    private var stopped = false

    public init(
        engine: any AssistantEngine,
        store: AssistantConversationStore = AssistantConversationStore(),
        lastPlace: any SavedState = UserDefaultsSavedState(key: AssistantPhoneModel.lastPlaceKey),
        nowMs: @Sendable @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        stamp: @Sendable @escaping (Int64) -> String = { AssistantPhoneModel.shareStamp($0) },
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        }
    ) {
        self.engine = engine
        self.store = store
        self.lastPlaceStore = lastPlace
        self.nowMs = nowMs
        self.stamp = stamp
        self.wait = wait
    }

    /// Whether this phone can answer at all (``AssistantSupport``). The one
    /// input Chat reads to decide whether any of this is drawn.
    public var availability: AssistantAvailability { engine.availability }

    public var canChat: Bool { AssistantSupport.phoneCanChat(availability) }

    /// `ChatPlace.ready` (`ChatPlace.kt:255`).
    public var ready: Bool? { AssistantSupport.phoneReady(availability) }

    // MARK: - reading the list

    /// Reads the conversations off the phone. Cheap (a directory of small
    /// files) and synchronous, which is why there is no `ApiRead` here: there
    /// is no server, no cursor and nothing to go stale.
    public func load() {
        summaries = store.list()
        loaded = true
    }

    /// The main list: newest first, archived left out.
    ///
    /// **Pinned-less**, which is the one way this list is not
    /// ``ChatModel/rows``: see this type's heading.
    public var rows: [ChatRow] {
        summaries.filter { !$0.archived }
            .sorted { order($0, $1) }
            .map { $0.row() }
    }

    public var archivedRows: [ChatRow] {
        summaries.filter(\.archived)
            .sorted { order($0, $1) }
            .map { $0.row() }
    }

    public var archivedCount: Int { summaries.filter(\.archived).count }

    private func order(_ a: AssistantConversationSummary, _ b: AssistantConversationSummary) -> Bool {
        if a.updated != b.updated { return a.updated > b.updated }
        return a.id > b.id
    }

    /// A row's age in words (``ChatModel/timeText(_:)``, over this place's own
    /// clock).
    ///
    /// The clock is the model's — `nowMs` is injected — so a test can place a
    /// chat an hour ago and read the words back. A view calling `Date()` could
    /// not be moved at all, which is the reason ``ChatModel`` holds the same
    /// function rather than letting its rows format themselves.
    public func timeText(_ row: ChatRow) -> String {
        relTimeMs(row.updatedMs == 0 ? nil : row.updatedMs, nowMs: nowMs())
    }

    public var searching: Bool { !filter.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    /// What the list draws, as one value (`ChatHome.kt:421`), in
    /// ``ChatListContent`` — Chat's own type, because it is the same list.
    public var content: ChatListContent {
        if !loaded { return .waiting }
        if searching && hits == nil { return .waiting }
        let shown = searching ? (hits ?? []) : (showArchived ? archivedRows : rows)
        guard shown.isEmpty else { return .rows(shown) }
        if searching { return .empty(ChatStrings.noChatsMatch, hint: nil) }
        if showArchived { return .empty(ChatStrings.noArchivedChats, hint: nil) }
        return .empty(ChatStrings.noChatsYet, hint: AssistantStrings.emptyHintPhone)
    }

    public func showArchived(_ show: Bool) {
        showArchived = show
    }

    /// The search field changed. Debounced, as Chat's is, and **the hits of
    /// the last search are kept** until the new ones land, so typing on does
    /// not blank a result that is still roughly right (`ChatHome.kt:304`).
    ///
    /// There is no `searchFailed` here and no sentence for one:
    /// ``ChatStrings/searchFailed`` is for a place that had to ask somebody
    /// else. This search is a directory of files on this phone — a file that
    /// will not decode is left out of `all()` and the search still answers.
    public func filter(_ text: String) {
        filter = text
        debounce?.cancel()
        if !searching {
            hits = nil
            return
        }
        let asked = text
        debounce = Task { [weak self] in
            guard let self else { return }
            try? await self.wait(250)
            guard !Task.isCancelled, self.filter == asked else { return }
            self.hits = self.store.search(asked).map { $0.summary.row(match: $0.snippet) }
        }
    }

    // MARK: - opening

    /// Opens the conversation `id` names. A conversation whose file has gone
    /// (deleted on another screen) leaves the list, with the toast a failed
    /// read gets everywhere else in this app.
    public func open(_ id: String) {
        visits += 1
        stopped = false
        live = nil
        guard let found = store.load(id) else {
            toast = AssistantStrings.writeFailed(ChatStrings.verbOpenChat)
            load()
            return
        }
        conversation = found
    }

    public func open(_ row: ChatRow) { open(row.id) }

    /// A new chat. **Not saved** until its first message, so a new chat left
    /// alone leaves nothing on the phone (`ConversationStore.kt:277`).
    public func newChat() {
        visits += 1
        stopped = false
        live = nil
        conversation = store.create(now: nowMs())
    }

    /// Back to the list.
    public func close() {
        visits += 1
        conversation = nil
        live = nil
        load()
    }

    /// The transcript on screen: what is saved, plus the reply being written.
    public var transcript: [AssistantMessage] {
        var out = conversation?.messages ?? []
        if let live {
            out.append(AssistantMessage(from: .domovoi, text: live, at: nowMs()))
        }
        return out
    }

    /// Where the window divider goes, as an index into ``transcript``; nil for
    /// no divider (`windowDividerIndex`, `ChatHome.kt:671`).
    ///
    /// Zero is **not** a divider: the model saw the whole chat from its first
    /// message, and a line above the first message would say that everything
    /// is beyond what Domovoi can keep in mind.
    public var windowDividerIndex: Int? {
        guard let from = conversation?.windowFrom, from > 0,
              from < (conversation?.messages.count ?? 0) else { return nil }
        return from
    }

    /// `ChatStrings.windowNote`, which was ported with Chat and has its first
    /// reader here.
    public var windowNote: String { ChatStrings.windowNote }

    public var isEmptyConversation: Bool { transcript.isEmpty }

    // MARK: - sending

    /// Sends `text` and starts the reply.
    ///
    /// The person's message is **saved before the model is asked**: a question
    /// typed on a phone that then fails to answer is still the person's
    /// question, and it is what "try again" re-asks.
    public func send(_ text: String) {
        let asked = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !asked.isEmpty, !sending, var open = conversation else { return }
        let at = nowMs()
        open.messages.append(AssistantMessage(from: .user, text: asked, at: at))
        open.updated = at
        if open.created == 0 { open.created = at }
        conversation = open
        store.save(open)
        load()
        run(on: open.id, history: turns(of: open.messages), rebuildingWindow: false)
    }

    /// "try again" (`ChatHome.kt:686`): the last reply is dropped and the
    /// question before it is asked again.
    ///
    /// Offered only on a finished reply at the end of a chat, which is
    /// ``canTryAgain``'s three clauses — the same shape as
    /// ``ChatRules/canRetry(_:)``, over this place's own messages.
    public func tryAgain() {
        guard canTryAgain, var open = conversation else { return }
        open.messages.removeLast()
        open.updated = nowMs()
        conversation = open
        store.save(open)
        load()
        run(on: open.id, history: turns(of: open.messages), rebuildingWindow: false)
    }

    /// `canRetry` (`ChatScreen.kt:1302`) for this place: a finished reply, at
    /// the end, with a question before it.
    public var canTryAgain: Bool {
        guard !sending, let messages = conversation?.messages, messages.count >= 2,
              let last = messages.last else { return false }
        return !last.fromUser && messages[messages.count - 2].fromUser
    }

    /// Stops the reply being written. The words so far are kept, with the
    /// `stopped` mark on them (`AssistantChat.kt:595`) — which is the
    /// difference between a stop and a failure, and the thing a case has to be
    /// able to see.
    public func stop() {
        guard sending else { return }
        stopped = true
        engine.cancel()
        reply?.cancel()
    }

    private func turns(of messages: [AssistantMessage]) -> [AssistantTurn] {
        messages
            .filter { $0.error == nil && !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
            .map { AssistantTurn(fromUser: $0.fromUser, text: $0.text) }
    }

    /// One turn, end to end. `rebuildingWindow` is the second attempt after a
    /// context overflow, and it is what stops an overflow loop: the shorter
    /// history is tried **once**.
    private func run(on id: String, history: [AssistantTurn], rebuildingWindow: Bool) {
        // Taken BEFORE the await, both of them — the conversation this reply
        // belongs to, and the visit it was asked in.
        let epoch = visits
        sending = true
        live = ""
        stopped = false
        reply = Task { [weak self] in
            guard let self else { return }
            var text = ""
            var firstTextMs: Int64?
            let startedMs = self.nowMs()
            var failure: AssistantEngineError?
            do {
                for try await chunk in self.engine.reply(
                    system: AssistantPersona.systemPrompt,
                    history: history
                ) {
                    switch chunk {
                    case .append(let more): text += more
                    case .replace(let whole): text = whole
                    }
                    if firstTextMs == nil, !text.isEmpty { firstTextMs = self.nowMs() - startedMs }
                    // The only read-after-await that touches the screen, and
                    // it is guarded: a reply for a conversation that is no
                    // longer open writes nothing here.
                    if self.stillShowing(id, epoch) { self.live = text }
                }
            } catch let error as AssistantEngineError {
                failure = error
            } catch {
                failure = .failed((error as NSError).localizedDescription)
            }

            if failure == .contextWindowExceeded, !rebuildingWindow {
                self.rebuildWindow(on: id, epoch: epoch)
                return
            }
            self.finish(
                on: id,
                epoch: epoch,
                text: text,
                firstTextMs: firstTextMs,
                failure: failure
            )
        }
    }

    /// Whether the conversation `id` is still the one on screen, in the same
    /// visit.
    ///
    /// Both halves are needed: the id alone would let a reply land in the
    /// *second* opening of the same chat (where a rename or a delete may have
    /// happened in between), and the epoch alone would let one chat's reply
    /// land in another opened at the same count.
    private func stillShowing(_ id: String, _ epoch: Int) -> Bool {
        epoch == visits && conversation?.id == id
    }

    /// The model forgot the beginning: write down where its view starts now,
    /// and ask once more with the shorter history.
    ///
    /// Where it starts is **the last question** — the one turn the model is
    /// certainly able to read. Android's engine trims inside LiteRT and
    /// reports `windowFrom`; Apple's session only throws, so the app decides,
    /// and the honest answer is the smallest one that cannot overflow again.
    /// The divider then sits above that question, which is exactly what
    /// ``ChatStrings/windowNote`` says.
    private func rebuildWindow(on id: String, epoch: Int) {
        guard var open = store.load(id) else {
            finish(on: id, epoch: epoch, text: "", firstTextMs: nil, failure: .contextWindowExceeded)
            return
        }
        let lastQuestion = open.messages.lastIndex(where: \.fromUser) ?? 0
        open.windowFrom = lastQuestion
        store.save(open)
        if stillShowing(id, epoch) { conversation = open }
        run(
            on: id,
            history: turns(of: Array(open.messages[lastQuestion...])),
            rebuildingWindow: true
        )
    }

    /// Saves the reply and puts the screen back where it belongs.
    ///
    /// The conversation is read **from the store** rather than from
    /// ``conversation``, and the saved meta is merged back with
    /// ``AssistantConversation/withMetaOf(_:)``: a rename or an archive that
    /// happened while the model was writing has to survive the reply, and a
    /// reply for a chat the person walked away from still has to be in it when
    /// they come back.
    private func finish(
        on id: String,
        epoch: Int,
        text: String,
        firstTextMs: Int64?,
        failure: AssistantEngineError?
    ) {
        let wasStopped = stopped && stillShowing(id, epoch)
        var message = AssistantMessage(
            from: .domovoi,
            text: text,
            at: nowMs(),
            stopped: wasStopped,
            firstTextMs: firstTextMs
        )
        switch failure {
        case .none:
            break
        case .contextWindowExceeded:
            // Reached only when the rebuilt attempt overflowed too, which
            // means one question on its own does not fit.
            message.error = AssistantStrings.modelCouldNotStart(
                "this question is longer than the model can read"
            )
        case .refused:
            message.error = AssistantStrings.refused
        case .unavailable(let reason):
            message.error = AssistantStrings.modelCouldNotStart(AssistantStrings.unavailableReason(reason))
        case .failed(let why):
            message.error = wasStopped ? nil : AssistantStrings.modelStopped(why)
        }
        // A reply with no words, no error and no stop is the runtime ending a
        // reply nobody asked it to (`AssistantController.kt:1630`).
        if message.text.isEmpty && message.error == nil && !wasStopped {
            message.error = AssistantStrings.cutOff
        }

        if var saved = store.load(id) {
            saved.messages.append(message)
            saved.updated = message.at
            store.save(saved)
            if stillShowing(id, epoch) { conversation = saved }
        }
        // `sending` and `live` are this screen's, not the conversation's: a
        // reply that landed in a chat the person has left still has to let the
        // composer go, and the list still has to show the new time.
        sending = false
        live = nil
        stopped = false
        load()
        Task { [weak self] in await self?.titleIfNeeded(id) }
    }

    // MARK: - the title job

    /// Asks the model for a title, once the first exchange is there
    /// (`ChatTitles.kt`, and `needsTitle`).
    ///
    /// Every outcome is written down: a title that cleans to nothing, or an
    /// engine that will not answer, counts a try
    /// (``AssistantPersona/titleMaxTries``) so the job does not run for ever
    /// on a chat it cannot name. The first five words stay the title either
    /// way, which is why nothing on screen says this failed.
    public func titleIfNeeded(_ id: String) async {
        guard var open = store.load(id), open.needsTitle, let exchange = open.firstExchange() else { return }
        let epoch = visits
        let written = try? await engine.title(
            system: AssistantPersona.titleSystem,
            prompt: AssistantPersona.titlePrompt(question: exchange.question, answer: exchange.answer),
            maxTokens: AssistantPersona.titleMaxTokens
        )
        // Read after the await: the conversation is taken from the store
        // again, because a reply (or a rename) may have landed in it while the
        // title was being written.
        guard var now = store.load(id) else { return }
        if let title = assistantCleanTitle(written ?? nil), !title.isEmpty {
            now.name = title
            now.titled = true
        } else {
            now.titleTries += 1
        }
        open = now
        store.save(open)
        if stillShowing(id, epoch) { conversation = open }
        load()
    }

    // MARK: - the row menu

    /// The menu, in order, **less `pin`**: see
    /// ``assistantPinKey(place:id:server:)`` for why the control is not drawn.
    public func actions(for row: ChatRow) -> [String] {
        assistantRowActions(pinned: false, archived: row.archived)
            .filter { $0 != ChatStrings.actionPin && $0 != ChatStrings.actionUnpin }
    }

    /// The toast has been said, or is being replaced.
    ///
    /// The toast itself is the **shell's** — it is drawn over the whole app
    /// because a failed delete has to outlive this screen — so the view hands
    /// it over and clears it here, exactly as ``ChatModel/setToast(_:)`` is
    /// used. Without a clearer the same sentence could not be said twice: a
    /// view watching ``toast`` for a change would see none.
    public func setToast(_ text: String?) { toast = text }

    public func renameRequested(_ row: ChatRow) { renaming = row }
    public func deleteRequested(_ row: ChatRow) { deleting = row }
    public func clearAllRequested() { confirmingClearAll = true }
    public func dismissSheets() {
        renaming = nil
        deleting = nil
        confirmingClearAll = false
        sharing = nil
    }

    /// The confirmation for deleting a phone chat — the one sentence that says
    /// the chat is not the household's (`PhoneChatPlace.kt:63`).
    public func deleteText(_ row: ChatRow) -> String { AssistantStrings.deleteText(row.title) }

    /// A name the person typed. Flattened and cut as the Kotlin does
    /// (`ChatTitles.plainTitle`, and `ChatHome.kt:562`'s `take(200)`); a name
    /// that is nothing but spaces takes the chat back to its first words.
    public func rename(_ row: ChatRow, to title: String) {
        renaming = nil
        guard var open = store.load(row.id) else {
            toast = AssistantStrings.writeFailed(ChatStrings.verbRename)
            return
        }
        let name = String(assistantPlainTitle(title).prefix(ChatStrings.renameMaxChars))
        open.name = name.isEmpty ? nil : name
        open.titleByUser = !name.isEmpty
        guard store.save(open) else {
            toast = AssistantStrings.writeFailed(ChatStrings.verbRename)
            return
        }
        if conversation?.id == open.id { conversation = conversation?.withMetaOf(open) }
        load()
    }

    public func setArchived(_ row: ChatRow, _ archived: Bool) {
        guard var open = store.load(row.id) else {
            toast = AssistantStrings.writeFailed(archived ? ChatStrings.verbArchive : ChatStrings.verbMoveBack)
            return
        }
        open.archived = archived
        guard store.save(open) else {
            toast = AssistantStrings.writeFailed(archived ? ChatStrings.verbArchive : ChatStrings.verbMoveBack)
            return
        }
        if conversation?.id == open.id { conversation = conversation?.withMetaOf(open) }
        toast = archived ? ChatStrings.archivedToast : ChatStrings.movedBackToast
        load()
    }

    public func delete(_ row: ChatRow) {
        deleting = nil
        guard store.delete(row.id) else {
            toast = AssistantStrings.writeFailed(ChatStrings.verbDelete)
            return
        }
        if conversation?.id == row.id { close() } else { load() }
    }

    /// Every conversation on this phone (`AssistantChat.kt:830`).
    public func clearAll() {
        confirmingClearAll = false
        let gone = store.clearAll()
        if !gone { toast = AssistantStrings.writeFailed(ChatStrings.verbDelete) }
        conversation = nil
        live = nil
        visits += 1
        load()
    }

    /// How many chats are kept, and where (`conversationsSummary`,
    /// `AssistantChat.kt:857`), as this milestone can honestly say it.
    public var privacyLine: String { AssistantStrings.privacyLine(summaries.count) }

    // MARK: - sharing one chat as text

    /// One chat as text for the share sheet (`ChatExport.text`,
    /// `ChatExport.kt:55`). Nothing here sends anything: the person's own tap
    /// on the share sheet is the only way out of the phone.
    public func share(_ row: ChatRow) {
        guard let open = store.load(row.id) else {
            toast = AssistantStrings.writeFailed(ChatStrings.verbOpenChat)
            return
        }
        sharing = ChatShare(title: open.title, text: transcriptText(open))
    }

    /// The text of one conversation. Public because sharing is the one write
    /// in this place a test can check without a share sheet.
    public func transcriptText(_ conversation: AssistantConversation) -> String {
        var out = "\(conversation.title)\n"
        let at = conversation.created > 0 ? conversation.created : conversation.updated
        out += "\(AssistantStrings.shareHeader)\(stamp(at))\n"
        for message in conversation.messages {
            // `ChatExport.words` (`ChatExport.kt:45`): the failure and the
            // stop are in the text, in brackets, so a chat read elsewhere
            // still says which replies were not finished.
            var words = message.text.trimmingCharacters(in: .whitespacesAndNewlines)
            if let error = message.error {
                if !words.isEmpty { words += "\n" }
                words += "[\(error)]"
            }
            if message.stopped {
                if !words.isEmpty { words += " " }
                words += "[stopped]"
            }
            let who = message.fromUser ? ChatStrings.shareYou : ChatStrings.shareDomovoi
            out += "\n\(who): \(words)\n"
        }
        return out
    }

    /// `yyyy-MM-dd HH:mm`, as the Kotlin's export stamp is
    /// (`ChatExport.kt:35`).
    nonisolated public static func shareStamp(_ ms: Int64) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HH:mm"
        return formatter.string(from: Date(timeIntervalSince1970: Double(ms) / 1000))
    }

    // MARK: - which place a new chat starts in

    /// The place this phone used last, for
    /// ``assistantDefaultPlace(choices:lastUsed:serverHasModel:)``.
    public var lastUsedPlace: ChatPlace? {
        guard let blob = lastPlaceStore.read(), let key = String(data: blob, encoding: .utf8) else { return nil }
        return ChatPlace.of(key)
    }

    /// A message went out in a place: it becomes the last used
    /// (`ChatFrame.onSent`, `ChatPlace.kt:243`).
    public func sentIn(_ place: ChatPlace) {
        lastPlaceStore.write(Data(place.key.utf8))
    }

    /// Everything in flight, dropped — the app went to the background, or Chat
    /// left the screen.
    public func stopEverything() {
        debounce?.cancel()
        reply?.cancel()
        engine.cancel()
        sending = false
        live = nil
    }
}
