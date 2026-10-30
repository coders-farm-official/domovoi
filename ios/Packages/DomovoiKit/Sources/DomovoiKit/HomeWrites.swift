import Foundation

// ---------------------------------------------------------------------------
// Home's four writes and their toasts — the port of `HomeIo.kt:45-124`
// (read at `6859b1f7`), map §4 and its strings §6.7.
//
// **All four are device tier, and a refusal raises the pairing screen the
// ordinary way.** That path is part 1's: ``DomovoiClient/raw(method:path:…)``
// sets `pairing` on a device-token refusal and the shell watches it, so every
// call here goes through the tracked verbs and none of them inspects or
// re-raises pairing itself. Going around it is how a screen ends up with its
// own idea of whether this phone is paired.
// ---------------------------------------------------------------------------

extension HomeApi {

    /// pause / resume / stop one room — the Music page's transport
    /// (`HomeApi.roomAction`, `HomeIo.kt:46`).
    ///
    /// `POST /api/music/<verb>/<room>`, with the room **percent-encoded and
    /// `+` turned back into `%20`**. That second step is not decoration:
    /// Java's `URLEncoder` is a *form* encoder, so it writes a space as `+`,
    /// which in a path segment is a literal plus sign and would address a
    /// room that does not exist. `addingPercentEncoding` with a path-segment
    /// character set gets there directly, and ``encodedRoomPathSegment(_:)``
    /// is where that is spelt out and tested.
    public static func roomAction(
        _ client: DomovoiClient,
        room: String,
        verb: String
    ) async throws(DomovoiError) {
        try await client.send(
            method: "POST",
            path: "/api/music/\(verb)/\(encodedRoomPathSegment(room))",
            body: DomovoiClient.emptyObject
        )
    }

    /// Stop every room in `rooms` at once; each succeeds or fails on its own
    /// (`HomeApi.stopRooms`, `HomeIo.kt:68`).
    ///
    /// **One POST per room, concurrently** — Kotlin's `async`/`awaitAll`, here
    /// a `TaskGroup`. Sequentially would mean a six-room house waiting six
    /// round trips with the whole batch's rooms disabled, and the first
    /// failure would decide when the rest were even attempted.
    ///
    /// The result keeps the rooms that stopped **and** the failures with their
    /// errors, because ``stopAllToast(total:outcome:)`` says how many of how
    /// many and quotes the first failure's own words.
    ///
    /// Cancellation is not caught: Kotlin rethrows `CancellationException`
    /// explicitly before its catch-all, and Swift's structured concurrency
    /// does the equivalent by not having a catch-all here — a cancelled batch
    /// is not a batch of failures.
    public static func stopRooms(_ client: DomovoiClient, rooms: [String]) async -> StopOutcome {
        await withTaskGroup(of: (String, DomovoiError?).self) { group in
            for room in rooms {
                group.addTask {
                    do {
                        try await roomAction(client, room: room, verb: "stop")
                        return (room, nil)
                    } catch let error as DomovoiError {
                        return (room, error)
                    } catch {
                        // Unreachable: `roomAction` throws `DomovoiError` and
                        // nothing else. Spelt out because Swift does not
                        // narrow a caught type inside a task-group child
                        // closure, and mapped to a failure rather than to nil
                        // so that an error nobody expected still counts as a
                        // room that did not stop.
                        return (room, .unreachable(error.localizedDescription))
                    }
                }
            }
            var stopped: [String] = []
            var failed: [(room: String, error: DomovoiError)] = []
            for await (room, error) in group {
                if let error { failed.append((room, error)) } else { stopped.append(room) }
            }
            // The group yields in completion order, which is not the caller's
            // order and is not stable between runs. Only the counts and the
            // first failure's text reach a person, but a test that compares
            // lists needs them settled, and a toast that names a room should
            // not name a different one on a re-run.
            return StopOutcome(
                stopped: stopped.sorted(),
                failed: failed.sorted { $0.room < $1.room }
            )
        }
    }

    /// `DELETE /api/timers/<id>`, which also reaches a timer set with no room
    /// (`HomeApi.cancelTimer`, `HomeIo.kt:52`).
    ///
    /// The **404 is not a failure**: it means the timer fired, or somebody
    /// else cancelled it, between the read and the tap. So it has an outcome
    /// of its own and a toast of its own, and it is why the double-tap guard
    /// matters — see ``TimerBook/beginCancel(_:)``.
    public static func cancelTimer(
        _ client: DomovoiClient,
        timer: HomeTimer,
        shared: Bool
    ) async -> CancelOutcome {
        do {
            try await client.send(method: "DELETE", path: "/api/timers/\(timer.id)")
            return .cancelled(HomeToastStrings.cancelledPrefix + timerNoun(timer, shared: shared))
        } catch {
            if error.failure?.status == 404 { return .gone(HomeToastStrings.alreadyFinished) }
            return .failed(failureText("cancel", error))
        }
    }

