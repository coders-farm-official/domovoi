import Foundation
import Observation

/// The queue, as the player holds it: what ``PhoneUpNext`` reads and the
/// only ways it changes it (`PhoneUpNext.kt` `Host`). ``PlayerModel`` is
/// the one in the app.
@MainActor
public protocol PhoneUpNextHost: AnyObject {
    var upNextQueue: [PlayerItem] { get }
    var upNextIndex: Int { get }
    /// Casting to a room: the room's server carries the session.
    var upNextCasting: Bool { get }
    /// Change songs in place (the same songs, new tags).
    func upNextRetag(_ change: (PlayerItem) -> PlayerItem)
    /// Add `items` at the end of the queue.
    func upNextAppend(_ items: [PlayerItem])
    /// Take out the songs after the one playing that `drop` names.
    func upNextRemoveWaiting(_ drop: (PlayerItem) -> Bool)
    /// The session's id was just put on queued songs: the listen of the one
    /// playing carries it from now on.
    func upNextSessionArrived()
}

/// What the session needs to know about the app (`PhoneUpNext.kt` `Env`).
public struct PhoneUpNextEnv {
    /// The server the app talks to now, normalised (``ServerCredentials/normalize(_:)``).
    public var server: @MainActor () -> String
    /// Paired with that server, and not in room mode: Up next may be asked for.
    public var paired: @MainActor () -> Bool
    public var deviceID: @MainActor () -> String
    /// The Autoplay preference (per device, default on).
    public var autoplay: @MainActor () -> Bool
    public var saveAutoplay: @MainActor (Bool) -> Void
    /// The raw "listening as" pick for a server (``PlayerListenerStore/pick(for:)``).
    public var listenerPick: @MainActor (String) -> String?
    public var shared: @MainActor (String) -> Bool
    /// The "for" selector picked this person (nil: the household).
    public var setListener: @MainActor (String?) -> Void
    /// The household's people on a server (`GET /api/people`), for the "for"
    /// selector; empty when it can't be read.
    public var people: @MainActor (String) async -> [UpNextPerson]

    public init(
        server: @escaping @MainActor () -> String,
        paired: @escaping @MainActor () -> Bool,
        deviceID: @escaping @MainActor () -> String,
        autoplay: @escaping @MainActor () -> Bool,
        saveAutoplay: @escaping @MainActor (Bool) -> Void,
        listenerPick: @escaping @MainActor (String) -> String?,
        shared: @escaping @MainActor (String) -> Bool,
        setListener: @escaping @MainActor (String?) -> Void,
        people: @escaping @MainActor (String) async -> [UpNextPerson] = { _ in [] }
    ) {
        self.server = server
        self.paired = paired
        self.deviceID = deviceID
        self.autoplay = autoplay
        self.saveAutoplay = saveAutoplay
        self.listenerPick = listenerPick
        self.shared = shared
        self.setListener = setListener
        self.people = people
    }
}

/// The phone's own Up next session (P9; Android `PhoneUpNext.kt`, the web's
/// player.jsx startUpNext, refill, setAutoplay, lessLikeThis,
/// setUpNextListener): it starts one for every kind of play, asks for more at
/// ``UpNextRules/refillAt`` songs left, takes picks back when the session
/// does, and follows the Autoplay switch.
///
/// The player (``PlayerModel``) owns the queue and is the host; this class
/// only edits it through the host. Everything runs on the main actor:
/// `launch` starts the network part, and **each answer is applied only while
/// the play it was for (``gen``) and its server are still the current
/// ones** — a newer play, a dropped session or another server makes it a
/// no-op ([[read-after-await-wrong-key]]).
@MainActor
@Observable
public final class PhoneUpNext {

    /// The session; nil for none.
    public private(set) var state: UpNextState?

    /// This phone's Autoplay preference as last read or set, so a view
    /// drawing the switch follows it.
    public private(set) var preference: Bool

    /// The household's people, for the server they were read from.
    public private(set) var roster: (server: String, people: [UpNextPerson])?

    /// How many plays started (or dropped) a session: an answer for an older
    /// one is ignored.
    @ObservationIgnored public private(set) var gen = 0

    @ObservationIgnored private var refilling = false

    @ObservationIgnored private let api: any UpNextApi
    @ObservationIgnored private weak var host: (any PhoneUpNextHost)?
    @ObservationIgnored private let env: PhoneUpNextEnv
    @ObservationIgnored private let launch: @MainActor (@escaping @MainActor @Sendable () async -> Void) -> Void

    public init(
        api: any UpNextApi,
        host: any PhoneUpNextHost,
        env: PhoneUpNextEnv,
        launch: @escaping @MainActor (@escaping @MainActor @Sendable () async -> Void) -> Void = { work in
            _ = Task { @MainActor in await work() }
        }
    ) {
        self.api = api
        self.host = host
        self.env = env
        self.launch = launch
        self.preference = env.autoplay()
    }

