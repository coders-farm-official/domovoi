import Foundation
import Synchronization

/// What the server's answers have said about this phone's household token,
/// and what the phone does about it.
///
/// A port of `android/.../net/TokenHealth.kt` (2026-10-17, a server that no
/// longer knows this phone). The household token is kept per server address.
/// A server reinstalled at the same address, replaced by a new machine, or
/// whose admin rotated the token no longer accepts the token this phone
/// holds for it. Before this, the app kept sending it on every poll — and
/// the server counts wrong tokens per address (5 free, then a doubling wait
/// capped at 5 min: `DEVICE_TOKEN_BACKOFF` in domovoi/admin_auth.py) and
/// answers `429` while that wait runs, so the phone renewed its own lockout
/// every time the window opened and showed "offline" with a page of raw
/// errors.
///
/// Two facts, learnt only from requests that carried the token:
///
///  * **Rejected.** The server answered a device-tier `401`/`403` whose body
///    is its OWN refusal (``isServerDeviceRefusal(status:body:)``) to a
///    request that carried this token. The server's body says the same for
///    "no token" and "wrong token", but this side knows which it sent, so no
///    server change is needed to tell them apart. The phone stops sending
///    that token at once — requests go out bare, like an unpaired phone's,
///    which the server does not count — and the shell shows one card
///    offering to pair again (``TokenTrouble``). Kept in the ``RejectedBook``
///    so a restart does not try it again. Cleared by any new token for that
///    server, by forgetting the server, or by the person asking to check
///    again.
///  * **Waiting.** The server answered `429` on the device tier (its detail
///    names the header; a `429` for anything else is not about the token).
///    Until then, nothing that would carry the token is sent to that server.
///    Kept in memory: after a restart the first request learns it again, and
///    a throttled request costs nothing on the server.
///
/// `503` — the server could not check credentials just now — and everything
/// else teach it nothing. That is deliberate: a database blip must not cost
/// a good token.
///
/// Both facts are keyed by ``ServerCredentials/normalize(_:)``, the key the
/// token itself is kept under.
///
/// **Why a lock and not an actor.** As ``HouseholdLock``: the redirect
/// delegate is a synchronous platform callback that cannot `await`, and it
/// has to ask both of these questions before it puts the token on a hop.
public final class TokenHealth: Sendable {

    /// Where "this server refused the token this phone holds for it" is kept.
    /// The persistent one lands with the shell's settings store (I1-C); this
    /// batch has nothing to persist into yet.
    public protocol RejectedBook: Sendable {
        func isTokenRejected(_ url: String) -> Bool
        func markTokenRejected(_ url: String)

        /// What `Prefs.clearTokenRejected` does: the person asked to try the
        /// old token once more, or a new one arrived.
        ///
        /// A requirement and **not** an extension with a no-op default,
        /// added when ``AppContainer`` gave the protocol a second
        /// implementation (I1-C part 1). ``TokenHealth`` itself never calls
        /// it — the only caller is the token-trouble card's `try the old
        /// token again`, which is part 2's — and a conformance that silently
        /// did nothing would leave that button unable to do what it says,
        /// with nothing to notice it. A missing method is a compile error
        /// instead.
        func clear(_ url: String)
    }

    /// For tests and for a client with no settings store.
    public final class InMemoryBook: RejectedBook {
        private let rejected = Mutex<Set<String>>([])
        public init() {}
        public func isTokenRejected(_ url: String) -> Bool {
            rejected.withLock { $0.contains(ServerCredentials.normalize(url)) }
        }
        public func markTokenRejected(_ url: String) {
            rejected.withLock { _ = $0.insert(ServerCredentials.normalize(url)) }
        }
        /// What `Prefs.clearTokenRejected` does: the person asked to try the
        /// old token once more, or a new one arrived.
        public func clear(_ url: String) {
            rejected.withLock { _ = $0.remove(ServerCredentials.normalize(url)) }
        }
    }

    /// When a `429` carries no usable `Retry-After`.
    public static let defaultWaitMs: Int64 = 60_000

    /// The longest wait a server can impose this way (its own cap is 5 min);
    /// anything longer is clamped, so a broken header cannot park the app for
    /// hours.
    public static let maxWaitMs: Int64 = 15 * 60_000

    private let book: RejectedBook
    private let clock: @Sendable () -> Int64
    private let log: @Sendable (String) -> Void
    private let waitsUntil = Mutex<[String: Int64]>([:])

    /// `clock` is monotonic milliseconds. Its default is Darwin's
    /// `CLOCK_MONOTONIC`, which `man 3 clock_gettime` on the build Mac
    /// (macOS 26.4.1) describes as continuing "to increment while the system
    /// is asleep" — so it is the counterpart of `SystemClock.elapsedRealtime`,
    /// which Android passes in, and not of `CLOCK_UPTIME_RAW`, which the same
    /// page says does not. That matters: a wait measured on a clock that
    /// stops during sleep would outlast the server's by however long the
    /// phone slept, and the phone would sit waiting after the server had
    /// forgotten all about it.
    public init(
        book: RejectedBook = InMemoryBook(),
        clock: @Sendable @escaping () -> Int64 = TokenHealth.monotonicMs,
        log: @Sendable @escaping (String) -> Void = { _ in }
    ) {
        self.book = book
        self.clock = clock
        self.log = log
    }

