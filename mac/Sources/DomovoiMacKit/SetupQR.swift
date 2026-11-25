import CoreGraphics
import CoreImage
import Foundation

/// The setup code's QR (1.5.0; domovoi/setup_qr.py is the reference): the
/// dashboard's claim address on this Mac's home-network address, with the
/// code in the fragment only, `http://<lan>:6369/#setup-code=<code>`. A
/// browser never sends a fragment to the server, and the dashboard's
/// setup_fragment.js clears it at once. The code's life (24 h, single use)
/// is unchanged. Drawn with CoreImage's own generator: no new dependency.
public enum SetupQR {
    public static let fragmentKey = "setup-code"

    /// What Python's `quote(code, safe='-')` leaves alone: ASCII letters,
    /// digits and `_.-~`. Everything else (the spaces between the words) is
    /// percent-encoded, as the dashboard's decodeURIComponent expects.
    static let unreserved = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~")

    /// The claim address, or nil when there is no usable address (a phone
    /// can't reach this Mac's loopback) or no code.
    public static func claimURL(lan: String?, port: Int = Ports.real.web, code: String) -> String? {
        guard let lan, HostFiles.usable(lan) else { return nil }
        let c = code.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !c.isEmpty, let enc = c.addingPercentEncoding(withAllowedCharacters: unreserved) else { return nil }
        return "http://\(lan):\(port)/#\(fragmentKey)=\(enc)"
    }

    /// The QR for `text`, black on white with the standard 4-module quiet
    /// zone, each module `scale` pixels (nearest-neighbour: sharp edges).
    /// Error correction M, as the Linux and Windows QRs.
    public static func image(for text: String, scale: Int = 8) -> CGImage? {
        guard let f = CIFilter(name: "CIQRCodeGenerator") else { return nil }
        f.setValue(Data(text.utf8), forKey: "inputMessage")
        f.setValue("M", forKey: "inputCorrectionLevel")
        // CoreImage adds a one-module margin of its own; pad to four.
        guard let raw = f.outputImage else { return nil }
        let quiet = CGFloat(3)
        let white = CIImage(color: .white).cropped(to: raw.extent.insetBy(dx: -quiet, dy: -quiet))
        let padded = raw.composited(over: white)
        let scaled = padded.transformed(by: CGAffineTransform(scaleX: CGFloat(scale), y: CGFloat(scale)))
        let ctx = CIContext(options: [.useSoftwareRenderer: true])
        return ctx.createCGImage(scaled, from: scaled.extent)
    }
}
