import Foundation

/*
 * "Continue from the kitchen" and the plan a book starts from —
 * `audiobooks/BookPositions.kt:224-258` (`ContinueOffer`, `ContinueRules`)
 * and `audiobooks/BookPlay.kt:79-115` (`StartPlan`, `BookStart`).
 *
 * In a file of their own, apart from `AudiobookPositions.swift`, because the
 * two pure rules here — which place is offered, and how its time is spelt —
 * are what a UI case's wording depends on, and they want their own tests.
 */

/// Run `work`, giving up after `ms`. Nil for a timeout **and** for a
/// failure, which is the one shape every caller here treats the same —
/// Kotlin's `runCatching { withTimeoutOrNull(…) { … } }.getOrNull()`.
///
/// A helper rather than two copies of the task group: the bound is the thing
/// that keeps a server out of reach from holding up play, and a second copy
/// is a second chance to write a different number into it.
///
/// ### The `sleep` a test must NOT pass
///
/// `bounded` returns **whichever child lands first**, so a `sleep` that
/// returns immediately is a timeout that always wins — or rather wins
/// whenever the scheduler runs it second, which is a coin toss. That is what
/// `sleep: { _ in }` in a rig buys: a case that passes four times in eight.
/// It cost `AudiobooksModelTests.testOpeningABookTakesTheServersPlaceWhenOnlyItHasOne`
/// an intermittent red measured on the Mac on 2026-11-04 — 4 of 8 runs alone
/// in the process — and a round of misdiagnosis before that, in which the
/// flake was blamed on work leaking between test classes.
///
/// ``heldShut`` is the sleep a case that does not want the bound to fire
/// passes instead. It lives here rather than in a test file so that the next
/// rig finds it instead of writing `{ _ in }` again.
enum BookTimeout {

    /// A `sleep` the caller can hold shut: it yields rather than waiting on a
    /// clock, so a case costs no real time, and it ends only on cancellation
    /// — which is what ``bounded(ms:sleep:_:)`` does to the losing child.
    ///
    /// **Bounded, not infinite.** `yields` is a ceiling far above anything a
    /// case waits for, and reaching it lets the timeout fire. An unbounded
    /// loop would turn a `work` that never finishes into a run that hangs
    /// forever rather than one that fails, and a hung train is worse evidence
    /// than a red one.
    ///
    /// ### Test-only API in a shipping file, and what holds it
    ///
    /// Nothing in the app calls this: every caller is a test rig, and it
    /// lives here rather than beside them for the reason above — so that the
    /// next rig finds it. Its own rules are measured in `BookTimeoutTests`,
    /// which is where review 3 of the 2026-11-03 batch asked for them: the
    /// case this helper fixed is intermittent **by nature**, so reverting
    /// this body to `{ _ in }` made `AudiobooksModelTests` red only 2 of 6
    /// runs. A guard that fires a third of the time is one the next reader
    /// deletes, so `BookTimeoutTests` asserts the two things that fail every
    /// time instead: that a `work` which only yields beats the bound, and
    /// that this sleep does not return until it is cancelled.
    static func heldShut(yields: Int = 50_000) -> @Sendable (Int64) async throws -> Void {
        { _ in
            for _ in 0..<yields {
                try Task.checkCancellation()
                await Task.yield()
            }
        }
    }

    static func bounded<Value: Sendable>(
        ms: Int64,
        sleep: @Sendable @escaping (Int64) async throws -> Void,
        _ work: @Sendable @escaping () async throws -> Value
    ) async -> Value? {
        await withTaskGroup(of: Value?.self) { group in
            group.addTask { try? await work() }
            group.addTask {
                try? await sleep(ms)
                return nil
            }
            let first = await group.next() ?? nil
            group.cancelAll()
            return first
        }
    }
}

/// The "continue from the kitchen" offer. `BookPositions.kt:225`.
public struct BookContinueOffer: Sendable, Equatable {
    public let label: String
    public let positionSec: Double

    /// A room, which is what puts the definite article in the sentence.
    public let isRoom: Bool

    public init(label: String, positionSec: Double, isRoom: Bool) {
        self.label = label
        self.positionSec = positionSec
        self.isRoom = isRoom
    }
}

/// `ContinueRules` (`BookPositions.kt:227`), pure and ported whole.
public enum BookContinueRules {

    /// A room (or another device) counts as further only past this lead
    /// (`:229`). Sixty seconds, so a place a second ahead of this phone's —
    /// the same listening session, written by whichever client saved last —
    /// is not offered as somewhere else to carry on from.
    public static let leadSec: Double = 60

