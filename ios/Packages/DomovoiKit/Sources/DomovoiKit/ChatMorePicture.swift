import Foundation

/// What a picture is by its own bytes, whether it has to be converted before
/// the house would take it, the size it is converted to, and the words a
/// person reads when one cannot be sent.
///
/// Ported from `ChatPhotoPlan.kt` whole — `sniffPicture` (`:96`), `photoPlan`
/// (`:142`), `chatPhotoSize` (`:157`), `photoUnreadableText` (`:182`) and
/// `uploadRefusalText` (`:236`) — with the same rules as the server's
/// `picture_format_of` (`chat.py:2512`, which says so), so the two name a
/// photo alike.
///
/// ### What is not here, and why there is no control for it
///
/// **There is no sender.** `ChatModel/send(_:)` (`ChatModel.swift:579`) takes
/// a `String` and `ChatPath.sendBody(content:deviceID:)`
/// (`ChatModels.swift:504`) writes `{content, device_id}` — the server's
/// `SendBody` also accepts `images: [{token, name}]` (`chat.py:642`), but
/// both of those files are ones this item may not edit (NIGHT-RULES §2). A
/// paperclip whose picture can never leave the phone is exactly the control
/// [[no-misleading-controls]] forbids, so the paperclip is not drawn and this
/// is the pure half waiting for it: shared change 1 in
/// `design-notes/ios/overnight/chatmore/DECISIONS.md` is the one call that
/// finishes it.
///
/// What **is** drawn is a picture a message already carries
/// (``ChatMorePictureStore``), which needs no send at all.

/// The server's picture limits (`chat.py`, `domovoi/image_safety.py`;
/// `ChatPhotoPlan.kt:27-28`).
public enum ChatMorePictureLimits {
    public static let maxPixels: Int64 = 40_000_000
    public static let maxBytes: Int64 = 20 * 1024 * 1024

    /// The long edge of a converted photo, and its JPEG quality
    /// (`ChatPhotoPlan.kt:38`, `:41`).
    public static let maxEdge = 2560
    public static let jpegQuality = 90
}

/// What a picture is, by its first bytes (`ChatPhotoPlan.kt:48`).
///
/// ``fileExtension`` and ``mime`` are set for the kinds the phone sends as
/// they are; every other kind is converted to a JPEG.
public enum ChatMorePictureKind: String, Equatable, Sendable, CaseIterable {
    case jpeg
    case png
    case gif
    case webp
    /// The server takes BMP, but it is uncompressed: a JPEG is a fraction of
    /// it (`ChatPhotoPlan.kt:53`).
    case bmp
    case heic
    case heif
    case avif
    case jpegXL
    case tiff
    case unknown

    /// How it is named in a sentence a person reads.
    public var label: String {
        switch self {
        case .jpeg: return "JPEG"
        case .png: return "PNG"
        case .gif: return "GIF"
        case .webp: return "WebP"
        case .bmp: return "BMP"
        case .heic: return "HEIC"
        case .heif: return "HEIF"
        case .avif: return "AVIF"
        case .jpegXL: return "JPEG XL"
        case .tiff: return "TIFF"
        case .unknown: return ""
        }
    }

    public var fileExtension: String? {
        switch self {
        case .jpeg: return "jpg"
        case .png: return "png"
        case .gif: return "gif"
        case .webp: return "webp"
        default: return nil
        }
    }

    public var mime: String? {
        switch self {
        case .jpeg: return "image/jpeg"
        case .png: return "image/png"
        case .gif: return "image/gif"
        case .webp: return "image/webp"
        default: return nil
        }
    }

    public var sendsAsIs: Bool { fileExtension != nil }

    /// Whether **this** phone can decode the kind at all.
    ///
    /// Android asks which Android version first decodes it
    /// (`decodableFromSdk`, `ChatPhotoPlan.kt:68`: API 28 for HEIF/HEIC, 31
    /// for AVIF). `ImageIO` decodes HEIC, HEIF and AVIF on every iOS this app
    /// ships for (18 and up), so the version branch has no iOS subject and
    /// what is left is the two kinds no Apple decoder reads: JPEG XL and
    /// TIFF — and TIFF `ImageIO` does read, so only JPEG XL is out.
    public var convertible: Bool {
        switch self {
        case .jpegXL, .unknown: return false
        default: return true
        }
    }
}

/// Why a picture is converted before it is sent (`ChatPhotoPlan.kt:125`).
public enum ChatMoreConvertReason: Equatable, Sendable {
    case format
    case pixels
    case bytes
}

