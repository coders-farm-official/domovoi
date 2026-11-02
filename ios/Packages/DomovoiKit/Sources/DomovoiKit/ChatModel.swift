import Foundation
import Observation

/// The Chat screen: the household's threads, the archived view, the search, the
/// three writes a thread takes, and the conversation with its live reply.
///
/// `ChatHome.kt` plus `ConversationPane` (`ChatScreen.kt:422`), minus
/// everything about the phone as a place. It is a model here for the reason
/// every one of these has been: which of four emptinesses is on screen, whether
/// a search's archived hits survive the sort, whether a composer is offered,
/// what a failed retry leaves behind and whether a thread exists on the server
/// yet are all **decisions**, and a view cannot be asked whether it got them
/// right.
///
/// ### What this does not do, and why there is no control for it
///
/// **Pictures, voice, documents, drafts, letters, long-document jobs, lookups,
/// report-a-message and heads-up picks.** Each is a milestone away rather than
/// a decision to skip, and `ANDROID-CHAT-MAP.md` says which milestone. The
/// fields for them decode (``ChatMessage/hasDocuments``) and nothing draws
/// them: a control that cannot do what it says is not drawn, and the other half
/// of that rule is that the omission is written down where the code is.
///
/// ### The list is one read, not a pager
///
/// `GET /threads` answers with every matching thread in one body — no `limit`,
/// no `offset`, no cursor (`chat.py:219`) — so this is ``ApiRead`` and not
/// ``Pager``. That the route hands a whole house's threads over at once is a
/// real scaling gap; it is the server's, and inventing client paging for a
/// route that has none would only hide it.
@MainActor
@Observable
public final class ChatModel {

    /// `chat.changed`: a thread appeared, was renamed, archived or deleted —
    /// on this phone or on any other device in the house
    /// (`ServerChatPlace.kt:65`).
    public static let listEvents = ChatRules.listEvents

    private let container: AppContainer
    private let deviceID: String?
    private let book: ChatPinBook
    private let wait: @Sendable (Int64) async throws -> Void
    private let nowMs: @Sendable () -> Int64
    private let nowISO: @Sendable () -> String

    /// The not-archived threads, and the archived ones, as two reads — which is
    /// what the route gives: `?archived=` picks one view or the other, so there
    /// is no single answer holding both (`ServerChatPlace.kt:65-70`).
    public let threads: ApiRead<ChatThreadList>
    public let archivedThreads: ApiRead<ChatThreadList>

    /// What the house has installed. Read for ``visionRefusal`` alone.
    public let models: ApiRead<ChatModelsInfo>

    /// Whether the house allows chat at all (`features.ai`).
    ///
    /// **Absent means on**, which is why the default is `true`: an older server
    /// has no switch, and a manifest that failed to load must not say Chat is
    /// turned off on a house that has its models (``Capabilities/aiEnabled``).
    public private(set) var aiEnabled = true

    /// What is typed in the search field — which is also what makes the list a
    /// search, from the first keystroke rather than from the answer.
    ///
    /// Android's `query` is the field itself and its debounce is inside the
    /// effect (`ChatHome.kt:301`), so `searching` there is true while the wait
    /// is still running and the list draws nothing at all. This is the same
    /// field under the same rule; what it must **not** be is the answered
    /// search, which would leave the whole list on screen for a quarter of a
    /// second after the first keystroke and then swap it.
    public private(set) var filter = ""

    /// The hits, or nil while a search has not answered yet — which the list
    /// draws as nothing at all rather than as "no chats match".
    ///
    /// A **second** search keeps the first one's hits until its own land
    /// (`ChatHome.kt:304` clears them only for an empty field), so typing on
    /// does not blank a result that is still roughly right.
    public private(set) var hits: [ChatRow]?

    /// The search could not run: the list says so instead of saying nothing
    /// matched (`ChatPlace.kt:146`).
    public private(set) var searchFailed = false

    public private(set) var showArchived = false