    /// Say something in every room; the answer lists the rooms that **took**
    /// it (`HomeApi.announceAll`, `HomeIo.kt:88`).
    ///
    /// `POST /api/satellites/announce-all` with `{"message": …}`. The return
    /// is `announced_to`, and reading it rather than the status code is the
    /// point: see ``announceToast(delivered:onlineCount:)``.
    public static func announceAll(
        _ client: DomovoiClient,
        message: String
    ) async throws(DomovoiError) -> [String] {
        let body = try? JSONSerialization.data(withJSONObject: ["message": message])
        return try await client.post(
            "/api/satellites/announce-all",
            body: body ?? DomovoiClient.emptyObject,
            as: HomeAnnounceResult.self
        ).announcedTo
    }
}

/// `enc(s)` (`HomeIo.kt:90`): `URLEncoder.encode(s, "UTF-8").replace("+", "%20")`.
///
/// A room id is a person's words — `front room`, `Mum's office` — and it goes
/// in a **path segment**, so everything a path segment cannot carry has to be
/// escaped and a space must be `%20` and never `+`.
///
/// `.urlPathAllowed` leaves `/`, `?`, `#`, `&` and `+` alone, each of which
/// would change which URL this is, so the allowed set is spelt out instead:
/// unreserved characters only, which is a superset of what Java's form
/// encoder leaves alone once its `+` is fixed. A nil from
/// `addingPercentEncoding` is impossible for this character set, and the
/// fallback is the raw string rather than a crash.
public func encodedRoomPathSegment(_ room: String) -> String {
    let unreserved = CharacterSet(
        charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    )
    return room.addingPercentEncoding(withAllowedCharacters: unreserved) ?? room
}

/// `POST /api/satellites/announce-all`'s answer (`HomeAnnounceResult`,
/// `HomeModels.kt:210`).
public struct HomeAnnounceResult: Equatable, Sendable, Decodable {

    public let announcedTo: [String]

    public init(announcedTo: [String] = []) {
        self.announcedTo = announcedTo
    }

    enum CodingKeys: String, CodingKey {
        case announcedTo = "announced_to"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        announcedTo = try c.decodeIfPresent([String].self, forKey: .announcedTo) ?? []
    }
}

/// How a cancel went, with the toast that says so (`CancelOutcome`,
/// `HomeIo.kt:94`).
public enum CancelOutcome: Equatable, Sendable {

    /// The server confirmed. **Only this one keeps the optimistic mark** in
    /// ``TimerBook/cancelled`` — see ``TimerBook/settleCancel(_:outcome:)``.
    case cancelled(String)

    /// A 404: it fired, or somebody else cancelled it, first.
    case gone(String)

    case failed(String)

    public var toast: String {
        switch self {
        case .cancelled(let text), .gone(let text), .failed(let text): return text
        }
    }
}

/// The result of a `stop all` batch (`StopOutcome`, `HomeIo.kt:101`).
public struct StopOutcome: Equatable, Sendable {

    public let stopped: [String]
    public let failed: [(room: String, error: DomovoiError)]

    public init(stopped: [String] = [], failed: [(room: String, error: DomovoiError)] = []) {
        self.stopped = stopped
        self.failed = failed
    }

    public static func == (lhs: StopOutcome, rhs: StopOutcome) -> Bool {
        lhs.stopped == rhs.stopped
            && lhs.failed.count == rhs.failed.count
            && zip(lhs.failed, rhs.failed).allSatisfy { $0.room == $1.room && $0.error == $1.error }
    }
}

/// `stopAllToast(total, outcome)` (`HomeIo.kt:106`): `stopped 3 rooms`,
/// `stopped 2 of 3 rooms · stop failed: …`, or the failure alone.
///
/// Note the three branches and which number each uses. The partial line
/// counts `total - failed.count` and **not** `stopped.count`: they are the
/// same number here, and the Kotlin's arithmetic is kept so that a future
/// batch that stops reporting every room still says something consistent.
/// When everything failed there is no count at all — just the server's own
/// words, because "stopped 0 of 3" is a sentence about success.
public func stopAllToast(total: Int, outcome: StopOutcome) -> String {
    guard let first = outcome.failed.first else {
        return HomeToastStrings.stoppedPrefix + plural(total, HomeToastStrings.room)
    }
    let said = failureText("stop", first.error)
    guard outcome.failed.count < total else { return said }
    return HomeToastStrings.stoppedPrefix
        + "\(total - outcome.failed.count)\(HomeToastStrings.ofSeparator)\(total)\(HomeToastStrings.roomsSuffix)"
        + HomeToastStrings.middot + said
}