    private func current(_ u: UpNextState) -> Bool { u.gen == gen && u.server == env.server() }

    private func stillFor(_ g: Int, _ server: String) -> Bool { g == gen && server == env.server() }

    private static func isPick(_ item: PlayerItem, of id: String) -> Bool {
        item.queueOrigin == .auto && item.sessionID == id
    }

    // MARK: - what is read

    /// Paired with the server the app talks to, and not in room mode: Up
    /// next may be asked for (and shown).
    public var mayAsk: Bool { env.paired() }

    /// What the switch says: the session's, else this phone's preference.
    public var autoplay: Bool { state?.autoplay ?? preference }

    /// The people of the server the app talks to now (none read yet, or
    /// another server's: none).
    public var people: [UpNextPerson] {
        guard let roster, roster.server == env.server() else { return [] }
        return roster.people
    }

    /// Read the household's people for the "for" selector. Quiet: a read
    /// that fails leaves the selector out.
    public func readPeople() async {
        let server = env.server()
        let read = await env.people(server)
        guard server == env.server() else { return }
        roster = (server, read.filter { $0.id > 0 })
    }

    // MARK: - plays

    /// A new play of anything: the session before it is gone (here). The
    /// number to tag the play's songs with (``PlayerItem/upNextGen``).
    @discardableResult
    public func newPlay() -> Int {
        gen += 1
        refilling = false
        state = nil
        return gen
    }

    /// The queue was cleared, or its server left: no session.
    public func drop() { newPlay() }

    /// A song tapped inside a list (`context`; nil: on its own, library
    /// mode). `listIDs`: the list's songs, for an `ids` list.
    public func startFromList(trackID: Int64, context: UpNextContext?, listIDs: [Int64]) {
        start(.fromList(trackID: trackID, context: context, listIDs: listIDs))
    }

    /// A whole list played on purpose (`listIDs` in play order).
    public func startWholeList(context: UpNextContext?, listIDs: [Int64]) {
        guard !listIDs.isEmpty else { return }
        start(.wholeList(context: context, listIDs: listIDs))
    }

    /// The body a start sends (§3.5f), for this device.
    func startBody(_ start: UpNextStart, server: String, autoplay: Bool) -> Data {
        var body: [String: Any] = [
            "target": ["kind": "device", "device_id": env.deviceID()],
            "start": start.json,
            "autoplay": autoplay,
        ]
        body.merge(UpNextListenerFields.fields(pick: env.listenerPick(server), shared: env.shared(server))) { _, new in new }
        return HttpUpNextApi.encode(body)
    }

    private func start(_ start: UpNextStart) {
        guard env.paired() else { return }
        let g = gen
        let server = env.server()
        let auto = env.autoplay()
        state = UpNextState(server: server, gen: g, autoplay: auto, loading: true, start: start)
        let body = startBody(start, server: server, autoplay: auto)
        let api = self.api
        launch { [weak self] in
            let session: UpNextSession
            do {
                session = try await api.start(server: server, body: body)
            } catch {
                guard let self, self.stillFor(g, server) else { return }
                // Refused (a song the library lost, a sign-in): no Up next
                // for this play, quietly. Unreachable or failing: say Up next
                // needs the home server, and try again when the song changes.
                self.state = upNextRefused(error)
                    ? UpNextState(server: server, gen: g, autoplay: auto, refused: true)
                    : UpNextState(server: server, gen: g, autoplay: auto, offline: true, start: start)
                return
            }
            guard let self, self.stillFor(g, server) else { return }
            self.state = .from(session, server: server, gen: g)
            // The songs this start played are the session's too (listens feed it).
            self.host?.upNextRetag { item in
                guard item.upNextGen == g, item.sessionID == nil else { return item }
                var tagged = item
                tagged.sessionID = session.id
                return tagged
            }
            self.host?.upNextAppend(session.picks.map { UpNextRules.pickItem($0, sessionID: session.id) })
            self.host?.upNextSessionArrived()
            self.check()
        }
    }

    // MARK: - the refill (on every change of song or queue)

