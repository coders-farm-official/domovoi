import CoreGraphics
import Foundation
import ImageIO
import XCTest
@testable import DomovoiKit

/// Security audit round 6, IOS-4: a room's reads stop at their limit as the
/// body arrives, a cover comes only from `/v1/music/cover/{id}`, and a cover
/// is decoded by ImageIO to a thumbnail, never whole, with extreme sizes
/// refused.
final class RoomReadLimitTests: XCTestCase {

    // MARK: - the streamed limit

    /// A server that sends for ever, with no `Content-Length`: the read is
    /// cancelled once the body passes the limit, rather than buffered to the
    /// end (there is no end).
    func testAnEndlessBodyIsCancelledAtTheLimit() throws {
        EndlessBody.reset()
        let configuration = RoomNetwork.configuration()
        configuration.protocolClasses = [EndlessBody.self]
        let network = RoomNetwork(configuration: configuration)
        defer { network.invalidate() }

        let limit = 1024 * 1024
        XCTAssertThrowsError(try network.get("http://192.168.1.20:6370/v1/music/cover/12", limit: limit)) { error in
            XCTAssertTrue((error as? RoomNetworkFailure)?.message.contains("too large") == true, "\(error)")
        }
        XCTAssertTrue(RoomTestKit.wait(5) { EndlessBody.stopped }, "the read was cancelled, not left running")
        let sent = EndlessBody.sentBytes
        XCTAssertGreaterThan(sent, limit, "it did read up to the limit")
        XCTAssertLessThan(sent, EndlessBody.ceiling, "and stopped long before the stub's own ceiling")
    }

    /// A `Content-Length` already past the limit stops the read at the head.
    func testADeclaredLengthPastTheLimitIsRefusedBeforeTheBody() throws {
        let core = try LoopbackServer(.init(status: 200, body: String(repeating: "x", count: 5_000)))
        defer { core.stop() }
        let network = RoomNetwork()
        defer { network.invalidate() }
        XCTAssertThrowsError(try network.get("http://127.0.0.1:\(core.port)/v1/music/cover/1", limit: 4_000)) { error in
            XCTAssertTrue((error as? RoomNetworkFailure)?.message.contains("too large") == true, "\(error)")
        }
        let small = try LoopbackServer(.init(status: 200, body: String(repeating: "y", count: 10)))
        defer { small.stop() }
        let body = try network.get("http://127.0.0.1:\(small.port)/v1/music/cover/1", limit: 10)
        XCTAssertEqual(Data(String(repeating: "y", count: 10).utf8), body, "exactly the limit is fine")
    }

    // MARK: - the cover path

    func testTheCoverPathIsExactlyTheCoreOne() {
        for good in ["/v1/music/cover/1", "/v1/music/cover/12", "/v1/music/cover/1234567890"] {
            XCTAssertTrue(RoomDisplayFeed.isArtPath(good), good)
        }
        let bad = [
            "/v1/music/cover/", "/v1/music/cover/12345678901", "/v1/music/cover/12?v=3", "/v1/music/cover/12/",
            "/v1/music/cover/-1", "/v1/music/cover/1a", "/v1/music/cover/١٢", "/v1/music/cover/12 ",
            "/v1/music/cover/../../admin", "/v1/admin/restart", "/v1/health", "/v1/art/1",
            "http://elsewhere/v1/music/cover/1", "//elsewhere/v1/music/cover/1", "/V1/music/cover/1",
            " /v1/music/cover/1", "/v1/music/cover/1\n",
        ]
        for path in bad {
            XCTAssertFalse(RoomDisplayFeed.isArtPath(path), path)
        }
    }

    // MARK: - the dimension rule

    func testTheDimensionRule() {
        XCTAssertTrue(RoomArtImage.acceptable(width: 600, height: 600))
        XCTAssertTrue(RoomArtImage.acceptable(width: 4096, height: 4096))
        XCTAssertTrue(RoomArtImage.acceptable(width: 800, height: 100), "8:1 is the edge")
        XCTAssertFalse(RoomArtImage.acceptable(width: 4097, height: 4000), "a side over 4096")
        XCTAssertFalse(RoomArtImage.acceptable(width: 100, height: 4097))
        XCTAssertFalse(RoomArtImage.acceptable(width: 900, height: 100), "past 8:1")
        XCTAssertFalse(RoomArtImage.acceptable(width: 1, height: 9))
        XCTAssertFalse(RoomArtImage.acceptable(width: 0, height: 10))
        XCTAssertFalse(RoomArtImage.acceptable(width: -5, height: 10))
        XCTAssertFalse(RoomArtImage.acceptable(width: Int.max, height: Int.max))
    }

    func testACoverIsDecodedToAThumbnailAndExtremeOnesAreRefused() throws {
        let big = try XCTUnwrap(Self.png(width: 2000, height: 1500))
        let thumb = try XCTUnwrap(RoomArtImage.thumbnail(big))
        XCTAssertLessThanOrEqual(max(thumb.width, thumb.height), RoomArtImage.thumbnailSide)
        XCTAssertEqual(2000, RoomArtImage.pixelSize(big)?.width)

        let small = try XCTUnwrap(Self.png(width: 64, height: 64))
        XCTAssertNotNil(RoomArtImage.thumbnail(small))

        XCTAssertNil(RoomArtImage.thumbnail(try XCTUnwrap(Self.png(width: 4097, height: 600))), "a side over 4096")
        XCTAssertNil(RoomArtImage.thumbnail(try XCTUnwrap(Self.png(width: 900, height: 100))), "past 8:1")
        XCTAssertNil(RoomArtImage.thumbnail(Data("not a picture".utf8)))
        XCTAssertNil(RoomArtImage.thumbnail(Data()))
    }

    /// A plain grey PNG of the size asked.
    static func png(width: Int, height: Int) -> Data? {
        guard let context = CGContext(
            data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return nil }
        context.setFillColor(CGColor(red: 0.5, green: 0.5, blue: 0.5, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        guard let image = context.makeImage() else { return nil }
        let out = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(out as CFMutableData, "public.png" as CFString, 1, nil)
        else { return nil }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else { return nil }
        return out as Data
    }
}

/// A server that answers 200 and sends 64 KiB pieces until it is stopped,
/// with no `Content-Length` (up to ``ceiling``, so a broken test ends).
///
/// `@unchecked Sendable`: its one piece of per-instance state is behind a
/// ``RoomLock``, and the counters every instance shares are too.
final class EndlessBody: URLProtocol, @unchecked Sendable {

    static let ceiling = 256 * 1024 * 1024
    private static let chunk = Data(repeating: 0x41, count: 64 * 1024)
    private static let counters = RoomLock<(sent: Int, stopped: Bool)>((0, false))

    static func reset() { counters.withLock { $0 = (0, false) } }
    static var sentBytes: Int { counters.withLock { $0.sent } }
    static var stopped: Bool { counters.withLock { $0.stopped } }

    private let running = RoomLock(true)

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let url = request.url,
              let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: "HTTP/1.1",
                                             headerFields: ["Content-Type": "image/jpeg"])
        else { return }
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            while running.withLock({ $0 }), Self.sentBytes < Self.ceiling {
                client?.urlProtocol(self, didLoad: Self.chunk)
                Self.counters.withLock { $0.sent += Self.chunk.count }
                Thread.sleep(forTimeInterval: 0.001)
            }
            if Self.sentBytes >= Self.ceiling { client?.urlProtocolDidFinishLoading(self) }
        }
    }

    override func stopLoading() {
        running.withLock { $0 = false }
        Self.counters.withLock { $0.stopped = true }
    }
}