    /// The thread open in the conversation, or nil for the list.
    public private(set) var openThread: Int64?

    /// A new chat is open and **has no thread on the server yet**. The thread
    /// is made by the first message that needs one, so a new chat left empty
    /// leaves nothing in the household's list.
    public private(set) var newChat = false

    /// The title to show for ``newChat`` and for a thread the list does not
    /// know yet.
    public private(set) var openTitle = ChatStrings.newChat

    public private(set) var transcript = ChatTranscript()

    /// A reply is being written, or a send is on its way out.
    public private(set) var sending = false

    public private(set) var toast: String?

    /// The row a rename dialog or a delete confirmation is open for.
    public private(set) var renaming: ChatRow?
    public private(set) var deleting: ChatRow?

    /// A chat as text, waiting for the share sheet (title, text).
    public private(set) var sharing: ChatShare?

    private var debounce: Task<Void, Never>?
    private var stream: Task<Void, Never>?
    private var pinKeys: Set<String>

    public init(
        in container: AppContainer,
        deviceID: String?,
        pins: ChatPinBook = ChatPinBook(),
        wait: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        },
        nowMs: @Sendable @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        nowISO: @Sendable @escaping () -> String = { ISO8601DateFormatter().string(from: Date()) }
    ) {
        self.container = container
        self.deviceID = deviceID
        self.book = pins
        self.wait = wait
        self.nowMs = nowMs
        self.nowISO = nowISO
        self.pinKeys = pins.all

        let client = container.client
        self.threads = ApiRead(in: container, eventTypes: Self.listEvents) {
            try await client.get(ChatPath.threads(), as: ChatThreadList.self)
        }
        self.archivedThreads = ApiRead(in: container, eventTypes: Self.listEvents) {
            try await client.get(ChatPath.threads(archived: true), as: ChatThreadList.self)
        }
        // No `eventTypes`: what Ollama has installed does not change because a
        // chat did, and `chat.changed` arrives on every message.
        self.models = ApiRead(in: container) {
            try await client.get(ChatPath.models, as: ChatModelsInfo.self)
        }
    }

    // MARK: - the house, and this phone's own address for it

    /// The normalised spelling of the server, which is half of a pin's key
    /// (`ChatScreen.kt:151`).
    public var server: String { ServerCredentials.normalize(container.store.serverURL) }

    /// The server said whether its AI features are on.
    public func capabilitiesChanged(_ capabilities: Capabilities) {
        aiEnabled = capabilities.aiEnabled
    }

    // MARK: - the list

    /// The main list: pinned first, then newest first, archived left out.
    public var rows: [ChatRow] {
        ChatRules.orderChats(withPins(ChatRules.rows(of: threads.data ?? ChatThreadList())))
    }

    /// The archived view, newest first.
    public var archivedRows: [ChatRow] {
        ChatRules.archivedChats(withPins(ChatRules.rows(of: archivedThreads.data ?? ChatThreadList())))
    }

    /// How many archived chats there are, for the row at the end of the list.
    public var archivedCount: Int { archivedRows.count }

    private func withPins(_ rows: [ChatRow]) -> [ChatRow] {
        ChatRules.withPins(rows, pins: pinKeys, server: server)
    }

    /// Whether a search is what is on screen (`ChatHome.kt:355`).
    public var searching: Bool { !filter.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    /// **What the list draws**, as one decision rather than three `if`s in a
    /// view (`ChatHome.kt:421`).
    public var content: ChatListContent {
        if searching && hits == nil { return .waiting }
        let shown = searching ? (hits ?? []) : (showArchived ? archivedRows : rows)
        guard shown.isEmpty else { return .rows(shown) }
        if searching { return .empty(searchFailed ? ChatStrings.searchFailed : ChatStrings.noChatsMatch, hint: nil) }
        if showArchived { return .empty(ChatStrings.noArchivedChats, hint: nil) }
        return .empty(ChatStrings.noChatsYet, hint: ChatStrings.emptyHint)
    }

    /// The line above a partial result: some place could not be searched.
    ///
    /// Android says "everywhere" because it searches two places
    /// (`ChatHome.kt:416`). There is one here, so a failed search with hits in
    /// it cannot happen — the read either answered or it did not — and this is
    /// nil for that reason rather than by omission.
    public var searchWarning: String? { nil }

    public func showArchived(_ show: Bool) {
        showArchived = show
        if show { archivedThreads.refresh() }
    }

    // MARK: - searching

    /// A keystroke in the search field. The search itself runs after
    /// ``ChatRules/searchDebounceMs``.
    public func typed(_ text: String) {
        filter = text
        debounce?.cancel()
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            hits = nil
            searchFailed = false
            return
        }
        debounce = Task { @MainActor [weak self] in
            guard let self else { return }
            try? await self.wait(ChatRules.searchDebounceMs)
            guard !Task.isCancelled else { return }
            await self.search(trimmed)
        }
    }

    private func search(_ trimmed: String) async {
        let client = container.client
        let path = ChatRules.threadSearchPath(query: trimmed)
        do {
            let found = try await client.get(path, as: ChatThreadList.self)
            // **The hits are sorted by date and nothing else.** `?q=` with no
            // `archived` searches the archived threads as well
            // (`chat.py:235`), so running them through `orderChats` would
            // silently drop every archived match. `ChatHome.kt:320` does the
            // same, deliberately.
            guard !Task.isCancelled else { return }
            hits = ChatRules.searchHits(withPins(ChatRules.rows(of: found)))
            searchFailed = false
        } catch {
            guard !Task.isCancelled else { return }
            hits = []
            searchFailed = true
        }
    }

    // MARK: - pins, which are this phone's

    public func isPinned(_ row: ChatRow) -> Bool {
        pinKeys.contains(ChatRules.pinKey(row, server: server))
    }

    public func setPinned(_ row: ChatRow, _ pinned: Bool) {
        pinKeys = book.set(ChatRules.pinKey(row, server: server), pinned: pinned)
        // The hits were built with the old set: a pin is a tap, and a list that
        // did not move under it would look broken.
        if let current = hits { hits = ChatRules.searchHits(withPins(current)) }
    }

    /// What a row's menu offers, with the pin state this phone has.
    public func actions(for row: ChatRow) -> [String] {
        ChatRules.rowActions(row.pinned(isPinned(row)))
    }

    // MARK: - the three writes

    public func renameRequested(_ row: ChatRow) { renaming = row }
    public func cancelRename() { renaming = nil }

    /// `PATCH /threads/{id}` with a title. A blank name is not sent: the server
    /// refuses it with a 400 (`chat.py:324`) and Android does not ask
    /// (`ServerChatPlace.kt:114`).
    public func rename(_ row: ChatRow, to title: String) {
        renaming = nil
        let name = String(title.trimmingCharacters(in: .whitespacesAndNewlines)
            .prefix(ChatStrings.renameMaxChars))
        guard !name.isEmpty, let id = row.threadID else { return }
        write(verb: ChatStrings.verbRename, method: "PATCH", path: ChatPath.thread(id),
              body: ChatPath.patchBody(title: name))
        if openThread == id { openTitle = name }
    }

    /// `PATCH /threads/{id}` with `archived`. The server's, so it shows on
    /// every paired device.
    public func setArchived(_ row: ChatRow, _ archived: Bool) {
        guard let id = row.threadID else { return }
        write(
            verb: archived ? ChatStrings.verbArchive : ChatStrings.verbMoveBack,
            method: "PATCH",
            path: ChatPath.thread(id),
            body: ChatPath.patchBody(archived: archived),
            said: archived ? ChatStrings.archivedToast : ChatStrings.movedBackToast
        )
    }

    public func deleteRequested(_ row: ChatRow) { deleting = row }
    public func cancelDelete() { deleting = nil }

    /// What the delete confirmation says (`ServerChatPlace.kt:102`). The thread
    /// is the household's, and this is the one place that fact is said.
    public func deleteText(_ row: ChatRow) -> String { ChatStrings.deleteText(row.title) }

    /// `DELETE /threads/{id}` — **for the whole house**.
    ///
    /// The pin goes with it (`ChatHome.kt:477`): a key for a thread that no
    /// longer exists would sit in this phone's saved set for ever, and if the
    /// house ever reused the id it would pin a stranger's chat.
    public func delete(_ row: ChatRow) {
        deleting = nil
        guard let id = row.threadID else { return }
        setPinned(row, false)
        if openThread == id { closeChat() }
        write(verb: ChatStrings.verbDelete, method: "DELETE", path: ChatPath.thread(id), body: nil)
    }

    /// One write, its refusal worded, and both lists read again afterwards.
    ///
    /// Both: an archive moves a thread from one view to the other, so reading
    /// only the one the row came from leaves the other stale (Android's
    /// `refresh()` does the same, `ServerChatPlace.kt:95`).
    private func write(
        verb: String,
        method: String,
        path: String,
        body: Data?,
        said: String? = nil
    ) {
        let client = container.client
        Task { @MainActor [weak self] in
            do {
                try await client.send(method: method, path: path, body: body)
                self?.toast = said
            } catch let trouble as DomovoiError {
                self?.toast = isAiOffRefusal(trouble) ? aiChatOffText : failureText(verb, trouble)
            } catch {
                self?.toast = failureText(verb, .unreachable("\(error)"))
            }
            self?.refreshLists()
        }
    }

    private func refreshLists() {
        threads.refresh()
        archivedThreads.refresh()
    }

    // MARK: - sharing a chat

    /// Read the thread and hand its text to the share sheet
    /// (`ServerChatPlace.kt:136`). A thread that cannot be read shares nothing
    /// rather than an empty sheet, exactly as Android's `getOrNull()` does.
    public func share(_ row: ChatRow) {
        guard let id = row.threadID else { return }
        let client = container.client
        let title = row.title
        Task { @MainActor [weak self] in
            guard let list = try? await client.get(ChatPath.messages(id), as: ChatMessageList.self)
            else { return }
            self?.sharing = ChatShare(
                title: title,
                text: ChatRules.shareText(title: title, messages: list.messages)
            )
        }
    }

    public func shared() { sharing = nil }

    // MARK: - the conversation

    /// Open `row`'s thread and read its messages.
    public func open(_ row: ChatRow) {
        guard let id = row.threadID else { return }
        open(thread: id, title: row.title)
    }

    /// Open a thread by id — what a help notification's tap asks for
    /// (``PendingChatThread``).
    public func open(thread id: Int64, title: String) {
        cancelStream()
        openThread = id
        newChat = false
        openTitle = title
        transcript.clear()
        loadMessages(id)
    }

    /// A new chat. **Nothing is made on the server** until the first message
    /// needs a thread (`ChatScreen.kt:415`), so a new chat opened and left
    /// leaves no empty thread in the household's list.
    public func startNewChat() {
        cancelStream()
        openThread = nil
        newChat = true
        openTitle = ChatStrings.newChat
        transcript.clear()
    }

    public func closeChat() {
        cancelStream()
        openThread = nil
        newChat = false
        transcript.clear()
        refreshLists()
    }

    /// Whether the conversation is on screen at all.
    public var conversationOpen: Bool { openThread != nil || newChat }

    /// Whether the composer is offered.
    ///
    /// **Not while the house's AI is off**: the screen says so and offers no
    /// composer, because a message box that cannot send is the thing
    /// [[no-misleading-controls]] is about. Not while a reply is being written
    /// either (`ChatScreen.kt:736`'s `sending` guard).
    public var composerOffered: Bool { aiEnabled && conversationOpen }

    public var canSend: Bool { composerOffered && !sending }

    /// What the screen says in place of the whole conversation when the house's
    /// AI features are off (`RouteNotes.kt:10`, already ported as
    /// ``ShellStrings/unavailableNote(label:isChat:aiEnabled:)``).
    public var aiOffNote: (title: String, detail: String)? {
        guard !aiEnabled else { return nil }
        return ShellStrings.unavailableNote(label: ChatStrings.title, isChat: true, aiEnabled: false)
    }

    /// Why a picture cannot be read here, when that is certain
    /// (``visionMissing(_:)``). Nil otherwise.
    ///
    /// There is no paperclip in this app, so nothing offers to send one. This
    /// is here for the reply that **arrives** refused — a picture sent from the
    /// dashboard or from Android — so the sentence under it is the server's
    /// reason rather than a code.
    public var visionRefusal: String? {
        guard visionMissing(models.data) else { return nil }
        return visionMissingText(models.data?.visionModel)
    }

    private func loadMessages(_ id: Int64) {
        let client = container.client
        let who = deviceID
        Task { @MainActor [weak self] in
            do {
                let list = try await client.get(ChatPath.messages(id), as: ChatMessageList.self)
                // **The thread that is open now**, not the one this read was
                // for: a slow read landing after the person opened another chat
                // would otherwise draw somebody else's conversation under this
                // title. That is [[read-after-await-wrong-key]] in its
                // navigation form, and the check is one `==`.
                guard let self, self.openThread == id else { return }
                self.transcript.load(list.messages, thisDeviceID: who)
            } catch let trouble as DomovoiError {
                guard let self, self.openThread == id else { return }
                self.toast = isAiOffRefusal(trouble)
                    ? aiChatOffText
                    : failureText(ChatStrings.verbSend, trouble)
            } catch {
                return
            }
        }
    }

    // MARK: - sending

    /// Send `text`. Makes the thread first when this is a new chat.
    ///
    /// Returns whether anything went out, as Android's `send` does
    /// (`ChatScreen.kt:734`): a blank message, a second send while one is
    /// running, and a house whose AI is off all send nothing.
    @discardableResult
    public func send(_ text: String) -> Bool {
        let content = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !content.isEmpty, canSend, conversationOpen else { return false }
        sending = true
        transcript.beginSend(content: content, atISO: nowISO())
        let client = container.client
        let who = deviceID
        stream = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let id = try await self.ensureThread()
                await self.run(
                    path: ChatPath.messages(id),
                    body: ChatPath.sendBody(content: content, deviceID: who),
                    verb: ChatStrings.verbSend
                )
            } catch let trouble as DomovoiError {
                // The thread could not even be made: the refusal goes where the
                // reply would have been, as Android's `onFailure` puts it
                // (`ChatScreen.kt:773`).
                self.transcript.failLive(sendFailureLine(trouble, verb: ChatStrings.verbNewChat))
                self.endStream()
            } catch {
                self.transcript.failLive(failureText(ChatStrings.verbNewChat, .unreachable("\(error)")))
                self.endStream()
            }
        }
        return true
    }

    /// "Try again": a new reply to the last question, in place of the one it
    /// got (`POST /threads/{id}/retry`).
    ///
    /// Gated on ``ChatTranscript/canRetry``, which is
    /// ``ChatRules/canRetry(_:)``. A failed retry writes nothing on the server,
    /// and the transcript puts the old reply back.
    @discardableResult
    public func retry() -> Bool {
        guard let id = openThread, canSend, transcript.beginRetry() else { return false }
        sending = true
        stream = Task { @MainActor [weak self] in
            await self?.run(
                path: ChatPath.retry(id),
                // No body. The route takes none (`chat.py:1069`), and Android
                // sends `{}` only because its builder always sends one.
                body: nil,
                verb: ChatStrings.verbTryAgain
            )
        }
        return true
    }

    /// One streamed round trip: open it, apply every event, then settle.
    private func run(path: String, body: Data?, verb: String) async {
        let client = container.client
        do {
            let events = try await client.events(method: "POST", path: path, body: body)
            for try await event in events {
                guard !Task.isCancelled else { break }
                react(to: transcript.apply(event))
            }
        } catch let trouble as DomovoiError {
            transcript.failLive(sendFailureLine(trouble, verb: verb))
        } catch {
            transcript.failLive(failureText(verb, .unreachable("\(error)")))
        }
        endStream(verb: verb)
    }

    private func react(to step: ChatStreamStep) {
        // Refused for want of the vision model: read what is installed again,
        // so the refusal's own sentence is right next time
        // (`ChatScreen.kt:776`).
        if case .failed(_, let code, _) = step, code == ChatErrors.visionModelMissing {
            models.refresh()
        }
    }

    private func endStream(verb: String = ChatStrings.verbSend) {
        switch transcript.finishStream(atISO: nowISO()) {
        case .keptOldReply(let why):
            // A retry that failed. The old reply is back — the thread on the
            // server never changed — and the failure is said once, as a toast,
            // rather than left attached to a reply that is not the one that
            // failed.
            toast = ChatStrings.couldNotTryAgain(why ?? ChatStrings.searchFailed)
        case .unfinished(let why):
            if verb == ChatStrings.verbTryAgain, let why {
                toast = ChatStrings.couldNotTryAgain(why)
            }
        case .finished, .nothingRunning:
            break
        }
        sending = false
        // The list's own row for this thread is now stale: its snippet, its
        // count and its `updated_at` all moved.
        refreshLists()
    }

    private func cancelStream() {
        stream?.cancel()
        stream = nil
        sending = false
    }

    /// The thread to write to: the one open, or the one this message makes
    /// (`ensureThread`, `ChatScreen.kt:438`).
    private func ensureThread() async throws(DomovoiError) -> Int64 {
        if let openThread { return openThread }
        let made = try await container.client.post(ChatPath.newThread, as: ChatThread.self)
        openThread = made.id
        newChat = false
        return made.id
    }

    // MARK: - coming and going

    public func setToast(_ said: String?) { toast = said }

    /// A push arrived.
    public func received(_ event: StateEvent) {
        threads.received(event)
        archivedThreads.received(event)
        // A message in the thread on screen, from another device or from this
        // one: read it back — but **not** while a reply is being written here,
        // which `ChatTranscript.load` refuses anyway.
        if let openThread, Self.listEvents.contains(event.type), !transcript.streaming {
            loadMessages(openThread)
        }
    }

    public func start() {
        threads.refresh()
        models.refresh()
        if showArchived { archivedThreads.refresh() }
    }

    /// The app moved house. Everything here is the old house's — the thread
    /// ids, which two houses can both have, most of all.
    public func serverChanged() {
        debounce?.cancel()
        cancelStream()
        openThread = nil
        newChat = false
        openTitle = ChatStrings.newChat
        transcript.clear()
        filter = ""
        hits = nil
        searchFailed = false
        showArchived = false
        renaming = nil
        deleting = nil
        sharing = nil
        // The pins are **not** cleared: they are keyed by address, so the
        // other house's are simply not matched, and coming home finds them
        // where they were.
        pinKeys = book.all
        threads.serverChanged()
        archivedThreads.serverChanged()
        models.serverChanged()
    }

    public func stop() {
        debounce?.cancel()
        cancelStream()
        threads.stop()
        archivedThreads.stop()
        models.stop()
    }
}

/// What the history list draws (`ChatHome.kt:421`), as one value.
public enum ChatListContent: Equatable, Sendable {
    /// Rows.
    case rows([ChatRow])
    /// Nothing, with a line saying which nothing it is, and the hint that goes
    /// with it where there is one.
    case empty(String, hint: String?)
    /// A search has been asked and has not answered. **Nothing is drawn** —
    /// not "no chats match", which would be a different and wrong answer for
    /// the few hundred milliseconds before the real one.
    case waiting
}

/// A chat as text, on its way to the share sheet.
public struct ChatShare: Equatable, Sendable {
    public let title: String
    public let text: String

    public init(title: String, text: String) {
        self.title = title
        self.text = text
    }
}