    /// Asks for more once ``UpNextRules/refillAt`` or fewer songs are left
    /// after the one playing; starts a session for a queue of library songs
    /// that has none (songs added one by one); tries a failed start again on
    /// a new song. Never while casting (the room refills itself), never twice
    /// at once, never twice on the same song after a failure.
    public func check() {
        guard let host, !host.upNextCasting, !refilling else { return }
        let q = host.upNextQueue
        let index = host.upNextIndex
        guard !q.isEmpty, q.indices.contains(index) else { return }
        guard q.count - 1 - index <= UpNextRules.refillAt else { return }
        guard let u = state else {
            guard env.autoplay(), env.paired() else { return }
            let lib = q.filter(\.isLibrarySong)
            guard !lib.isEmpty else { return }
            let g = newPlay()
            host.upNextRetag { item in
                guard item.isLibrarySong else { return item }
                var tagged = item
                tagged.upNextGen = g
                return tagged
            }
            start(.queue(lib.map(\.itemID)))
            return
        }
        guard current(u) else { return }
        if u.failedAt == index { return }
        guard u.id != nil else {
            if u.offline, let again = u.start, !u.loading {
                if u.failedAt == nil {
                    var marked = u
                    marked.failedAt = index
                    state = marked
                } else {
                    start(again)
                }
            }
            return
        }
        if u.loading || u.ended || u.refused { return }
        if !u.autoplay && u.mode != "list" { return }
        refill(u, at: index)
    }

    private func refill(_ u: UpNextState, at: Int) {
        guard let id = u.id else { return }
        let g = gen
        refilling = true
        let api = self.api
        launch { [weak self] in
            let answer: Result<UpNextBatch, any Error>
            do {
                answer = .success(try await api.next(server: u.server, id: id, haveThrough: u.haveThrough))
            } catch {
                answer = .failure(error)
            }
            guard let self else { return }
            guard self.stillFor(g, u.server) else {
                if g == self.gen { self.refilling = false }
                return
            }
            switch answer {
            case .success(let r):
                let gone = Set(r.removed)
                let picks = r.picks.filter { $0.seq > u.haveThrough && !gone.contains($0.seq) }
                let top = r.picks.reduce(u.haveThrough) { max($0, $1.seq) }
                if !gone.isEmpty {
                    self.host?.upNextRemoveWaiting { Self.isPick($0, of: id) && gone.contains($0.upNextSeq ?? -1) }
                }
                if !picks.isEmpty {
                    self.host?.upNextAppend(picks.map { UpNextRules.pickItem($0, sessionID: id) })
                }
                // Nothing new and not over: ask again when the song changes,
                // not in a loop.
                let idle = picks.isEmpty && !r.ended
                if var now = self.state, now.id == id, self.current(now) {
                    now.haveThrough = top
                    now.ended = r.ended
                    now.offline = false
                    now.failedAt = idle ? at : nil
                    now.mode = r.mode ?? u.mode
                    self.state = now
                }
            case .failure(let error):
                if var x = self.state, x.id == id {
                    if upNextRefused(error) {
                        // The session is gone or no longer this phone's: no
                        // more asking, and nothing shown that can't work.
                        x.refused = true
                    } else {
                        x.offline = true
                        x.failedAt = at
                    }
                    self.state = x
                } else {
                    if g == self.gen { self.refilling = false }
                    return
                }
            }
            if g == self.gen {
                self.refilling = false
                self.check()
            }
        }
    }

    // MARK: - what the person does

    /// The Autoplay switch. Off: the waiting picks of a mix leave the queue
    /// and nothing more is added (a list keeps the rest of itself, then
    /// stops). Remembered for this device. Throws when the server didn't
    /// take it (the switch still says what this phone does).
    public func setAutoplay(_ on: Bool) async throws {
        env.saveAutoplay(on)
        preference = on
        guard let u = state, current(u) else {
            check()
            return
        }
        var v = u
        v.autoplay = on
        if on { v.ended = false }
        v.failedAt = nil
        state = v
        if !on, u.mode != "list" { host?.upNextRemoveWaiting { $0.queueOrigin == .auto } }
        defer { check() }
        if let id = u.id {
            _ = try await api.patch(server: u.server, id: id, body: HttpUpNextApi.encode(["autoplay": on]))
        }
    }

    /// "Less like this" on the pick at queue row `i`: that artist is out of
    /// the mix for the rest of the session, their waiting picks with it. The
    /// seqs gone (the row's own included); empty when `i` is no pick of the
    /// session. Throws when the server didn't take it.
    public func lessLikeThis(_ i: Int) async throws -> [Int] {
        guard let host, host.upNextQueue.indices.contains(i) else { return [] }
        let item = host.upNextQueue[i]
        guard let u = state, let id = u.id, let seq = item.upNextSeq,
              Self.isPick(item, of: id), current(u) else { return [] }
        let g = gen
        let removed = try await api.feedback(server: u.server, id: id, seq: seq, kind: "less_like_this")
        let gone = Set(removed + [seq])
        if stillFor(g, u.server), state?.id == id {
            self.host?.upNextRemoveWaiting { Self.isPick($0, of: id) && gone.contains($0.upNextSeq ?? -1) }
            check()
        }
        return gone.sorted()
    }