/// Send the picture as it is, or convert it to a JPEG first
/// (`ChatPhotoPlan.kt:128`).
public enum ChatMorePhotoPlan: Equatable, Sendable {
    case asIs(ChatMorePictureKind)
    case convert(ChatMorePictureKind, ChatMoreConvertReason)
}

public struct ChatMorePhotoSize: Equatable, Sendable {
    public let width: Int
    public let height: Int

    public init(width: Int, height: Int) {
        self.width = width
        self.height = height
    }
}

public enum ChatMorePicture {

    // MARK: - what the bytes say

    private static let heicBrands: Set<String> = ["heic", "heix", "hevc", "hevx", "heim", "heis", "hevm", "hevs"]
    private static let heifBrands: Set<String> = ["mif1", "msf1"]
    private static let avifBrands: Set<String> = ["avif", "avis"]

    /// JPEG XL's container signature box (`ChatPhotoPlan.kt:86`).
    private static let jxlBox: [UInt8] = [0, 0, 0, 0x0C, 0x4A, 0x58, 0x4C, 0x20, 0x0D, 0x0A, 0x87, 0x0A]

    /// What kind of picture `head` (a file's first bytes; 64 is plenty) is, by
    /// its magic bytes **alone** — never the name the gallery reports, which
    /// is the whole reason this exists (`ChatPhotoPlan.kt:96`).
    public static func sniff(_ head: Data) -> ChatMorePictureKind {
        let bytes = [UInt8](head)
        func at(_ index: Int) -> Int { index < bytes.count ? Int(bytes[index]) : -1 }
        func ascii(_ from: Int, _ length: Int) -> String? {
            guard from >= 0, from + length <= bytes.count else { return nil }
            return String(bytes: bytes[from..<(from + length)], encoding: .isoLatin1)
        }

        if at(0) == 0xFF, at(1) == 0xD8, at(2) == 0xFF { return .jpeg }
        if at(0) == 0x89, ascii(1, 3) == "PNG", at(4) == 0x0D, at(5) == 0x0A, at(6) == 0x1A, at(7) == 0x0A {
            return .png
        }
        if ascii(0, 6) == "GIF87a" || ascii(0, 6) == "GIF89a" { return .gif }
        if ascii(0, 4) == "RIFF", ascii(8, 4) == "WEBP" { return .webp }
        if ascii(0, 2) == "BM", bytes.count >= 14 { return .bmp }
        if ascii(4, 4) == "ftyp" {
            let declared = (at(0) << 24) | (at(1) << 16) | (at(2) << 8) | at(3)
            let end = min(bytes.count, (16...4096).contains(declared) ? declared : 16)
            // The major brand, then the compatible ones (the minor version at
            // 12..<16 is skipped), as `ChatPhotoPlan.kt:109` reads them.
            var brands: [String] = []
            if let major = ascii(8, 4) { brands.append(major) }
            var index = 16
            while index + 3 < end {
                if let brand = ascii(index, 4) { brands.append(brand) }
                index += 4
            }
            if brands.contains(where: avifBrands.contains) { return .avif }
            if brands.contains(where: heicBrands.contains) { return .heic }
            if brands.contains(where: heifBrands.contains) { return .heif }
            return .unknown
        }
        if (at(0) == 0xFF && at(1) == 0x0A) || bytes.starts(with: jxlBox) { return .jpegXL }
        if ascii(0, 4) == "II*\u{0000}" || ascii(0, 4) == "MM\u{0000}*" { return .tiff }
        return .unknown
    }

    // MARK: - the decision

    /// A JPEG, PNG, GIF or WebP the server takes as it is goes as it is, byte
    /// for byte. Anything else is converted — another kind, or one of those
    /// four over 40 megapixels or 20 MB (`ChatPhotoPlan.kt:142`).
    ///
    /// `width`/`height` are the picture's own, from its header; 0 or less when
    /// they could not be read, and then a kind the server takes goes as it is
    /// and **the server says what is wrong with it**.
    public static func plan(
        kind: ChatMorePictureKind,
        width: Int,
        height: Int,
        byteCount: Int64
    ) -> ChatMorePhotoPlan {
        guard kind.sendsAsIs else { return .convert(kind, .format) }
        if width > 0, height > 0, Int64(width) * Int64(height) > ChatMorePictureLimits.maxPixels {
            return .convert(kind, .pixels)
        }
        if byteCount > ChatMorePictureLimits.maxBytes { return .convert(kind, .bytes) }
        return .asIs(kind)
    }

