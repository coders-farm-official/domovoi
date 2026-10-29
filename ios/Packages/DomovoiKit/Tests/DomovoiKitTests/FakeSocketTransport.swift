import Foundation
import XCTest
@testable import DomovoiKit

/// A ``StateSocketTransport`` a test drives by hand.
///
/// It records **the request it was handed** — which is what "what left the
/// phone" means for a socket, because this is the only thing that opens one —
/// and lets the test say when the upgrade opened, when a frame arrived, and
/// how it ended.
///
/// `@unchecked Sendable` with the reason, as the house rule asks: every
/// mutable field lives inside `lock`, the lock is never held across a
/// suspension because nothing here is async, and the links handed out are
/// themselves lock-protected.
final class FakeSocketTransport: StateSocketTransport, @unchecked Sendable {

    /// One socket this transport was asked to open.
    final class Link: StateSocketLink, @unchecked Sendable {

        let request: URLRequest
        private let events: StateSocketEvents
        private let lock = NSLock()
        private var frames: [String] = []
        private var cancelled = false
        /// Which ending call has already been made, if any — see
        /// ``end(_:)``.
        private var ended: String?

        init(request: URLRequest, events: StateSocketEvents) {
            self.request = request
            self.events = events
        }

        /// The text frames the socket sent down this link.
        var sent: [String] { lock.withLock { frames } }
        var isCancelled: Bool { lock.withLock { cancelled } }

        /// What the upgrade carried, read off the request.
        var token: String? { request.value(forHTTPHeaderField: deviceTokenHeader) }
        var path: String { request.url?.path ?? "" }

        func send(_ text: String) { lock.withLock { frames.append(text) } }
        func cancel() { lock.withLock { cancelled = true } }

        // ---- what a test makes happen ------------------------------------

        func reportOpen() { events.opened(self) }
        func deliver(_ text: String) { events.text(text) }

        func fail(
            status: Int? = nil,
            body: String? = nil,
            file: StaticString = #filePath,
            line: UInt = #line
        ) {
            end("failed", file: file, line: line)
            events.failed(status, body)
        }

        func closeFromPeer(file: StaticString = #filePath, line: UInt = #line) {
            end("closed", file: file, line: line)
            events.closed()
        }

        /// **``StateSocketTransport``'s obligation, enforced:** exactly one of
        /// `failed` or `closed` per link, and neither after `cancel()`.
        ///
        /// A fake that let a test report both would let a test pass against a
        /// transport that cannot exist — and the thing it would hide is a
        /// silently halved backoff, because `advance(.lost, attempt:)` does
        /// not bump the attempt. The real transport's `claimFinish()` makes
        /// this impossible; this is where it is **checked**, which is the
        /// difference between a contract and a paragraph.
        private func end(_ what: String, file: StaticString, line: UInt) {
            let trouble = lock.withLock { () -> String? in
                if cancelled { return "\(what) after cancel()" }
                if let ended { return "\(what) after \(ended)" }
                ended = what
                return nil
            }
            if let trouble {
                XCTFail(
                    "one end per link: \(trouble). StateSocketTransport promises exactly one of "
                        + "failed/closed per link and none after cancel().",
                    file: file,
                    line: line
                )
            }
        }
    }

    private struct State {
        var links: [Link] = []
        /// Report `opened` from inside `open`, before it has returned — the
        /// ordering that used to lose the subscribe-all frame.
        var opensImmediately = false
    }

    private let lock = NSLock()
    private var state = State()

    /// Set on every `open`, so a test can wait for one with the bounded
    /// `settle(for:what:)` rather than a spin loop.
    let opened = Shared<Bool>(false)

    init(opensImmediately: Bool = false) {
        state.opensImmediately = opensImmediately
    }

    var links: [Link] { lock.withLock { state.links } }
    var opens: Int { links.count }
    var last: Link? { links.last }

    /// Every path this transport was asked to open. Android's assertion is
    /// `fake.requests.none { it.path.startsWith("/ws/state") }`; this is the
    /// same list.
    var paths: [String] { links.map(\.path) }

    func open(_ request: URLRequest, reporting events: StateSocketEvents) -> any StateSocketLink {
        let link = Link(request: request, events: events)
        let now = lock.withLock { () -> Bool in
            state.links.append(link)
            return state.opensImmediately
        }
        opened.set(true)
        if now { link.reportOpen() }
        return link
    }
}
