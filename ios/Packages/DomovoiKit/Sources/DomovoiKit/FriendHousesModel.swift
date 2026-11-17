import Foundation
import Observation

/// What `read` gives back, as the screen shows it: the data, or the sentence
/// ``fhProblem(_:_:)`` makes of the failure. On the main actor, as every
/// caller is, so the closure never crosses an isolation boundary.
@MainActor
func fhRead<T: Sendable & Equatable>(_ action: String, _ block: () async throws -> T) async -> FhRead<T> {
    do {
        return .ok(try await block())
    } catch {
        return .problem(fhProblem(action, error))
    }
}

/// The Friend houses screen's state: which tab, which conversation is open,
/// and the three reads — Android's `FriendHousesScreen` composable's
/// `remember`s and its 15-second `LaunchedEffect`, as a type the package
/// tests drive.
///
/// ### Every answer is keyed by its server
///
/// Each read captures the server before it sends (and the call is held to
/// it, ``FriendHousesApi``'s `server:`), and what comes back is dropped when
/// the phone has moved house meanwhile — the read-after-await rule of this
/// track. A server change clears everything and starts again.
@MainActor
@Observable
public final class FriendHousesModel {

    /// How often the inbox, friends and shared lists are read again while the
    /// screen is up (nothing pushes them).
    public static let inboxPollMs: Int64 = 15_000

    public var tab: FhTab = .messages
    /// The house whose conversation is open, or nil for the tabs.
    public var open: String?

    public private(set) var inbox: FhRead<[FhConversation]>?
    public private(set) var friends: FhRead<[FhFriend]>?
    public private(set) var shared: FhRead<[FhSharedList]>?

    public let gate: FriendHousesGate

    private let container: AppContainer
    private let sleep: @Sendable (Int64) async throws -> Void
    @ObservationIgnored private var loop: Task<Void, Never>?
    @ObservationIgnored private var readFor: String?

    public init(
        container: AppContainer,
        gate: FriendHousesGate,
        sleep: @Sendable @escaping (Int64) async throws -> Void = { try await Task.sleep(for: .milliseconds($0)) }
    ) {
        self.container = container
        self.gate = gate
        self.sleep = sleep
    }

    /// The server the screen is about.
    public var server: String { container.store.serverURL }

    /// The status the header carries, or nil while hidden.
    public var status: FhStatus? { gate.shownFor(server) }

    /// The conversation open, from the inbox or a friend never written to.
    public var conversation: FhConversation? {
        fhOpenConversation(open, inbox: inbox?.value ?? [], friends: friends?.value ?? [])
    }

    // MARK: - lifecycle

    /// The screen is up: read now and every 15 s.
    public func start() {
        if readFor != ServerCredentials.normalize(server) { serverChanged() }
        guard loop == nil else { return }
        let sleep = self.sleep
        let every = Self.inboxPollMs
        loop = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                await self?.readAll()
                do { try await sleep(every) } catch { return }
            }
        }
    }

    /// The screen is gone: the reads stop.
    public func stop() {
        loop?.cancel()
        loop = nil
    }

    /// Back in the foreground: read now.
    public func cameBack() {
        stop()
        start()
    }

    /// The saved server moved: nothing of the last house's stays on screen.
    public func serverChanged() {
        readFor = ServerCredentials.normalize(server)
        inbox = nil
        friends = nil
        shared = nil
        open = nil
        if loop != nil {
            stop()
            start()
        }
    }

    /// The three reads, once; and the gate's, for the badge.
    public func readAll() async {
        let asked = server
        let api = gate.api
        let newInbox = await fhRead("load messages") { try await api.inbox(server: asked).newestFirst() }
        guard ServerCredentials.normalize(container.store.serverURL) == ServerCredentials.normalize(asked) else { return }
        inbox = newInbox
        let newFriends = await fhRead("load friends") { try await api.friends(server: asked) }
        guard ServerCredentials.normalize(container.store.serverURL) == ServerCredentials.normalize(asked) else { return }
        friends = newFriends
        let newShared = await fhRead("load shared lists") { try await api.sharedLists(server: asked) }
        guard ServerCredentials.normalize(container.store.serverURL) == ServerCredentials.normalize(asked) else { return }
        shared = newShared
        gate.refresh()
    }

    // MARK: - moving about

    public func openConversation(_ house: String) {
        guard FhPaths.isHouseKey(house) else { return }
        open = house
    }

    /// "message" on a friend: the Messages tab, on their conversation.
    public func message(_ friend: FhFriend) {
        guard FhPaths.isHouseKey(friend.house) else { return }
        tab = .messages
        open = friend.house
    }

    public func closeConversation() {
        open = nil
    }

    /// "open in lists": the list this phone opens next in Lists, for this
    /// server. The shell then navigates there.
    public func openInLists(_ shared: FhSharedList, selection: ListsSelection = ListsSelection()) -> Bool {
        guard let id = shared.listId, id > 0, shared.state == "active" else { return false }
        selection.save(server: server, id: id)
        return true
    }
}

/// One conversation on screen: newest at the bottom, earlier pages above, the
/// composer under it — Android's `ConversationView` as a type.
///
/// Opening it reads it and marks it read; then every 10 s while it is up,
/// marking read again when something new came in. Everything is held to the
/// server it was opened on: an answer, a mark or a send for that house never
/// lands on, or goes to, another.
@MainActor
@Observable
public final class FhConversationModel {