    /// The size a converted photo gets: its own shape, at most `maxEdge` on
    /// the long edge and `maxPixels` in all, never enlarged, each side at
    /// least 1 (`chatPhotoSize`, `ChatPhotoPlan.kt:157`).
    ///
    /// Returns nil for a picture with no size, where the Kotlin throws: a
    /// `require` here would be a crash in a decoder's hands, and the caller
    /// already has a branch for "the phone could not read the header".
    public static func size(
        width: Int,
        height: Int,
        maxEdge: Int = ChatMorePictureLimits.maxEdge,
        maxPixels: Int64 = ChatMorePictureLimits.maxPixels
    ) -> ChatMorePhotoSize? {
        guard width > 0, height > 0 else { return nil }
        let byEdge = Double(maxEdge) / Double(max(width, height))
        let byPixels = (Double(maxPixels) / (Double(width) * Double(height))).squareRoot()
        let scale = min(1.0, byEdge, byPixels)
        if scale >= 1.0 { return ChatMorePhotoSize(width: width, height: height) }
        // Rounded (4032 × 3024 is 2560 × 1920, not 1919), then trimmed back
        // inside both limits in the rare case rounding stepped over one.
        var w = max(1, Int((Double(width) * scale).rounded()))
        var h = max(1, Int((Double(height) * scale).rounded()))
        while max(w, h) > maxEdge || Int64(w) * Int64(h) > maxPixels {
            if w >= h { w -= 1 } else { h -= 1 }
            if w < 1 || h < 1 { return ChatMorePhotoSize(width: max(1, w), height: max(1, h)) }
        }
        return ChatMorePhotoSize(width: w, height: h)
    }

    // MARK: - the words

    /// Why the phone could not send a picture it could not open
    /// (`photoUnreadableText`, `ChatPhotoPlan.kt:182`).
    ///
    /// Android's first branch — "this phone can't convert HEIC photos (that
    /// needs Android 9 or newer)" — is **not** ported: `ImageIO` decodes
    /// HEIC, HEIF and AVIF on every iOS this app ships for, so there is no
    /// version to name and no phone to blame.
    public static func unreadableText(_ kind: ChatMorePictureKind) -> String {
        if kind == .unknown { return ChatMoreStrings.notAPicture }
        if kind.sendsAsIs || kind == .bmp { return ChatMoreStrings.pictureDamaged(kind.label) }
        if kind.convertible { return ChatMoreStrings.photoDamaged(kind.label) }
        return ChatMoreStrings.cannotConvert(kind.label)
    }

    /// The codes of the server's upload refusals (`chat.py` upload refusal
    /// codes; `ChatPhotoPlan.kt:228`).
    public static let uploadRefusalCodes: Set<String> = [
        "unsupported_format", "too_many_pixels", "file_too_large",
    ]

    /// What a person reads when the server refused an upload, from its status
    /// and body: **the server's own sentence** for a coded refusal, the same
    /// words for an older server's bare 415/413, nil for anything else — and
    /// then the caller says "upload failed" with the reason
    /// (`uploadRefusalText`, `ChatPhotoPlan.kt:236`).
    public static func uploadRefusalText(status: Int, body: String) -> String? {
        let object = ChatMoreJSON.object(body)
        let code = ChatMoreJSON.string(object, "code")
        let detail = ChatMoreJSON.string(object, "detail")
        if let code, uploadRefusalCodes.contains(code), let detail, !detail.isEmpty { return detail }
        switch status {
        case 415: return ChatMoreStrings.uploadNotAPicture
        case 413: return ChatMoreStrings.uploadTooLarge
        default: return nil
        }
    }
}

/// Reading one string out of a server's error body.
///
/// Three refusals in this item prefer the server's own `detail` over a
/// sentence of the app's — a voice turn (`VoiceTurn.kt:104`), an upload
/// (`ChatPhotoPlan.kt:236`) and a document (`ChatDocsApi.kt:257`) — and each
/// one in Kotlin parses the body itself. One parser, so a malformed body
/// behaves the same in all three.
enum ChatMoreJSON {

    static func object(_ body: String) -> [String: Any]? {
        guard let data = body.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return object
    }

    /// The value at `key` when it is a string with something in it.
    static func string(_ object: [String: Any]?, _ key: String) -> String? {
        guard let said = object?[key] as? String else { return nil }
        let trimmed = said.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
