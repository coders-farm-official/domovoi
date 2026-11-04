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

    /// The title the row was opened under, kept only as the third thing
    /// ``openTitle`` asks. Nil for a new chat.
    private var storedTitle: String?

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

    /// How many times this app has moved house since it launched.
    ///
    /// **The mechanism for [[read-after-await-wrong-key]], said once.** This
    /// track's top defect class is a value read after an `await` and written
    /// back under a key that has since changed. In this type the key that
    /// changes is the house: a thread id is the *house's* id, and two houses
    /// can both have a thread 7 — ``serverChanged()`` says exactly that in its
    /// own words — so `openThread == id` cannot tell house A's answer from
    /// house B's, and house A's conversation lands under house B's title.
    ///
    /// So **every `Task` this type starts takes this number before its first
    /// await and asks ``stillHome(_:)`` after it**: ``loadMessages(_:)``,
    /// ``share(_:)``, ``write(verb:method:path:body:said:)``,
    /// ``ensureThread(_:)`` and the stream consumer ``run(path:body:verb:on:)``.
    /// A task that no longer holds the house it was started for writes
    /// nothing, says nothing and refreshes nothing. One `guard` at each call
    /// site, and this is the only place the rule is written down.
    ///
    /// A counter rather than the address itself, because leaving a house and
    /// coming back to it is also a change: the first visit's in-flight read is
    /// as wrong on the second visit as it was on the first, and
    /// `normalize(serverURL)` cannot tell the two visits apart.
    ///
    /// A counter rather than a tracked handle per task, because cancelling is
    /// best effort and the guard is what makes this correct:
    /// ``ApiRead/run()`` cancels *and* guards for the reason its own comment
    /// gives — "a `fetch` that ignores cancellation can still return". Two
    /// half-mechanisms would be worse than one that holds everywhere, and the
    /// stream (the one task with a real handle) is cancelled as well.
    private var houseEpoch = 0

    /// Whether a task that took ``houseEpoch`` before its await still owns
    /// what it is about to write.
    private func stillHome(_ epoch: Int) -> Bool { epoch == houseEpoch }

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

    /// How old a row is, in words (`ChatHome.kt:613`'s `relTime`).
    ///
    /// Here rather than in the row's view, for ``HomeAttentionView``'s reason:
    /// **a view that reads the wall clock itself cannot be driven to a
    /// boundary by a test**, and the row times would drift from the rest of
    /// the screen's. ``ChatRules/row(_:)`` deliberately does not take a clock
    /// — the row keeps `updatedMs` and the *wording* of an age is
    /// ``relTimeMs(_:nowMs:)``'s — and this is the one place that passes it
    /// one, which is the clock `init` already takes.
    ///
    /// It is also the reader that clock did not have. It was stored by part 1
    /// and read by nothing, which on this project is a bug rather than a seam
    /// ([[reasoning-that-hides-a-bug]]: grep the call site before saying a
    /// value is used).
    ///
    /// No ``houseEpoch`` here, and none needed: there is no `await` in it.
    public func timeText(_ row: ChatRow) -> String {
        relTimeMs(row.updatedMs == 0 ? nil : row.updatedMs, nowMs: nowMs())
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

    /// The title shown over the conversation, which is **the list's title
    /// first** (`ChatScreen.kt:941`'s `titleOf`, built at
    /// `ServerChatPlace.kt:149`).
    ///
    /// Android's chain of four, link for link, and the first link is the one
    /// that matters: the Kotlin writes down both of its reasons — *"the model's
    /// title arrives after the first reply; a rename on another device shows
    /// too"*. A stored value cannot do either. A new chat's header said "new
    /// chat" for ever after the server's title job had named the thread, and a
    /// rename made on the dashboard never showed while the chat was open here.
    ///
    /// It also means a **refused** rename cannot leave the rejected name in the
    /// header: nothing writes this, so the header follows the list back.
    public var openTitle: String {
        if let openThread, let named = listTitle(of: openThread) { return named }
        // Not ``ChatStrings/newChat``: that is ``ChatRules/row(_:)``'s
        // substitution for a thread the server has **not** named, so a row
        // handing it over is the list saying it has no name and the chain has
        // to go on. Android's second link is the `ThreadRow`'s own nullable
        // title, which is null in exactly that case.
        if let storedTitle, storedTitle != ChatStrings.newChat { return storedTitle }
        if let asked = transcript.messages.first(where: { $0.role == "user" })?.content,
           let words = ChatTitles.firstWords(asked) { return words }
        return ChatStrings.newChat
    }

    /// The title either list has for `id` right now, or nil — including for a
    /// thread the server has not named yet, because Android's `titleOf` reads
    /// `ThreadRow.title`, which is nullable, and falls through on a null.
    /// ``ChatRules/row(_:)``'s "new chat" substitution is the *list's* wording
    /// and must not short-circuit the chain's third link.
    private func listTitle(of id: Int64) -> String? {
        let known = (threads.data?.threads ?? []) + (archivedThreads.data?.threads ?? [])
        return known.first { $0.id == id }?.title
    }

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
        // **The header is not set here.** ``openTitle`` reads the list, so the
        // new name appears when the `PATCH` has landed and the list has been
        // read again — and a *refused* rename leaves the old name where it was
        // instead of the rejected one in the header for as long as the chat is
        // open. Android gets the same for free from `titleOf`.
        write(verb: ChatStrings.verbRename, method: "PATCH", path: ChatPath.thread(id),
              body: ChatPath.patchBody(title: name))
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
        let epoch = houseEpoch
        Task { @MainActor [weak self] in
            var said: String? = said
            do {
                try await client.send(method: method, path: path, body: body)
            } catch let trouble as DomovoiError {
                said = isAiOffRefusal(trouble) ? aiChatOffText : failureText(verb, trouble)
            } catch {
                said = failureText(verb, .unreachable("\(error)"))
            }
            // ``houseEpoch``: an "archived" toast for a thread in a house the
            // person has left names a chat that is no longer on screen, and
            // the new house's lists are its own start()'s business.
            guard let self, self.stillHome(epoch) else { return }
            self.toast = said
            self.refreshLists()
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
        let epoch = houseEpoch
        Task { @MainActor [weak self] in
            guard let list = try? await client.get(ChatPath.messages(id), as: ChatMessageList.self)
            else { return }
            // ``houseEpoch``: this one hands a whole conversation to the share
            // sheet, so a read from the house the person has left would put
            // the *other house's* words into whatever they share next.
            // ``serverChanged()`` nils `sharing`; without this guard this task
            // set it back.
            guard let self, self.stillHome(epoch) else { return }
            self.sharing = ChatShare(
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
        storedTitle = title
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
        storedTitle = nil
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
    /// [[no-misleading-controls]] is about.
    ///
    /// A reply being written does **not** take the composer away — the box
    /// stays live so the next message can be typed while the last one
    /// finishes. That is ``canSend``'s question, not this one, and the two
    /// were conflated here: this comment used to claim the `sending` guard
    /// (`ChatScreen.kt:736`) as well, which the expression has never carried.
    public var composerOffered: Bool { aiEnabled && conversationOpen }

    /// Whether a send would be taken **now**.
    ///
    /// The one gate ``send(_:)`` and both composers ask. Nothing is queued: a
    /// send refused because a reply is being written is simply refused, which
    /// is why neither composer draws a send in that state (the views carry the
    /// reasoning, and this batch's DECISIONS.md the departure from Android).
    public var canSend: Bool { composerOffered && !sending }

    /// Show one page more of the open thread (``ChatTranscriptWindow``, D64).
    ///
    /// On the model and not on the view's own copy of the transcript, because
    /// ``transcript`` is `private(set)`: the window is the model's state, and
    /// it has to survive a redraw.
    public func showEarlier() {
        transcript.showEarlier()
    }

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
        let epoch = houseEpoch
        Task { @MainActor [weak self] in
            do {
                let list = try await client.get(ChatPath.messages(id), as: ChatMessageList.self)
                // **The thread that is open now, in the house that is open
                // now.** The id alone is not the key: a thread id belongs to a
                // house, two houses can both have a thread 7, and house A's
                // slow read then passed `openThread == 7` and drew house A's
                // conversation under house B's title. ``houseEpoch`` is the
                // other half, and it is also what drops a read from a house the
                // person has left when nothing at all is open.
                guard let self, self.stillHome(epoch), self.openThread == id else { return }
                self.transcript.load(list.messages, thisDeviceID: who)
            } catch let trouble as DomovoiError {
                guard let self, self.stillHome(epoch), self.openThread == id else { return }
                // ``ChatStrings/verbOpenChat``, not `verbSend`: **nothing was
                // sent**. This is the read that draws a conversation, and a
                // toast reading "send failed (offline?)" for a chat that would
                // not open names something the person did not do.
                self.toast = isAiOffRefusal(trouble)
                    ? aiChatOffText
                    : failureText(ChatStrings.verbOpenChat, trouble)
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
        let who = deviceID
        let epoch = houseEpoch
        stream = Task { @MainActor [weak self] in
            guard let self else { return }
            // `throws(DomovoiError)` on the `do` itself, which is what lets
            // the one `catch` below be the only one: a bare `do` is typed
            // `any Error` even when everything in it throws the same thing,
            // and the second `catch` that used to sit under this one could
            // not be reached.
            do throws(DomovoiError) {
                // Nil when the chat that asked moved on while the thread was
                // being made — ``ensureThread(_:)`` says what that means.
                // Nothing to say and nothing to settle: every caller that can
                // overtake a send goes through `cancelStream()`, which has
                // already put `sending` back.
                guard let id = try await self.ensureThread(epoch) else { return }
                await self.run(
                    path: ChatPath.messages(id),
                    body: ChatPath.sendBody(content: content, deviceID: who),
                    verb: ChatStrings.verbSend,
                    on: epoch
                )
            } catch {
                // The thread could not even be made: the refusal goes where the
                // reply would have been, as Android's `onFailure` puts it
                // (`ChatScreen.kt:773`).
                self.transcript.failLive(sendFailureLine(error, verb: ChatStrings.verbNewChat))
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
        let epoch = houseEpoch
        stream = Task { @MainActor [weak self] in
            await self?.run(
                path: ChatPath.retry(id),
                // No body. The route takes none (`chat.py:1069`), and Android
                // sends `{}` only because its builder always sends one.
                body: nil,
                verb: ChatStrings.verbTryAgain,
                on: epoch
            )
        }
        return true
    }

    /// One streamed round trip: open it, apply every event, then settle.
    private func run(path: String, body: Data?, verb: String, on epoch: Int) async {
        let client = container.client
        do {
            let events = try await client.events(method: "POST", path: path, body: body)
            for try await event in events {
                // ``houseEpoch`` beside the cancellation check: a stream opened
                // against the old house must not write its words into the
                // transcript of the new one.
                guard !Task.isCancelled, stillHome(epoch) else { break }
                react(to: transcript.apply(event))
            }
        } catch let trouble as DomovoiError {
            guard stillHome(epoch) else { return }
            transcript.failLive(sendFailureLine(trouble, verb: verb))
        } catch {
            guard stillHome(epoch) else { return }
            transcript.failLive(failureText(verb, .unreachable("\(error)")))
        }
        guard stillHome(epoch) else { return }
        endStream()
    }

    private func react(to step: ChatStreamStep) {
        // Refused for want of the vision model: read what is installed again,
        // so the refusal's own sentence is right next time
        // (`ChatScreen.kt:776`).
        if case .failed(_, let code, _) = step, code == ChatErrors.visionModelMissing {
            models.refresh()
        }
    }

    private func endStream() {
        switch transcript.finishStream(atISO: nowISO()) {
        case .keptOldReply(let why):
            // A retry that failed. The old reply is back — the thread on the
            // server never changed — and the failure is said once, as a toast,
            // rather than left attached to a reply that is not the one that
            // failed.
            //
            // **Nothing at all when the stream gave no reason**, as Android
            // does (`ChatScreen.kt:879` is `live.error?.let { toast(...) }`).
            // The fallback this used to have was `ChatStrings.searchFailed`,
            // which is "couldn't search": a retry whose stream opened, said
            // something and closed without an `error` or a `done` — a generator
            // ending early, a worker restart, a proxy closing after the headers
            // — told the person **the search** could not run. The old reply is
            // back on screen, which is the truth and is itself the answer.
            if let why { toast = ChatStrings.couldNotTryAgain(why) }
        // `.unfinished` had a `verb == verbTryAgain` branch, which could not be
        // reached: during a retry `stashed` stays non-nil until a `done`
        // settles it, so `finishStream` takes `.keptOldReply` every time and a
        // retry that got its `done` leaves nothing running at all.
        case .unfinished, .finished, .nothingRunning:
            break
        }
        sending = false
        // The list's own row for this thread is now stale: its snippet, its
        // count and its `updated_at` all moved.
        //
        // **Not for a stream that was cancelled.** `stop()` → `cancelStream()`
        // → `threads.stop()`, and then the cancelled task's continuation got
        // here and re-issued the two reads `stop()` had just stopped
        // (``ApiRead/refresh()`` re-joins the server-change registry), so a
        // screen going away asked the house for both lists on its way out.
        guard !Task.isCancelled else { return }
        refreshLists()
    }

    private func cancelStream() {
        stream?.cancel()
        stream = nil
        sending = false
    }

    /// The thread to write to: the one open, or the one this message makes
    /// (`ensureThread`, `ChatScreen.kt:438`).
    /// Nil when the chat that asked for the thread is no longer the one open:
    /// the thread was made on the server, and **this is not the send that may
    /// claim it**.
    ///
    /// Writing `openThread` here regardless was the same defect class as B1 in
    /// its worst form. Send from a new chat; while `POST /threads` is in
    /// flight, tap a row. `open(_:)` sets `openThread = 2` and reads thread
    /// 2's messages; the POST's continuation then clobbered `openThread` to
    /// the brand-new thread 77, so thread 2's own read failed *its* guard and
    /// was discarded, the header showed thread 2's title over an empty
    /// transcript, and the next message went into thread 77. Narrow —
    /// `session.data(for:delegate:)` is cancellation-aware, so the cancelled
    /// POST normally throws — but the guard is one line and
    /// ``ApiRead/run()``'s comment is this project's rule: *"a `fetch` that
    /// ignores cancellation can still return."*
    ///
    /// The thread it made is left behind on the house's list, as Android's is
    /// (`ChatScreen.kt:438` has no guard either). An empty thread in the list
    /// is a smaller wrong thing than a composer pointed at a chat that is not
    /// on screen.
    private func ensureThread(_ epoch: Int) async throws(DomovoiError) -> Int64? {
        if let openThread { return openThread }
        let made = try await container.client.post(ChatPath.newThread, as: ChatThread.self)
        guard stillHome(epoch), newChat, openThread == nil, !Task.isCancelled else { return nil }
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
        // **Both lists, always**, as Android does: `ServerChatPlace.chats` is
        // `threads + archivedThreads` and both are read on mount
        // (`ServerChatPlace.kt:65-70`, `:87`).
        //
        // Reading the archived list only `if showArchived` was a closed loop.
        // The only control that calls ``showArchived(_:)`` is the row the list
        // draws **when the archived count is non-zero** (`ChatHome.kt:443`'s
        // `archived.isNotEmpty()`), the count comes from this read, and the
        // read was waiting to be asked for — so the archived chats were simply
        // missing when Chat was opened, and turned up later, after the first
        // push or the first write, for no reason the person could see.
        archivedThreads.refresh()
    }

    /// The app moved house. Everything here is the old house's — the thread
    /// ids, which two houses can both have, most of all.
    public func serverChanged() {
        // **First**, so that every task already in flight for the old house
        // fails ``stillHome(_:)`` from here on, whatever order its continuation
        // runs in.
        houseEpoch += 1
        debounce?.cancel()
        cancelStream()
        openThread = nil
        newChat = false
        storedTitle = nil
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