    public static let pollMs: Int64 = 10_000

    public let house: String
    public let name: String
    /// Nil: no longer a friend (read only, no composer).
    public let friendId: String?
    /// The server it was opened on.
    public let server: String

    public private(set) var thread = FhThread()
    public private(set) var problem: String?
    /// What is in the box. Written through ``edit(_:)``, which also clears
    /// the last send's problem, as Android's `onValueChange` does.
    public private(set) var text: String = ""
    public private(set) var busy = false
    public private(set) var sendProblem: String?

    private let loader: FhThreadLoader
    private let gate: FriendHousesGate?
    private let container: AppContainer
    private let sleep: @Sendable (Int64) async throws -> Void
    @ObservationIgnored private var loop: Task<Void, Never>?
    @ObservationIgnored private var didOpen = false

    /// How many mark-reads were sent — for a test.
    @ObservationIgnored public private(set) var marks = 0

    public init(
        conversation: FhConversation,
        container: AppContainer,
        api: FriendHousesApi,
        gate: FriendHousesGate? = nil,
        sleep: @Sendable @escaping (Int64) async throws -> Void = { try await Task.sleep(for: .milliseconds($0)) }
    ) {
        house = conversation.house
        name = FhWords.conversationName(conversation.name)
        friendId = conversation.friendId
        let server = container.store.serverURL
        self.server = server
        self.container = container
        self.gate = gate
        self.sleep = sleep
        loader = FhThreadLoader(api: api, house: conversation.house, server: server)
    }

    public var former: Bool { friendId == nil }
    public var length: Int { fhLength(text) }
    public var over: Bool { length > fhMessageMax }
    public var counter: String { FhWords.counter(length) }
    public var canSend: Bool { !busy && friendId != nil && fhSendable(text) }
    public var placeholder: String { FhWords.placeholder(name) }

    /// The box changed.
    public func edit(_ newText: String) {
        guard newText != text else { return }
        text = newText
        sendProblem = nil
    }

    /// The phone moved house since this was opened.
    private var moved: Bool {
        ServerCredentials.normalize(container.store.serverURL) != ServerCredentials.normalize(server)
    }

    // MARK: - lifecycle

    /// Opened: read and mark read, then every 10 s while up. Started again
    /// after the app comes back, it reads at once and carries on, as
    /// Android's `repeatOnLifecycle` block does; the open's mark-read is
    /// once per open.
    public func start() {
        guard loop == nil else { return }
        let sleep = self.sleep
        let every = Self.pollMs
        let first = !didOpen
        didOpen = true
        loop = Task { @MainActor [weak self] in
            if first {
                await self?.opened()
            } else {
                await self?.latest(first: false)
            }
            while !Task.isCancelled {
                do { try await sleep(every) } catch { return }
                if Task.isCancelled { return }
                await self?.latest(first: false)
            }
        }
    }

    public func stop() {
        loop?.cancel()
        loop = nil
    }

    /// The open: the newest page, then mark it read.
    public func opened() async {
        await latest(first: true)
        await markRead()
    }

    /// The newest page, merged in. A later read that brings an unread
    /// incoming message marks it read again.
    public func latest(first: Bool) async {
        let current = thread
        do {
            let (next, unread) = try await loader.latest(current, first: first)
            guard !moved else { return }
            // Never more than it held (what "Load earlier" brought stays), and
            // never more than ``fhThreadKeep`` from re-reads alone.
            let keep = min(max(fhThreadKeep, thread.messages.count), fhThreadKeepEarlier)
            thread = fhCapped(
                FhThread(messages: fhMerge(thread.messages, next.messages), more: next.more, loaded: true),
                keep: keep
            )
            problem = nil
            if !first && unread { await markRead() }
        } catch {
            guard !moved else { return }
            problem = fhProblem("load messages", error)
            thread.loaded = true
        }
    }

    /// "Load earlier": the page before the oldest held.
    public func loadEarlier() async {
        do {
            let next = try await loader.earlier(thread)
            guard !moved else { return }
            thread = fhCapped(
                FhThread(messages: fhMerge(thread.messages, next.messages), more: next.more, loaded: true),
                keep: fhThreadKeepEarlier
            )
        } catch {
            guard !moved else { return }
            problem = fhProblem("load earlier", error)
        }
    }

    /// Mark it read; the next read tries again if this fails.
    public func markRead() async {
        guard !moved else { return }
        do {
            _ = try await loader.markRead()
            marks += 1
            gate?.conversationOpened(house)
        } catch {
            // The next read tries again.
        }
    }

    /// Send what is in the box. On a 400/404 the server's sentence, as
    /// written; on a 403 only an admin can; never the text itself.
    public func send() async {
        guard canSend, let friendId else { return }
        let body = text
        busy = true
        defer { busy = false }
        do {
            _ = try await loader.api.send(friendId: friendId, text: body, server: server)
            guard !moved else { return }
            if text == body { text = "" }
            sendProblem = nil
            await latest(first: false)
        } catch {
            guard !moved else { return }
            sendProblem = fhProblem("send", error)
        }
    }
}
