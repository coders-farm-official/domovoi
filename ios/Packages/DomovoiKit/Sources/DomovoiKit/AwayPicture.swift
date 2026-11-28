import CryptoKit
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// A picture made ready to send from away (AWAY-CHAT.md, §3.3): at most
/// 1024 px on its long edge, a JPEG at quality about 0.8, **no metadata at
/// all** (no EXIF, no GPS, no XMP: the pixels are drawn into a fresh JPEG
/// rather than the original's being copied), at most 1 MB, and its sha256.
/// The house strips and re-encodes it again whatever arrives.
public struct AwayPicture: Equatable, Sendable {
    public let jpeg: Data
    /// 64 lower-case hex.
    public let sha256: String
    public let width: Int
    public let height: Int

    public static let longEdge = 1024
    public static let quality = 0.8
    public static let maxBytes = AwayWire.maxPicture

    /// Shrink and re-encode `data` (any format ImageIO reads: HEIC, JPEG,
    /// PNG…), turned the way it is meant to be seen. Nil when it can't be
    /// read or can't be brought under 1 MB.
    public static func prepare(_ data: Data) -> AwayPicture? {
        guard let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
              CGImageSourceGetCount(source) > 0 else { return nil }
        var edge = longEdge
        while edge >= 256 {
            let options: [CFString: Any] = [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                // The EXIF orientation is applied to the pixels, so nothing
                // needs it afterwards.
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: edge,
                kCGImageSourceShouldCacheImmediately: true,
            ]
            guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else { return nil }
            for q in [quality, 0.7, 0.6, 0.5] {
                if let jpeg = encode(image, quality: q), jpeg.count <= maxBytes {
                    return AwayPicture(jpeg: jpeg, sha256: hex(jpeg), width: image.width, height: image.height)
                }
            }
            edge = edge * 3 / 4
        }
        return nil
    }

    /// A JPEG of `image` and nothing else: a destination given only the
    /// pixels and a quality writes no EXIF, GPS or XMP of the original.
    static func encode(_ image: CGImage, quality: Double) -> Data? {
        let out = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(out as CFMutableData, UTType.jpeg.identifier as CFString, 1, nil) else {
            return nil
        }
        let properties: [CFString: Any] = [kCGImageDestinationLossyCompressionQuality: quality]
        CGImageDestinationAddImage(destination, image, properties as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { return nil }
        return out as Data
    }

    static func hex(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}
