import XCTest
@testable import DomovoiKit

/// Library covers asked for at a size bucket (`track_cover`'s `size`):
/// the smallest bucket whose edge covers the drawn size at 3x, `?v=` kept,
/// the bucket part of the store's key, the lock screen at `xl`, and a
/// podcast's art left alone.
@MainActor
final class MusicCoverSizeTests: XCTestCase {

    nonisolated static let picture = Data([0x89, 0x50, 0x4E, 0x47] + Array(repeating: 0x20, count: 200))
    nonisolated static let thumbnail = Data([0x52, 0x49, 0x46, 0x46] + Array(repeating: 0x21, count: 100))

    override func setUp() {
        super.setUp()
        MusicCoverVersions.shared.forget()
    }

    override func tearDown() {
        MusicCoverVersions.shared.forget()
        super.tearDown()
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    // MARK: - the bucket

    func testTheBucketEdgesAreTheServers() {
        XCTAssertEqual([160, 320, 512, 768], MusicCoverSize.allCases.map(\.edge))
        XCTAssertEqual(["s", "m", "l", "xl"], MusicCoverSize.allCases.map(\.rawValue))
    }

    /// The smallest bucket whose edge is at least points x 3.
    func testTheSmallestBucketCoveringTheDrawnSizeAtThreeX() {
        XCTAssertEqual(.s, MusicCoverSize.fitting(points: 36), "the mini player")
        XCTAssertEqual(.s, MusicCoverSize.fitting(points: 40), "a library row")
        XCTAssertEqual(.s, MusicCoverSize.fitting(points: 53), "159 px fits 160")
        XCTAssertEqual(.m, MusicCoverSize.fitting(points: 54), "162 px does not fit 160")
        XCTAssertEqual(.m, MusicCoverSize.fitting(points: 56), "a room card")
        XCTAssertEqual(.m, MusicCoverSize.fitting(points: 96), "the track sheet")
        XCTAssertEqual(.m, MusicCoverSize.fitting(points: 106))
        XCTAssertEqual(.l, MusicCoverSize.fitting(points: 107))
        XCTAssertEqual(.l, MusicCoverSize.fitting(points: 112), "the player's head")
        XCTAssertEqual(.l, MusicCoverSize.fitting(points: 170))
        XCTAssertEqual(.xl, MusicCoverSize.fitting(points: 171))
        XCTAssertEqual(.xl, MusicCoverSize.fitting(points: 400), "anything larger is the largest bucket")
        XCTAssertEqual(.s, MusicCoverSize.fitting(points: 80, scale: 2), "an explicit scale is honoured")
        XCTAssertEqual(.xl, MusicCoverSize.nowPlaying)
    }

    // MARK: - the path

    func testThePathAddsTheSizeAndKeepsTheVersion() {
        XCTAssertEqual("/api/music/library/12/cover", PlayerArtwork.libraryPath(trackID: 12, coverV: nil, size: nil))
        XCTAssertEqual("/api/music/library/12/cover?size=s", PlayerArtwork.libraryPath(trackID: 12, coverV: nil, size: .s))
        XCTAssertEqual("/api/music/library/12/cover?v=3", PlayerArtwork.libraryPath(trackID: 12, coverV: 3, size: nil))
        XCTAssertEqual("/api/music/library/12/cover?v=3&size=m", PlayerArtwork.libraryPath(trackID: 12, coverV: 3, size: .m))
        XCTAssertEqual("/api/music/library/12/cover?v=3", PlayerArtwork.libraryPath(trackID: 12, coverV: 3),
                       "the unsized builder is unchanged")
    }

    func testTheKeysPathCarriesItsSize() {
        XCTAssertEqual("/api/music/library/7/cover?v=2&size=l", MusicCoverKey(trackID: 7, version: 2, size: .l).path)
        XCTAssertEqual("/api/music/library/7/cover?size=xl", MusicCoverKey(trackID: 7, size: .xl).path)
        XCTAssertEqual("/api/music/library/7/cover", MusicCoverKey(trackID: 7).path, "no size is the original")
    }

    func testASizedPathIsReadBack() {
        let both = PlayerArtwork.coverPathPieces("/api/music/library/12/cover?v=4&size=m")
        XCTAssertEqual(12, both?.trackID)
        XCTAssertEqual(4, both?.version)
        XCTAssertEqual(.m, both?.size)
        let reversed = PlayerArtwork.coverPathPieces("/api/music/library/12/cover?size=s&v=5")
        XCTAssertEqual(5, reversed?.version)
        XCTAssertEqual(.s, reversed?.size)
        XCTAssertEqual(4, PlayerArtwork.coverPathParts("/api/music/library/12/cover?v=4&size=xl")?.version,
                       "a size after the version does not hide the version")
        XCTAssertNil(PlayerArtwork.coverPathPieces("/api/music/library/12/cover?size=huge")?.size)
    }

    /// The lock screen's helper: library paths only, version kept.
    func testSizedTouchesOnlyALibraryCover() {
        XCTAssertEqual("/api/music/library/12/cover?size=xl", PlayerArtwork.sized("/api/music/library/12/cover", .xl))
        XCTAssertEqual("/api/music/library/12/cover?v=2&size=xl", PlayerArtwork.sized("/api/music/library/12/cover?v=2", .xl))
        XCTAssertEqual("/api/music/library/12/cover?v=2&size=xl",
                       PlayerArtwork.sized("/api/music/library/12/cover?v=2&size=s", .xl), "a size is replaced, not doubled")
        XCTAssertEqual("/api/podcasts/art/4", PlayerArtwork.sized("/api/podcasts/art/4", .xl))
        XCTAssertEqual("/api/audiobooks/9/cover", PlayerArtwork.sized("/api/audiobooks/9/cover", .xl))
        XCTAssertEqual("file:///x.jpg", PlayerArtwork.sized("file:///x.jpg", .xl))
        XCTAssertNil(PlayerArtwork.sized(nil, .xl))
    }

    /// A newer version heard keeps the size the path was asked at.
    func testAVersionMoveKeepsTheSize() {
        let versions = MusicCoverVersions()
        versions.received(StateEvent(
            type: MusicCoverVersions.event,
            frame: #"{"type":"library.cover.changed","data":{"tracks":[{"id":12,"cover_v":6}]}}"#
        ), server: "")
        XCTAssertEqual("/api/music/library/12/cover?v=6&size=s", versions.versioned("/api/music/library/12/cover?v=1&size=s"))
    }

    // MARK: - the store

    /// The same cover at two sizes is two pictures: two asks, two paths,
    /// and neither answer lands on the other's key.
    func testTwoSizesOfOneCoverNeverShareAnEntry() async {
        let asked = Shared<[String]>([])
        let covers = MusicCoverStore(renewal: MusicCoverRenewal(), revalidate: { key, _ in
            asked.mutate { $0.append(key.path) }
            return .picture(key.size == .s ? Self.thumbnail : Self.picture, etag: "\"\(key.path)\"")
        })
        let small = MusicCoverKey(trackID: 12, version: 3, size: .s)
        let large = MusicCoverKey(trackID: 12, version: 3, size: .l)
        XCTAssertNotEqual(small, large)

        covers.want(small)
        await settleUntil({ covers.cover(small) == .image(Self.thumbnail) }, "the thumbnail")
        XCTAssertNil(covers.cover(large), "the thumbnail is not the large copy")

        covers.want(large)
        await settleUntil({ covers.cover(large) == .image(Self.picture) }, "the large copy")
        XCTAssertEqual(.image(Self.thumbnail), covers.cover(small))
        XCTAssertEqual(
            ["/api/music/library/12/cover?v=3&size=s", "/api/music/library/12/cover?v=3&size=l"],
            asked.current
        )
        XCTAssertEqual(2, covers.count)
    }

    /// While one size loads, the newest version's copy at that size is
    /// preferred, and another size of it stands in when there is none.
    func testTheNewestImagePrefersTheSizeAsked() async {
        let covers = MusicCoverStore(renewal: MusicCoverRenewal(), revalidate: { key, _ in
            .picture(key.size == .s ? Self.thumbnail : Self.picture, etag: nil)
        })
        let small = MusicCoverKey(trackID: 12, version: 3, size: .s)
        covers.want(small)
        await settleUntil({ covers.cover(small) == .image(Self.thumbnail) }, "the thumbnail")
        XCTAssertEqual(.image(Self.thumbnail), covers.newestImage(trackID: 12, size: .l), "the only copy stands in")

        let large = MusicCoverKey(trackID: 12, version: 3, size: .l)
        covers.want(large)
        await settleUntil({ covers.cover(large) == .image(Self.picture) }, "the large copy")
        XCTAssertEqual(.image(Self.picture), covers.newestImage(trackID: 12, size: .l))
        XCTAssertEqual(.image(Self.thumbnail), covers.newestImage(trackID: 12, size: .s))
    }
}
