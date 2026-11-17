import CoreGraphics
import Foundation

/// Domovoi's cat as line art, for the menu-bar icon (Train 1 finding 3: the
/// icon was a generic SF Symbol cat). The drawing is the dashboard's own
/// mascot, web/static/assets/domovoi.svg (viewBox 0 0 24 24, stroke 1.4,
/// round caps and joins): the same path data, which MenuBarTests keeps equal
/// to that file. Nothing here is new art.
public enum Mascot {
    /// The SVG's `d` attributes, in its order.
    public static let paths: [String] = [
        "M5 13 C5 9.4 7.5 7.5 12 7.5 C16.5 7.5 19 9.4 19 13 L19 16.2 C19 18 17.4 19.4 15.6 19.4 L8.4 19.4 C6.6 19.4 5 18 5 16.2 Z",
        "M5.6 9.5 L4.4 5.6 L8.5 7.6",
        "M18.4 9.5 L19.6 5.6 L15.5 7.6",
        "M11.3 15.4 L12 16.1 L12.7 15.4",
        "M12 16.1 L12 17",
        "M7.6 15.4 L9.4 15.7 M7.6 16.4 L9.4 16.3",
        "M16.4 15.4 L14.6 15.7 M16.4 16.4 L14.6 16.3",
    ]
    /// The two filled eyes: (cx, cy, r).
    public static let eyes: [(CGFloat, CGFloat, CGFloat)] = [(9.6, 13.4, 0.55), (14.4, 13.4, 0.55)]
    public static let strokeWidth: CGFloat = 1.4
    /// What the strokes cover in the 24-unit box (ears to chin), before the
    /// stroke's own half width.
    public static let bounds = CGRect(x: 4.4, y: 5.6, width: 15.2, height: 13.8)

    /// One `d` attribute as a path, in the SVG's own units (y grows down).
    /// The file uses absolute M, L, C and Z only; anything else is nil.
    public static func path(_ d: String) -> CGPath? {
        var tokens: [String] = []
        var num = ""
        func flush() { if !num.isEmpty { tokens.append(num); num = "" } }
        for ch in d {
            if ch.isLetter { flush(); tokens.append(String(ch)) }
            else if ch == " " || ch == "," { flush() }
            else if ch == "-" { flush(); num.append(ch) }
            else { num.append(ch) }
        }
        flush()
        let p = CGMutablePath()
        var i = 0
        var cmd = ""
        func next() -> CGFloat? {
            guard i < tokens.count, let v = Double(tokens[i]) else { return nil }
            i += 1
            return CGFloat(v)
        }
        func point() -> CGPoint? {
            guard let x = next(), let y = next() else { return nil }
            return CGPoint(x: x, y: y)
        }
        while i < tokens.count {
            if let c = tokens[i].first, c.isLetter { cmd = tokens[i]; i += 1 }
            switch cmd {
            case "M": guard let a = point() else { return nil }; p.move(to: a); cmd = "L"
            case "L": guard let a = point() else { return nil }; p.addLine(to: a)
            case "C":
                guard let a = point(), let b = point(), let c = point() else { return nil }
                p.addCurve(to: c, control1: a, control2: b)
            case "Z": p.closeSubpath(); cmd = ""
            default: return nil
            }
        }
        return p.isEmpty ? nil : p
    }

    /// Draws the cat in `rect` (a context whose y grows DOWN, as an
    /// NSImage drawn with `flipped: true`), stroked and filled with `color`.
    public static func draw(in ctx: CGContext, rect: CGRect, color: CGColor) {
        let pad = strokeWidth / 2
        let box = bounds.insetBy(dx: -pad, dy: -pad)
        let s = min(rect.width / box.width, rect.height / box.height)
        ctx.saveGState()
        ctx.translateBy(x: rect.midX - box.midX * s, y: rect.midY - box.midY * s)
        ctx.scaleBy(x: s, y: s)
        ctx.setStrokeColor(color)
        ctx.setFillColor(color)
        ctx.setLineWidth(strokeWidth)
        ctx.setLineCap(.round)
        ctx.setLineJoin(.round)
        for d in paths { if let p = path(d) { ctx.addPath(p); ctx.strokePath() } }
        for (x, y, r) in eyes { ctx.fillEllipse(in: CGRect(x: x - r, y: y - r, width: 2 * r, height: 2 * r)) }
        ctx.restoreGState()
    }
}
