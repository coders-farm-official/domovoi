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

/// A shared list's Decline or Stop sharing, waiting for its confirmation.
public struct FhSharedAsk: Equatable, Sendable, Identifiable {
    public enum Kind: Equatable, Sendable { case decline, stop }

    public let kind: Kind
    public let shared: FhSharedList
    /// The server the list was read from: the action goes there or nowhere.
    public let server: String

    public var id: String { "\(kind)-\(shared.id)" }

    public var title: String { kind == .decline ? FhWords.declineTitle : FhWords.stopTitle }
    public var confirm: String { kind == .decline ? FhWords.decline : FhWords.stopSharing }
    public var body: String {
        let friend = FhWords.friendName(shared.friend)
        switch kind {
        case .decline: return FhWords.declineBody(shared.title, friend)
        case .stop: return FhWords.stopBody(fhListName(shared), friend)
        }
    }
}

/// How an Accept ended, for the offer's row.
public enum FhAcceptOutcome: Equatable, Sendable {
    case accepted
    /// A 400: a name already taken here. The row asks for another name and
    /// says the server's sentence.
    case nameTaken(String)
    /// Anything else went wrong: the toast says so.
    case failed
    /// The phone moved house meanwhile: nothing to say here.
    case dropped
}

/// The Friend houses screen's state: which tab, which conversation is open,
/// the two reads, and the shared lists' Accept, Decline and Stop sharing —
/// Android's `FriendHousesScreen` composable's `remember`s and its 15-second
/// `LaunchedEffect`, as a type the package tests drive.
///
/// ### Every answer is keyed by its server
///
/// Each read and each action captures the server before it sends (and the
/// call is held to it, ``FriendHousesApi``'s `server:`), and what comes back
/// is dropped when the phone has moved house meanwhile — the read-after-await
/// rule of this track. A server change clears everything and starts again.
///
/// ### No friends read (1.4.1)
///
/// The friends list is the dashboard's admin zone, which the phones hide, so
/// this screen no longer reads `/friends` at all; a conversation opens from
/// the inbox.
@MainActor
@Observable
public final class FriendHousesModel {

    /// How often the inbox and shared lists are read again while the screen
    /// is up (nothing pushes them).
    public static let inboxPollMs: Int64 = 15_000

    public var tab: FhTab = .messages
    /// The house whose conversation is open, or nil for the tabs.
    public var open: String?

    public private(set) var inbox: FhRead<[FhConversation]>?
    public private(set) var shared: FhRead<[FhSharedList]>?

    /// The shared lists with an action in flight, by id.
    public private(set) var busyShared: Set<String> = []
    /// A Decline or Stop sharing waiting for its confirmation.
    public private(set) var ask: FhSharedAsk?
    /// A line for the shell's toast, taken by ``takeToast()``.
    public private(set) var toast: String?
    /// The server ``shared`` was read from: every list action goes there,
    /// and only while the phone is still on it.
    @ObservationIgnored private var sharedServer: String?

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

    /// The conversation open, from the inbox.
    public var conversation: FhConversation? {
        fhOpenConversation(open, inbox: inbox?.value ?? [], friends: [])
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
        shared = nil
        sharedServer = nil
        open = nil
        busyShared = []
        ask = nil
        toast = nil
        if loop != nil {
            stop()
            start()
        }
    }

    /// The phone is still on `asked`.
    private func still(_ asked: String) -> Bool {
        ServerCredentials.normalize(container.store.serverURL) == ServerCredentials.normalize(asked)
    }

    /// The two reads, once, in the page's order; and the gate's, for the
    /// badge and Home's row.
    public func readAll() async {
        let asked = server
        let api = gate.api
        let newInbox = await fhRead("load messages") { try await api.inbox(server: asked).newestFirst() }
        guard still(asked) else { return }
        inbox = newInbox
        await readShared(asked)
        guard still(asked) else { return }
        gate.refresh()
    }

    private func readShared(_ asked: String) async {
        let api = gate.api
        let newShared = await fhRead("load shared lists") { try await api.sharedLists(server: asked) }
        guard still(asked) else { return }
        shared = newShared
        sharedServer = asked
    }

    // MARK: - moving about

    public func openConversation(_ house: String) {
        guard FhPaths.isHouseKey(house) else { return }
        open = house
    }

    public func closeConversation() {
        open = nil
    }

    /// "open in Lists": the list this phone opens next in Lists, for this
    /// server. The shell then navigates there. Never for an offer: it has no
    /// list here yet.
    public func openInLists(_ shared: FhSharedList, selection: ListsSelection = ListsSelection()) -> Bool {
        guard let id = shared.listId, id > 0, !fhIsOffer(shared) else { return false }
        selection.save(server: server, id: id)
        return true
    }

    // MARK: - the shared lists' actions (household tier, 1.4.1)

    /// Accept a friend's list. `name`: what to call it here, or nil for
    /// their title. On a 400 (a name taken here) the row asks for another.
    @discardableResult
    public func accept(_ offer: FhSharedList, name: String? = nil) async -> FhAcceptOutcome {
        guard fhIsOffer(offer), FhPaths.isSharedListId(offer.id), !busyShared.contains(offer.id),
              let asked = sharedServer, still(asked)
        else { return .dropped }
        let api = gate.api
        busyShared.insert(offer.id)
        defer { if still(asked) { busyShared.remove(offer.id) } }
        do {
            let made = try await api.acceptSharedList(offer.id, name: name, server: asked)
            guard still(asked) else { return .dropped }
            toast = FhWords.accepted(made.name ?? name ?? offer.title)
            await readShared(asked)
            return .accepted
        } catch {
            guard still(asked) else { return .dropped }
            if let failure = (error as? DomovoiError)?.failure, failure.status == 400 {
                return .nameTaken(fhProblem("accept", error))
            }
            toast = fhProblem("accept", error)
            return .failed
        }
    }

    /// Decline (an offer) or Stop sharing (anything shared now): asks first.
    public func askDecline(_ offer: FhSharedList) {
        guard fhIsOffer(offer), FhPaths.isSharedListId(offer.id), let asked = sharedServer, still(asked) else { return }
        ask = FhSharedAsk(kind: .decline, shared: offer, server: asked)
    }

    public func askStop(_ shared: FhSharedList) {
        guard !fhIsOffer(shared), FhPaths.isSharedListId(shared.id), let asked = sharedServer, still(asked) else { return }
        ask = FhSharedAsk(kind: .stop, shared: shared, server: asked)
    }

    public func cancelAsk() {
        ask = nil
    }

    /// The confirmation said yes: decline it or stop sharing it.
    public func confirmAsk(_ confirmed: FhSharedAsk) async {
        guard ask == confirmed else { return }
        ask = nil
        let id = confirmed.shared.id
        let asked = confirmed.server
        guard !busyShared.contains(id), still(asked) else { return }
        let api = gate.api
        busyShared.insert(id)
        defer { if still(asked) { busyShared.remove(id) } }
        let action = confirmed.kind == .decline ? "decline" : "stop sharing"
        do {
            switch confirmed.kind {
            case .decline: try await api.declineSharedList(id, server: asked)
            case .stop: try await api.stopSharing(id, server: asked)
            }
            guard still(asked) else { return }
            toast = confirmed.kind == .decline ? FhWords.declined : FhWords.stopped
            await readShared(asked)
        } catch {
            guard still(asked) else { return }
            toast = fhProblem(action, error)
        }
    }

    /// The toast's line, once.
    public func takeToast() -> String? {
        defer { toast = nil }
        return toast
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
