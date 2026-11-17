import CoreGraphics
import Foundation
import ImageIO

/// The room display's cover, decoded safely (security audit round 6, IOS-4).
///
/// The bytes come from the room's server, so they are read with ImageIO and
/// never handed whole to `UIImage(data:)`, which decodes every pixel the
/// file claims: a small file can claim a picture large enough to take a
/// phone's memory. Here the header is read first, a picture with a side over
/// ``maxSide`` or a shape longer than ``maxAspect`` to 1 is refused, and
/// what is drawn is a thumbnail ImageIO makes at no more than
/// ``thumbnailSide`` pixels, decoded straight to that size.
public enum RoomArtImage {

    /// The longest side a cover may claim, in pixels.
    public static let maxSide = 4096

    /// The longest side to the shortest: a cover is square, near enough.
    public static let maxAspect = 8.0

    /// The thumbnail's longest side, in pixels.
    public static let thumbnailSide = 1024

    /// Whether a picture of `width` × `height` pixels may be drawn.
    public static func acceptable(width: Int, height: Int) -> Bool {
        guard width > 0, height > 0, width <= maxSide, height <= maxSide else { return false }
        let long = Double(max(width, height))
        let short = Double(min(width, height))
        return long / short <= maxAspect
    }

    /// The pixel size the file says it is, from its header alone; nil when
    /// it is not a picture ImageIO can read.
    public static func pixelSize(_ data: Data) -> (width: Int, height: Int)? {
        guard let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
              CGImageSourceGetCount(source) > 0,
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = (properties[kCGImagePropertyPixelWidth] as? NSNumber)?.intValue,
              let height = (properties[kCGImagePropertyPixelHeight] as? NSNumber)?.intValue
        else { return nil }
        return (width, height)
    }

    /// The cover to draw, at most ``thumbnailSide`` pixels on its longest
    /// side; nil when the bytes are not a picture or claim a size
    /// ``acceptable(width:height:)`` refuses.
    public static func thumbnail(_ data: Data, maxPixels: Int = RoomArtImage.thumbnailSide) -> CGImage? {
        guard let size = pixelSize(data), acceptable(width: size.width, height: size.height),
              let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary)
        else { return nil }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceThumbnailMaxPixelSize: max(1, min(maxPixels, thumbnailSide)),
        ]
        return CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
    }
}