    /// A pick taken out of the queue (`item`, already gone): the session
    /// hears it, and any picks it takes back with it leave too.
    public func removed(_ item: PlayerItem) {
        guard let u = state, let id = u.id, let seq = item.upNextSeq,
              Self.isPick(item, of: id), current(u) else { return }
        let g = gen
        let api = self.api
        launch { [weak self] in
            let back: [Int]
            do {
                back = try await api.feedback(server: u.server, id: id, seq: seq, kind: "remove")
            } catch {
                return // best-effort, as on the web
            }
            guard let self, self.stillFor(g, u.server), self.state?.id == id else { return }
            let gone = Set(back).subtracting([seq])
            if !gone.isEmpty {
                self.host?.upNextRemoveWaiting { Self.isPick($0, of: id) && gone.contains($0.upNextSeq ?? -1) }
            }
        }
    }

    /// Whose taste the mix follows (the "for" selector): the same "listening
    /// as" the rest of the player uses; the session follows it from its next
    /// batch. nil: the household. Throws when the server didn't take it.
    public func setListener(_ personID: Int64?) async throws {
        env.setListener(personID.map { String($0) })
        guard let u = state, let id = u.id, current(u) else { return }
        let body: Data = personID.map { HttpUpNextApi.encode(["listener_person_id": $0]) }
            ?? Data(#"{"listener_person_id":null}"#.utf8)
        let s = try await api.patch(server: u.server, id: id, body: body)
        if let s, var now = state, now.id == id, current(now) {
            now.person = s.person
            state = now
        }
    }

    // MARK: - casting

    /// What a cast (`POST /api/music/play-tracks`) adds: this phone's
    /// session goes with the queue (the room's server carries it on and
    /// refills the room itself), autoplay when off, and whose mix it is.
    public func castFields() -> [String: Any] {
        let server = env.server()
        var out: [String: Any] = [:]
        if let u = state, let id = u.id, !u.ended, !u.refused, u.server == server, u.gen == gen {
            out["up_next_session_id"] = id
        }
        if !env.autoplay() { out["autoplay"] = false }
        out.merge(UpNextListenerFields.fields(pick: env.listenerPick(server), shared: env.shared(server))) { _, new in new }
        return out
    }

    /// Back to this phone from a room: the session the room carried on comes
    /// home (`…/move` to this device). One the room no longer has (another
    /// play replaced it there: 409 / 404), or a move that didn't happen, is
    /// dropped here; the queue starts a new one when it is nearly over.
    public func handBack() {
        guard let u = state, let id = u.id, current(u) else { return }
        let g = gen
        let target = HttpUpNextApi.encode(["kind": "device", "device_id": env.deviceID()])
        let api = self.api
        launch { [weak self] in
            var moved = true
            do {
                _ = try await api.move(server: u.server, id: id, target: target)
            } catch {
                moved = false
            }
            guard let self, self.stillFor(g, u.server), self.state?.id == id else { return }
            if !moved { self.drop() }
            self.check()
        }
    }

    // MARK: - back on screen

    /// Read the session again (`GET …/{id}`): the picks still waiting there
    /// replace the ones waiting here (the server may have taken some back
    /// while the app was away), and anything the phone doesn't hold yet
    /// joins. A session the server no longer has (404) is dropped. Quiet:
    /// any other failure changes nothing.
    public func reread() {
        guard let host, let u = state, let id = u.id else { return }
        guard !host.upNextCasting, !refilling, !u.loading, current(u) else { return }
        let g = gen
        let api = self.api
        launch { [weak self] in
            let s: UpNextSession
            do {
                s = try await api.read(server: u.server, id: id)
            } catch {
                guard let self else { return }
                if self.stillFor(g, u.server), self.state?.id == id, upNextStatus(error) == 404 {
                    self.drop()
                    self.check()
                }
                return
            }
            guard let self, self.stillFor(g, u.server), !self.refilling else { return }
            guard let now = self.state, now.id == id else { return }
            let waiting = s.picks.filter { $0.status == nil || $0.status == "queued" }
            let keep = Set(waiting.map(\.seq))
            let held = Set((self.host?.upNextQueue ?? []).filter { Self.isPick($0, of: id) }.compactMap(\.upNextSeq))
            self.host?.upNextRemoveWaiting { Self.isPick($0, of: id) && !keep.contains($0.upNextSeq ?? -1) }
            let add = waiting.filter { !held.contains($0.seq) && $0.seq > now.haveThrough }
            if !add.isEmpty { self.host?.upNextAppend(add.map { UpNextRules.pickItem($0, sessionID: id) }) }
            var next = now
            next.mode = s.mode ?? now.mode
            next.autoplay = s.autoplay
            next.person = s.person
            next.seed = s.seed ?? now.seed
            next.ended = s.ended
            next.haveThrough = max(now.haveThrough, s.haveThroughSeq)
            next.target = s.target ?? now.target
            self.state = next
            self.check()
        }
    }
}