    public func isRejected(_ url: String) -> Bool { book.isTokenRejected(url) }

    /// How long `url` still makes this phone wait, in ms; 0 when it does not.
    public func waitMs(_ url: String) -> Int64 {
        let key = ServerCredentials.normalize(url)
        guard let until = waitsUntil.withLock({ $0[key] }) else { return 0 }
        return max(0, until - clock())
    }

    /// Every address that is still making this phone wait, with how long is
    /// left. What I1-C's card reads; entries that have run out are not here.
    public func activeWaits() -> [String: Int64] {
        let now = clock()
        return waitsUntil.withLock { $0 }
            .compactMapValues { until in until > now ? until - now : nil }
    }

    /// Learn from the answer to a request to the server at `url` that carried
    /// `sent` as its household token. `current` is the token held for `url`
    /// now: a refusal of a token the person has replaced in the meantime (a
    /// re-pair racing a request in flight) says nothing about the new one.
    ///
    /// Kotlin takes an OkHttp `Response` and peeks 4,096 bytes of it. iOS has
    /// the whole body by the time the call returns, so the caller passes what
    /// it read; ``DomovoiClient`` passes the same 4,096-byte cap so a server
    /// that answers a refusal with a megabyte of HTML is read the same way on
    /// both phones.
    public func observe(
        url: String,
        sent: String,
        current: String?,
        status: Int,
        body: String,
        retryAfter: String?
    ) {
        switch status {
        case 401, 403:
            guard isServerDeviceRefusal(status: status, body: body) else { return }
            let held = current?.trimmingCharacters(in: .whitespacesAndNewlines)
            guard held == sent.trimmingCharacters(in: .whitespacesAndNewlines) else { return }
            guard !book.isTokenRejected(url) else { return }
            book.markTokenRejected(url)
            log("\(ServerCredentials.address(url)) refused this phone's household token (\(status)); it is no longer sent there")
        case 429:
            guard Self.isDeviceTokenThrottle(body) else { return }
            noteWait(url, waitMs: Self.retryAfterMs(retryAfter))
        default:
            // 503 (the server could not check credentials just now) and
            // anything else: not a verdict about the token, nothing learnt.
            break
        }
    }

    /// The server asked `url`'s callers from this address to wait. The longer
    /// of the old wait and the new one wins, so a second answer inside a wait
    /// can extend it but never shorten it.
    public func noteWait(_ url: String, waitMs: Int64) {
        let key = ServerCredentials.normalize(url)
        let until = clock() + waitMs
        let changed = waitsUntil.withLock { waits -> Bool in
            if until <= (waits[key] ?? 0) { return false }
            waits[key] = until
            return true
        }
        guard changed else { return }
        log("\(ServerCredentials.address(url)) asked this phone to wait \(waitMs / 1000) s before it sends a household token again")
    }

    /// Clears a wait. Not in the Kotlin, which has no call site for it: there
    /// a wait is in memory and the app outlives it. iOS needs it for the same
    /// reason the Kotlin needs `InMemoryBook.clear` — so a test can move past
    /// a wait without moving the clock — and for forgetting a server.
    public func clearWait(_ url: String) {
        waitsUntil.withLock { $0[ServerCredentials.normalize(url)] = nil }
    }

    // ---- pure, and the same on both phones ---------------------------------

    /// A device-tier `429`: the server's detail names the header ("too many
    /// wrong X-Device-Token values — retry in 53s, …"). A login's or a URL
    /// fetch's `429` is about something else.
    public static func isDeviceTokenThrottle(_ body: String?) -> Bool {
        guard let body else { return false }
        return body.range(of: deviceTokenHeader, options: .caseInsensitive) != nil
            || body.range(of: "device token", options: .caseInsensitive) != nil
    }

    /// `Retry-After` in milliseconds: delta-seconds (what Domovoi sends) or
    /// an HTTP date; ``defaultWaitMs`` when absent or unreadable; clamped to
    /// 1 s … ``maxWaitMs``.
    ///
    /// `now` is WALL-CLOCK milliseconds, because an HTTP date can only be
    /// compared with the wall clock — the same split the Kotlin makes between
    /// its monotonic `clock` and its `wallClockMs`.
    public static func retryAfterMs(
        _ header: String?,
        now: @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) }
    ) -> Int64 {
        let value = (header ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let ms: Int64
        if let seconds = Int64(value) {
            ms = seconds * 1000
        } else if let date = rfc1123.date(from: value) {
            ms = Int64(date.timeIntervalSince1970 * 1000) - now()
        } else {
            ms = defaultWaitMs
        }
        return min(max(ms, 1000), maxWaitMs)
    }

    /// RFC 1123, which is what `Retry-After`'s date form is. `en_US_POSIX`
    /// and GMT on purpose: a formatter on the user's locale would read the
    /// month names of whatever language the phone is in, and this header is
    /// always English.
    private static let rfc1123: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "GMT")
        formatter.dateFormat = "EEE, dd MMM yyyy HH:mm:ss zzz"
        return formatter
    }()

    /// Monotonic milliseconds; see ``init(book:clock:log:)`` for why this
    /// clock and not another.
    public static let monotonicMs: @Sendable () -> Int64 = {
        var now = timespec()
        clock_gettime(CLOCK_MONOTONIC, &now)
        return Int64(now.tv_sec) * 1000 + Int64(now.tv_nsec / 1_000_000)
    }
}