/// `announceToast(delivered, onlineCount)` (`HomeIo.kt:116`).
///
/// **It reads the rooms that took it, not the 200.** The Kotlin's reason, kept
/// verbatim because it is the whole justification for the type of this
/// function: "a 200 can still mean dead connections under the server's
/// active-sessions map". The server queues the announcement and answers
/// successfully; whether a room was still on the other end of its socket is a
/// different fact, and `announced_to` is the only place it is reported.
///
/// So: nothing took it → say so, and say the likely reason; fewer than are
/// online → name them; otherwise `broadcasted to 3 rooms`. Note
/// **`broadcasted`**, not `broadcast` — it is the Kotlin's word and this
/// function is not the place to correct anybody's grammar.
public func announceToast(delivered: [String], onlineCount: Int) -> String {
    if delivered.isEmpty { return HomeToastStrings.noRoomsAccepted }
    if delivered.count < onlineCount {
        return HomeToastStrings.broadcastPartialPrefix
            + "\(delivered.count)/\(onlineCount)"
            + HomeToastStrings.reachedPrefix
            + delivered.joined(separator: ", ")
            + HomeToastStrings.reachedSuffix
    }
    return HomeToastStrings.broadcastedToPrefix + plural(delivered.count, HomeToastStrings.room)
}

/// What the announce button does before it sends anything
/// (`onAnnounce`, `HomeScreen.kt:354`).
///
/// "Explicit feedback on the no-op branches (the web Broadcast's rule): a
/// silent bail reads as a broken button." So an empty message and a house
/// with nothing online each get a toast of their own rather than nothing
/// happening, and a send already in flight does nothing at all — the button
/// is disabled in that state anyway, and a second toast about it would be
/// noise.
public enum AnnounceAttempt: Equatable, Sendable {
    case toast(String)
    case send(String)
    case nothing
}

/// The branch order is the Kotlin's: the empty message is checked **before**
/// the online count, so a person who taps send with an empty field is told to
/// type something rather than told the house is empty.
public func announceAttempt(message: String, onlineCount: Int, sending: Bool) -> AnnounceAttempt {
    let trimmed = message.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.isEmpty { return .toast(HomeToastStrings.typeAMessageFirst) }
    if onlineCount == 0 { return .toast(HomeToastStrings.noRoomsConnected) }
    if sending { return .nothing }
    return .send(trimmed)
}

// MARK: - the strings

/// Home's toasts, verbatim from `ui/screens/home/` (map §6.7). Pinned against
/// the Kotlin on disk by `ShellStringsTests`.
///
/// The two Android-only ones are **not** here: `couldn't open the alarms &
/// reminders settings` and `couldn't open the notification settings` are the
/// failures of two intents that do not exist on iOS, and neither does the
/// alert-hint row that offers them (owner decision, 2026-10-30).
/// `no browser found to open the dashboard` is not here either: it belongs to
/// a Health row's `fix`, which is Settings → Health's and not Home's.
public enum HomeToastStrings {

    /// `HomeScreen.kt:358`.
    public static let typeAMessageFirst = "type a message first"

    /// `HomeScreen.kt:359`. **U+2014 EM DASH, one space each side.**
    public static let noRoomsConnected = "no rooms connected — nothing to broadcast to"

    /// `cancelled ` plus ``timerNoun(_:shared:)`` (`HomeIo.kt:54`).
    public static let cancelledPrefix = "cancelled "

    /// The 404 case (`HomeIo.kt:59`).
    public static let alreadyFinished = "that one already finished"

    /// `stopped ` plus a ``plural(_:_:_:)`` of ``room`` (`HomeIo.kt:108`).
    public static let stoppedPrefix = "stopped "

    /// The word ``plural(_:_:_:)`` is given, so `stopped 1 room` is right and
    /// `stopped 3 rooms` is too — unlike `stop $count rooms?`, which is
    /// hard-coded plural (map §11.5).
    public static let room = "room"

    /// `stopped 2 of 3 rooms` in pieces, because two counts sit in it
    /// (`HomeIo.kt:111`).
    public static let ofSeparator = " of "
    public static let roomsSuffix = " rooms"

    /// Between the count and the failure's own words (`HomeIo.kt:111`).
    /// **U+00B7, one space each side.**
    public static let middot = " · "

    /// `HomeIo.kt:120` — the parentheses and the question mark inside them are
    /// part of it.
    public static let noRoomsAccepted = "broadcast queued but no rooms accepted it (dead connections?)"

    /// `broadcast partial — 2/3 reached (kitchen, office)` in pieces
    /// (`HomeIo.kt:122`). **U+2014 EM DASH.**
    public static let broadcastPartialPrefix = "broadcast partial — "
    public static let reachedPrefix = " reached ("
    public static let reachedSuffix = ")"

    /// `broadcasted to ` plus a ``plural(_:_:_:)`` of ``room``
    /// (`HomeIo.kt:123`). **`broadcasted`, not `broadcast`.**
    public static let broadcastedToPrefix = "broadcasted to "
}