    /// The place to offer over the phone's own `mineSec`: of the others more
    /// than ``leadSec`` ahead and naming a room or a device, the one used
    /// most recently, then the furthest. Nil when none got further.
    /// `BookPositions.kt:234`.
    ///
    /// The tie-break order is the Kotlin's `compareBy(updatedAt, position)`
    /// and the first of equal maxima wins, which `max(by:)` does as
    /// `maxWithOrNull` does. A row with an unreadable timestamp sorts below
    /// every readable one rather than above them (`Long.MIN_VALUE`).
    public static func pick(mineSec: Double, others: [OtherBookPosition]) -> BookContinueOffer? {
        let ahead = others.filter {
            $0.positionSec > mineSec + leadSec && ($0.room != nil || $0.deviceLabel != nil)
        }
        let best = ahead.max { left, right in
            let leftAt = isoMs(left.updatedAt) ?? Int64.min
            let rightAt = isoMs(right.updatedAt) ?? Int64.min
            if leftAt != rightAt { return leftAt < rightAt }
            return left.positionSec < right.positionSec
        }
        guard let best else { return nil }
        let room = best.room.flatMap { ServerCredentials.isBlank($0) ? nil : $0 }
        return BookContinueOffer(
            label: room ?? best.deviceLabel ?? "",
            positionSec: best.positionSec,
            isRoom: room != nil
        )
    }

    /// `"continue from the kitchen (2 h 13 m)"`. `BookPositions.kt:244`.
    ///
    /// Assembled from pieces, as the Kotlin assembles it: the definite
    /// article only for a room, because "continue from the Sam's Pixel" is
    /// not a sentence.
    public static func text(_ offer: BookContinueOffer) -> String {
        AudiobooksStrings.continueFrom
            + (offer.isRoom ? AudiobooksStrings.theRoom : "")
            + offer.label
            + " (" + hm(offer.positionSec) + ")"
    }

    /// `"2 h 13 m"`, `"13 m"`, `"40 s"`. `BookPositions.kt:248`.
    ///
    /// **Not ``fmtBigDur(_:)``**, which spells the same span `2h 13m` with no
    /// spaces (`Format.swift:88`). Two wordings, two functions: porting this
    /// to the other one would quietly change a sentence the owner reads.
    public static func hm(_ sec: Double) -> String {
        let total = Int64(max(0, sec.isFinite ? sec : 0))
        let h = total / 3600
        let m = (total % 3600) / 60
        if h > 0 { return "\(h) h \(m) m" }
        if m > 0 { return "\(m) m" }
        return "\(total) s"
    }
}

/// How a book starts: where, at what speed, and what to offer.
/// `StartPlan` (`BookPlay.kt:80`).
public struct BookStartPlan: Sendable, Equatable {

    public let bookID: Int64
    public let title: String
    public let positionSec: Double
    public let speed: Double
    public let offer: BookContinueOffer?

    public init(
        bookID: Int64,
        title: String,
        positionSec: Double,
        speed: Double,
        offer: BookContinueOffer?
    ) {
        self.bookID = bookID
        self.title = title
        self.positionSec = positionSec
        self.speed = speed
        self.offer = offer
    }

    /// Worth asking before play: the phone has a place in the book, or
    /// somewhere else got further (`BookPlay.kt:87`). Below five seconds and
    /// with no offer the book just starts.
    public var asks: Bool { positionSec > 5 || offer != nil }
}

/// `BookStart` (`BookPlay.kt:90`).
public enum BookStartPlanner {

    /// How long the other places are waited for (`OTHERS_TIMEOUT_MS`,
    /// `BookPlay.kt:114`).
    public static let othersTimeoutMs: Int64 = 3_000

    /// Where `book` starts: the phone's one place, synced with the server's
    /// slot when connected (``BookPositionSync/open(bookID:server:)``), and
    /// whether a room got further.
    ///
    /// Both reads go at once and each is bounded, so a server out of reach
    /// costs a few seconds at most before play. The other places are read
    /// **only** from the book's own server (`BookPlay.kt:99`), and a server
    /// too old to have `/positions` is a swallowed failure and no offer — not
    /// a refusal to play.
    public static func plan(
        book: AudiobookRow,
        server: BookServerKey,
        sync: BookPositionSync,
        remote: (any BookPositionRemote)?,
        sleep: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        }
    ) async -> BookStartPlan {
        let bookID = book.id
        // The place is read through the sync, which is the only thing that
        // may decide the server's slot wins; the offer is read beside it.
        async let mine = sync.open(bookID: bookID, server: server)
        async let elsewhere = others(bookID: bookID, server: server, remote: remote, sleep: sleep)
        let place = await mine
        let others = (await elsewhere) ?? []
        return BookStartPlan(
            bookID: bookID,
            title: book.displayTitle,
            positionSec: place.positionSec,
            speed: place.speed,
            offer: BookContinueRules.pick(mineSec: place.positionSec, others: others)
        )
    }

    /// This listener's places elsewhere, or nil when there are none to ask
    /// for: no remote (no device id, DECISIONS §3), a remote pointed at
    /// another house, or a read that failed or took too long.
    private static func others(
        bookID: Int64,
        server: BookServerKey,
        remote: (any BookPositionRemote)?,
        sleep: @Sendable @escaping (Int64) async throws -> Void
    ) async -> [OtherBookPosition]? {
        guard let remote, remote.server.isSame(server) else { return nil }
        return await BookTimeout.bounded(ms: othersTimeoutMs, sleep: sleep) {
            try await remote.others(bookID)
        }
    }
}
