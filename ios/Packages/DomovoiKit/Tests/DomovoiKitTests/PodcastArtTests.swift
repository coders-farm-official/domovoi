import Foundation
import XCTest
@testable import DomovoiKit

/// The one rule on this screen that keeps a request off the network.
///
/// A port of `PodcastArtworkTest`, which is the Kotlin's own unit test of
/// `player/PodcastArtwork.kt`. It is in a file of its own for the reason it
/// has one over there: everything else about a cover is a view, and this is
/// the part where a wrong answer means the phone fetching an image from a
/// podcast directory nobody asked it to contact.
final class PodcastArtTests: XCTestCase {

    /// The shape the server sends since fix B11 (`podcasts.py:20-24`).
    func testAServerPathIsLoaded() {
        XCTAssertEqual(
            "/api/podcasts/subscriptions/12/artwork",
            PodcastArt.path("/api/podcasts/subscriptions/12/artwork")
        )
        XCTAssertEqual(
            "/api/podcasts/discover/artwork/abc123",
            PodcastArt.path("/api/podcasts/discover/artwork/abc123")
        )
    }

    /// **The whole point of the rule.** An older house, or a row written
    /// before B11, can still hand this app the publisher's own URL; loading
    /// it would be the phone going to a podcast directory on a stored
    /// string's say-so.
    func testAnAbsoluteUrlDrawsThePlaceholderInstead() {
        XCTAssertNil(PodcastArt.path("https://cdn.example.com/art/600x600.jpg"))
        XCTAssertNil(PodcastArt.path("http://is1-ssl.mzstatic.com/image/thumb/x.jpg"))
    }

    /// A protocol-relative URL is the shape that *looks* like a path and is
    /// not: `//host/api/x` resolves to another host entirely. It is refused by
    /// the same character comparison, which is why the test is a prefix test
    /// and nothing cleverer.
    func testAProtocolRelativeUrlIsNotAServerPath() {
        XCTAssertNil(PodcastArt.path("//cdn.example.com/api/podcasts/x.jpg"))
        XCTAssertNil(PodcastArt.path("//api/podcasts/x.jpg"))
    }

    /// Anything that is not a `/api/` path at all.
    func testEverythingElseIsThePlaceholder() {
        XCTAssertNil(PodcastArt.path(nil))
        XCTAssertNil(PodcastArt.path(""))
        XCTAssertNil(PodcastArt.path("   "))
        XCTAssertNil(PodcastArt.path("/static/art.png"))
        XCTAssertNil(PodcastArt.path("api/podcasts/x"), "no leading slash is not the route")
        XCTAssertNil(PodcastArt.path("data:image/png;base64,iVBORw0KGgo="))
        XCTAssertNil(PodcastArt.path("file:///etc/passwd"))
    }

    /// Trimmed first: a feed's whitespace reaches the column untouched, and
    /// refusing `" /api/…"` would lose a cover the house really does hold.
    func testSurroundingWhitespaceIsTrimmedBeforeTheTest() {
        XCTAssertEqual(
            "/api/podcasts/subscriptions/3/artwork",
            PodcastArt.path("  /api/podcasts/subscriptions/3/artwork\n")
        )
    }

    /// The two convenience overloads read the same field through the same
    /// rule — so a view cannot reach past it by taking `show.artwork`
    /// directly and getting a different answer.
    func testBothRowTypesGoThroughTheSameRule() {
        let good = PodcastShow(id: 12, artwork: "/api/podcasts/subscriptions/12/artwork")
        XCTAssertEqual("/api/podcasts/subscriptions/12/artwork", PodcastArt.path(for: good))

        let stale = PodcastShow(id: 13, artwork: "https://cdn.example.com/art.jpg")
        XCTAssertNil(PodcastArt.path(for: stale))

        let hit = PodcastDiscovery(artwork: "/api/podcasts/discover/artwork/k", feedURL: "https://a/f")
        XCTAssertEqual("/api/podcasts/discover/artwork/k", PodcastArt.path(for: hit))

        let staleHit = PodcastDiscovery(artwork: "//mzstatic/x.jpg", feedURL: "https://a/f")
        XCTAssertNil(PodcastArt.path(for: staleHit))
    }

    /// The cover **bytes** are held by ``VideoPosterStore``, reused unchanged
    /// rather than copied (DECISIONS.md, "Needs a shared change"). Asserted so
    /// that a later branch replacing the typealias with a second
    /// implementation has to delete this test and say why.
    func testTheCoverStoreIsTheOneThatAlreadyExists() {
        // A typealias and not a wrapper: the states are the same values, so
        // `absent` here settles a cover exactly as it settles a poster —
        // nothing asks the server twice.
        XCTAssertEqual(PodcastArtState.absent, VideoPoster.absent)
        XCTAssertEqual(PodcastArtState.loading, VideoPoster.loading)
        XCTAssertNotEqual(PodcastArtState.failed, VideoPoster.absent)
    }
}
