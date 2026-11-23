import Foundation

/// Which size of a library track's cover to ask for.
///
/// `GET /api/music/library/{id}/cover` takes an optional `size`
/// (`web/backend/api/music.py`, `track_cover`): `s`, `m`, `l` or `xl` answer
/// a WebP no larger than 160, 320, 512 or 768 px on its long edge, each with
/// its own `ETag`. Without `size` the original embedded picture comes back,
/// which can be 1.5 MB; a page of fifty rows asking for originals cost about
/// 20 MB and a full decode per 40-point thumbnail. A server older than the
/// parameter ignores it and answers the original, so it is always safe to
/// send.
///
/// The bucket is the smallest whose edge covers the drawn size at the
/// iPhone's 3x scale (``screenScale``); an iPad is 2x and gets a little
/// more than it needs, which is cheaper than a second rule.
public enum MusicCoverSize: String, CaseIterable, Sendable, Hashable {
    case s
    case m
    case l
    case xl

    /// The bucket's long edge in pixels, as the server caps it.
    public var edge: Int {
        switch self {
        case .s: return 160
        case .m: return 320
        case .l: return 512
        case .xl: return 768
        }
    }

    /// Pixels per point the bucket is chosen for: the iPhone's.
    public static let screenScale: Double = 3

    /// The lock screen's (`MPNowPlayingInfo`) artwork: the largest bucket.
    public static let nowPlaying: MusicCoverSize = .xl

    /// The smallest bucket whose edge is at least `points` × `scale` pixels;
    /// ``xl`` for anything larger than every bucket.
    ///
    /// At 3x: up to 53 pt is ``s``, 54–106 ``m``, 107–170 ``l``, beyond ``xl``.
    public static func fitting(points: Double, scale: Double = screenScale) -> MusicCoverSize {
        let pixels = points * scale
        return allCases.first { Double($0.edge) >= pixels } ?? .xl
    }
}
