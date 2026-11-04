import DomovoiKit
import SwiftUI

// ---------------------------------------------------------------------------
// The map: the house as a diagram you can zoom into.
//
// `ManualScreen.kt:481` (`ring`), `:503` (`ManualMap`) and `:641` (`MapNode`).
// Two levels — the centre with its ring of topics, and one topic's cluster of
// pieces — and a camera that moves between them.
//
// **The arithmetic is ported line for line**, and so is the ornament, as of
// Kamron's answers 30 and 31: the name scrambles into place
// (``ManualScrambleName``) and the cards float (``ManualMapNode``'s
// `floatPeriod`). Both stop under Reduce Motion, as the Kotlin's `reduce`
// branches do, and so does the 280 ms zoom. What is **not** ported is the
// wires following the float — see ``ManualMapStage/wires(_:size:)``.
// ---------------------------------------------------------------------------

/// A world position and the scale the thing at it is drawn at — `Pos`
/// (`ManualScreen.kt:479`).
struct ManualMapPos: Equatable {
    let x: CGFloat
    let y: CGFloat
    let s: CGFloat
}

/// One dashed wire, already in the stage's coordinates.
struct ManualMapWire: Equatable, Sendable {
    let from: CGPoint
    let to: CGPoint
}

/// A cluster's scale against its parent's (`CLUSTER`, `:159`). The camera's
/// second level is its reciprocal, so a cluster fills the stage the way the
/// ring does.
let manualCluster: CGFloat = 0.34

/// Narrower than this and two nodes share a row (`PHONE_MAX_DP`, `:160`).
/// Every iPhone is under it; an iPad in portrait is not.
let manualPhoneMaxWidth: CGFloat = 600

/// The gap between two rows of a phone's map (`PHONE_ROW_GAP`, `:162`).
let manualPhoneRowGap: CGFloat = 18

/// How a level is laid out (`ring`, `ManualScreen.kt:481`).
///
/// An ellipse when there is room for one; **two to a row** on a phone, half
/// the rows above the middle and half below, with an odd last node centred.
/// Positions are relative to the level's own centre.
func manualRing(
    count n: Int,
    width w: CGFloat,
    height h: CGFloat,
    phone: Bool,
    nodeWidth nw: CGFloat,
    nodeHeight nh: CGFloat
) -> [CGPoint] {
    guard n > 0 else { return [] }
    if !phone {
        let rx = max(w / 2 - nw / 2 - 14, 150)
        let ry = max(h / 2 - nh / 2 - 14, 110)
        return (0..<n).map { i in
            let a = (-90.0 + Double(i) * 360.0 / Double(n)) * .pi / 180
            return CGPoint(x: rx * CGFloat(cos(a)), y: ry * CGFloat(sin(a)))
        }
    }
    let rows = Int(ceil(Double(n) / 2))
    let upRows = Int(ceil(Double(rows) / 2))
    let step = nh + manualPhoneRowGap
    let dx = nw / 2 + 5
    return (0..<n).map { i in
        let r = i / 2
        let y = CGFloat(r < upRows ? -(upRows - r) : r - upRows + 1) * step
        let alone = i == n - 1 && n % 2 == 1
        return CGPoint(x: alone ? 0 : (i % 2 == 1 ? dx : -dx), y: y)
    }
}

/// Every world position the map holds: the centre, each ring topic, each
/// topic's cluster hub `h:<id>` and each visible piece `p:<id>` — `geo`
/// (`ManualScreen.kt:526`).
///
/// A struct rather than a dictionary so the **order** is part of the value:
/// SwiftUI's `ForEach` needs a collection, and the Kotlin's `LinkedHashMap`
/// order is what keeps two runs drawing the same map.
struct ManualMapGeo {

    let order: [String]
    let at: [String: ManualMapPos]

    init(
        doc: ManualDoc,
        visible: Set<String>,
        width w: CGFloat,
        height h: CGFloat,
        phone: Bool,
        nodeWidth nw: CGFloat,
        nodeHeight nh: CGFloat
    ) {
        var order: [String] = []
        var at: [String: ManualMapPos] = [:]
        func put(_ id: String, _ pos: ManualMapPos) {
            if at[id] == nil { order.append(id) }
            at[id] = pos
        }
        // The centre is keyed `domovoi` on both platforms — the string, not
        // `doc.centre.id`, which is what the Kotlin does (`:528`).
        put("domovoi", ManualMapPos(x: 0, y: 0, s: 1))
        let ring = doc.ringTopics
        let places = manualRing(
            count: ring.count, width: w, height: h,
            phone: phone, nodeWidth: nw, nodeHeight: nh
        )
        for (i, topic) in ring.enumerated() where i < places.count {
            put("t:\(topic.id)", ManualMapPos(x: places[i].x, y: places[i].y, s: 1))
        }
        for topic in doc.topics {
            // The topic the centre opens has no ring node of its own: its
            // cluster hangs off the middle.
            let parent: ManualMapPos
            if topic.id == doc.centre.opens {
                parent = ManualMapPos(x: 0, y: 0, s: 1)
            } else if let found = at["t:\(topic.id)"] {
                parent = found
            } else {
                // Kotlin asserts here (`g["t:${t.id}"]!!`). A topic that is
                // neither the centre's nor in the ring cannot exist —
                // `ringTopics` is "every topic but the centre's" — but a
                // crash in a manual page is not worth being right about.
                continue
            }
            put("h:\(topic.id)", ManualMapPos(x: parent.x, y: parent.y, s: manualCluster))
            let kids = topic.pieces.filter { visible.contains($0) }
            let kidPlaces = manualRing(
                count: kids.count, width: w, height: h,
                phone: phone, nodeWidth: nw, nodeHeight: nh
            )
            for (j, piece) in kids.enumerated() where j < kidPlaces.count {
                put(
                    "p:\(piece)",
                    ManualMapPos(
                        x: parent.x + kidPlaces[j].x * manualCluster,
                        y: parent.y + kidPlaces[j].y * manualCluster,
                        s: manualCluster
                    )
                )
            }
        }
        self.order = order
        self.at = at
    }
}

/// The stage: the wires, the nodes, and the camera over them (`ManualMap`,
/// `ManualScreen.kt:503`).
struct ManualMapStage: View {

    let doc: ManualDoc
    let manual: ManualModel
    let focus: ManualTopic?
    let reduceMotion: Bool

    /// A phone's node is one size for every node, so the rows never run into
    /// each other (`PHONE_NODE_DP`, `:161`).
    ///
    /// **`@ScaledMetric`, where Android's is a constant.** A node holds two
    /// lines of name and two of subtitle; at an accessibility type size a
    /// fixed 104 points clips all four, and this app's standing rule rejects
    /// `minimumScaleFactor` as the answer. The geometry is parameterised on
    /// the node's height already (`ring`'s `nh`), so the whole map — rows,
    /// gaps, stage — grows with it and nothing overlaps.
    @ScaledMetric(relativeTo: .body) private var nodeHeight: CGFloat = 104

    var body: some View {
        // The stage is as tall as its rows need and scrolls inside the page,
        // which is `phoneStageHeight()` (`:165`) — written out over the
        // scaled node so an accessibility size gets the room it asks for.
        let stage = 4 * (nodeHeight + manualPhoneRowGap) + nodeHeight + 32
        GeometryReader { space in
            let w = space.size.width
            let h = space.size.height
            let phone = w < manualPhoneMaxWidth
            let nw = phone ? (w - 14) / 2 : 196
            let nh = phone ? nodeHeight : 66
            let geo = ManualMapGeo(
                doc: doc, visible: manual.visiblePieces,
                width: w, height: h, phone: phone, nodeWidth: nw, nodeHeight: nh
            )
            let shown = shownIDs
            let camera = focus.flatMap { geo.at["h:\($0.id)"] } ?? ManualMapPos(x: 0, y: 0, s: 1)
            let k: CGFloat = focus == nil ? 1 : 1 / manualCluster
            ZStack(alignment: .topLeading) {
                wires(manualWires(geo, camera: camera, k: k, size: space.size), size: space.size)
                nodes(geo, shown: shown, nodeWidth: nw, nodeHeight: nh)
                    .frame(width: w, height: h, alignment: .topLeading)
                    .scaleEffect(k, anchor: .topLeading)
                    .offset(x: w / 2 - camera.x * k, y: h / 2 - camera.y * k)
            }
            .frame(width: w, height: h, alignment: .topLeading)
            .clipped()
        }
        .frame(height: stage)
        .frame(maxWidth: .infinity)
        .background(
            .quaternary.opacity(0.4),
            in: RoundedRectangle(cornerRadius: 14, style: .continuous)
        )
        .overlay {
            RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(.quaternary)
        }
        // One animation over the camera and the node set, keyed on the level:
        // 280 ms, the Kotlin's `MOVE_MS`, and a cut when the platform says
        // this person does not want things to move.
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.28), value: focus?.id)
    }

    /// `shown` (`ManualScreen.kt:544`): the centre and its ring at level one,
    /// a cluster's hub and its visible pieces at level two.
    private var shownIDs: [String] {
        guard let focus else {
            return ["domovoi"] + doc.ringTopics.map { "t:\($0.id)" }
        }
        let visible = manual.visiblePieces
        return ["h:\(focus.id)"] + focus.pieces.filter { visible.contains($0) }.map { "p:\($0)" }
    }

    /// `edges` (`:550`).
    private var edgeIDs: [(String, String)] {
        guard let focus else {
            return doc.ringTopics.map { ("domovoi", "t:\($0.id)") }
        }
        let visible = manual.visiblePieces
        return focus.pieces
            .filter { visible.contains($0) }
            .map { ("h:\(focus.id)", "p:\($0)") }
    }

    /// The dashed cubic wires (`:567`). A `Canvas`, drawn **once per level**
    /// where Android redraws it on every frame of the float
    /// (`drawPath` inside the `InfiniteTransition`'s read, `:569`).
    ///
    /// So a wire's ends do not rise and fall with the two cards they join. It
    /// is the one piece of the float that is given up, deliberately: a canvas
    /// rebuilt 60 times a second for as long as the map is open is the battery
    /// cost answer 31 was not worth, and a wire runs centre to centre between
    /// two ~104-point cards, so the part of it that would move is the part
    /// covered by the cards.
    ///
    /// Hidden from VoiceOver — it is a drawing of the links the nodes beside
    /// it already are.
    private func wires(_ lines: [ManualMapWire], size: CGSize) -> some View {
        // The points are worked out **before** the renderer, so the closure
        // captures a list of numbers and not this view — which holds a
        // `@MainActor` model.
        Canvas { context, _ in
            for line in lines {
                let my = (line.from.y + line.to.y) / 2
                var path = Path()
                path.move(to: line.from)
                path.addCurve(
                    to: line.to,
                    control1: CGPoint(x: line.from.x, y: my),
                    control2: CGPoint(x: line.to.x, y: my)
                )
                context.stroke(
                    path,
                    with: .color(Color.fgMuted.opacity(0.5)),
                    style: StrokeStyle(lineWidth: 1.4, dash: [4, 4])
                )
            }
        }
        .frame(width: size.width, height: size.height)
        .accessibilityHidden(true)
    }

    /// Every wire of the level on screen, in the stage's own coordinates
    /// (`ManualScreen.kt:570`).
    private func manualWires(
        _ geo: ManualMapGeo,
        camera: ManualMapPos,
        k: CGFloat,
        size: CGSize
    ) -> [ManualMapWire] {
        edgeIDs.compactMap { pair in
            guard let pa = geo.at[pair.0], let pb = geo.at[pair.1] else { return nil }
            return ManualMapWire(
                from: CGPoint(
                    x: (pa.x - camera.x) * k + size.width / 2,
                    y: (pa.y - camera.y) * k + size.height / 2
                ),
                to: CGPoint(
                    x: (pb.x - camera.x) * k + size.width / 2,
                    y: (pb.y - camera.y) * k + size.height / 2
                )
            )
        }
    }

    /// Only the nodes of the level on screen are built.
    ///
    /// Android fades every node in and out of one tree (`AnimatedVisibility`,
    /// `:621`). Drawing the other level at zero opacity would leave it in the
    /// accessibility tree, where VoiceOver would read a piece that is not on
    /// screen and a test asking "is the radio node gone" would be answered by
    /// a node nobody can see.
    private func nodes(
        _ geo: ManualMapGeo,
        shown: [String],
        nodeWidth nw: CGFloat,
        nodeHeight nh: CGFloat
    ) -> some View {
        ZStack(alignment: .topLeading) {
            ForEach(geo.order.filter { shown.contains($0) }, id: \.self) { id in
                if let pos = geo.at[id], let node = node(of: id) {
                    // The card's own index in the **whole** geometry, not in
                    // the drawn slice: Android's `ids.indexOf(id)` over
                    // `geo.keys` (`:558`), so a node bobs to the same rhythm
                    // whichever level it is seen on.
                    let at = geo.order.firstIndex(of: id) ?? 0
                    ManualMapNode(
                        node: node,
                        height: nh,
                        levelKey: levelKey,
                        reduceMotion: reduceMotion,
                        // `5.5 + (i % 4) * 0.9` seconds (`:561`). Zero is
                        // still, which is what Reduce Motion asks for.
                        floatPeriod: reduceMotion ? 0 : 5.5 + Double(at % 4) * 0.9,
                        // Android phases the sine by `i * 0.7` seconds
                        // (`:562`); a `delay` on a repeating animation is the
                        // same idea, spent once at the start.
                        floatPhase: Double(at % 8) * 0.7
                    ) {
                        manual.go(node.opens)
                    }
                    .frame(width: nw)
                    .scaleEffect(pos.s)
                    .offset(x: pos.x - nw / 2, y: pos.y - nh / 2)
                    .transition(.opacity)
                }
            }
        }
    }

    /// Which level is on screen, for the scramble's `.task(id:)` — the
    /// Kotlin's `levelKey` (`:620`).
    private var levelKey: String { focus?.id ?? "map" }

    /// What a key in ``ManualMapGeo`` draws (`:603`).
    private func node(of id: String) -> ManualMapNodeContent? {
        if id == "domovoi" {
            return ManualMapNodeContent(
                title: doc.centre.title,
                subtitle: doc.centre.subtitle,
                icon: nil,
                tags: [],
                hub: true,
                // The centre opens the topic the document says it does.
                opens: doc.centre.opens
            )
        }
        if id.hasPrefix("t:") || id.hasPrefix("h:") {
            let hub = id.hasPrefix("h:")
            guard let topic = doc.topic(String(id.dropFirst(2))) else { return nil }
            return ManualMapNodeContent(
                title: topic.title,
                subtitle: topic.subtitle,
                icon: manualTopicIcon(topic.id),
                tags: [],
                hub: hub,
                // A cluster's hub zooms back out; a ring topic zooms in.
                opens: hub ? "map" : topic.id
            )
        }
        guard let piece = doc.piece(String(id.dropFirst(2))) else { return nil }
        return ManualMapNodeContent(
            title: piece.title,
            subtitle: piece.subtitle,
            icon: nil,
            tags: manualPieceTags(piece, doc),
            hub: false,
            opens: piece.id
        )
    }
}

/// What one node says and what it opens.
struct ManualMapNodeContent: Equatable {
    let title: String
    let subtitle: String
    let icon: String?
    /// `preview` / `plugin`, in the **document's** words
    /// (`labels.preview.text`), already filtered to the ones that apply.
    let tags: [String]
    /// A hub is the level's own middle and is drawn with a stronger edge.
    let hub: Bool
    /// The id ``ManualModel/go(_:)`` is given.
    let opens: String
}

/// One card on the map (`MapNode`, `ManualScreen.kt:641`).
///
/// The name **scrambles into place** as its level opens
/// (``ManualScrambleName``) — answer 30 — and the card floats
/// (``float``) — answer 31. Both are ornament and both stop under Reduce
/// Motion.
struct ManualMapNode: View {

    let node: ManualMapNodeContent
    let height: CGFloat

    /// Which level this card belongs to, so the name scrambles again each
    /// time a level opens and not on every redraw — the Kotlin's `levelKey`
    /// (`:620`, passed to `MapNode` at `:637`).
    let levelKey: String

    /// Whether this person asked for less movement: no scramble, no float.
    let reduceMotion: Bool

    /// How long one bob takes, and how far into it this card starts
    /// (`bob`, `:559`: `5.5 + (i % 4) * 0.9` seconds, phased by `i * 0.7`).
    /// Zero period means the card is still.
    let floatPeriod: Double
    let floatPhase: Double

    let action: () -> Void

    /// The glyph's width is scaled, never a fixed `frame(width:)` — the
    /// standing rule, and the icon sits beside a name that grows.
    @ScaledMetric(relativeTo: .body) private var glyph: CGFloat = 15

    /// Flipped once, in `onAppear`, so the repeating animation below has a
    /// value change to animate — ``PrinterStatusDot``'s shape, which is this
    /// app's one other thing that never stops moving.
    @State private var floating = false

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    if let icon = node.icon {
                        Image(systemName: icon)
                            .font(.system(size: glyph))
                            .foregroundStyle(Color.brandInk)
                            .accessibilityHidden(true)
                    }
                    // **No `lineLimit`, and `fixedSize` vertically.** Android
                    // caps both of a node's texts at two lines inside a
                    // fixed 104 dp card (`ManualScreen.kt:641`). A node's
                    // *width* here is the phone's two-column width and does
                    // not scale, so at an accessibility size the two longest
                    // titles in the shipped document — `Rooms & devices` and
                    // `Music & media` — need three lines and a two-line cap
                    // cuts them: Apple's audit named exactly those two in
                    // train S006 for "Text of this element may be clipped at
                    // larger Dynamic Type sizes". That is a real cut, not the
                    // false positive the headline has twice been on this
                    // track — the measurement is in
                    // ``testManual_GrowsAtAnAccessibilitySize``. The cap is
                    // dropped instead of answered with
                    // `minimumScaleFactor`, which this app rejects
                    // everywhere, and the node's height is a `minHeight` over
                    // a `@ScaledMetric`, so the card grows to hold the lines
                    // and the stage grows with it.
                    ManualScrambleName(
                        text: node.title,
                        levelKey: levelKey,
                        reduceMotion: reduceMotion
                    )
                }
                if !node.subtitle.isEmpty || !node.tags.isEmpty {
                    Text(secondLine)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.horizontal, 11)
            .padding(.vertical, 9)
            .frame(maxWidth: .infinity, minHeight: height, alignment: .topLeading)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .background(
            Color(.secondarySystemBackground),
            in: RoundedRectangle(cornerRadius: 10, style: .continuous)
        )
        .overlay {
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                // `AnyShapeStyle` because the two arms are different styles
                // (a `Color` and the system's `.quaternary`); `strokeBorder`
                // infers one `S` from the ternary and would otherwise demand
                // `Color.quaternary`, which does not exist as a static.
                .strokeBorder(
                    node.hub
                        ? AnyShapeStyle(Color.brandInk.opacity(0.6))
                        : AnyShapeStyle(.quaternary)
                )
        }
        // The Kotlin's `contentDescription = listOfNotNull(title, sub)
        // .joinToString(": ")` (`:650`), so a node reads as one thing rather
        // than as two labels and a pair of pills.
        //
        // **This is also what keeps the scramble out of VoiceOver's way.** The
        // label is the node's real name, set here on the control, and
        // ``ManualScrambleName`` labels its own `Text` with the real name too:
        // the drawn glyphs are never anybody's accessibility label, so a case
        // that reaches a node by its name reaches it mid-scramble exactly as
        // it does at rest (``testManual_ANodeIsNamedWhileItsNameScrambles``).
        .accessibilityLabel(
            node.subtitle.isEmpty ? node.title : "\(node.title): \(node.subtitle)"
        )
        // ---- the float (`bob`, `ManualScreen.kt:559`), answer 31.
        //
        // ±5 points, and **5 here comes out as 5 × the node's scale** on
        // screen: the caller applies `scaleEffect(pos.s)` outside this view,
        // which is Android's `bob(...) * (geo[id]?.s ?: 1f)` reached by
        // another road.
        //
        // One `repeatForever` animation per card and **no per-frame redraw**:
        // the offset is handed to the render server once and animated there,
        // where Android drives an `InfiniteTransition` whose float the
        // `Canvas` reads, redrawing every wire 60 times a second. That is the
        // "battery sink" half of this decision, and the half that is given
        // up for it is that the wires do **not** follow: they are still drawn
        // once per level (see ``ManualMapStage/wires(_:size:)``). Almost all
        // of a wire is behind the two cards it joins — they are ~104 points
        // tall and it runs centre to centre — so what moves is hidden and what
        // shows does not move.
        //
        // Nothing moves under Reduce Motion: the caller passes a zero period,
        // and `nil` animation is the same cut the 280 ms zoom takes.
        // A still card sits **exactly** where the geometry put it: the zero
        // arm is not decoration. Without it, `floating` flipping with no
        // animation would leave every node 5 points above its own position
        // under Reduce Motion.
        .offset(y: floatPeriod <= 0 ? 0 : (floating ? -5 : 5))
        .animation(
            floatPeriod <= 0
                ? nil
                : .easeInOut(duration: floatPeriod)
                    .delay(floatPhase)
                    .repeatForever(autoreverses: true),
            value: floating
        )
        .onAppear { floating = true }
    }

    /// The subtitle with the `preview` and `plugin` words folded into it
    /// (`:660`), each washed as the Kotlin washes them
    /// (`SpanStyle(background = c.idleSoft)`, `:668`).
    ///
    /// The wash is ``Color/manualCodeWash`` — the same opaque token the pills
    /// inside a page's prose take, so the two words look the same wherever
    /// this screen draws them. `fgMuted` on it is **6.72:1** light and
    /// **7.77:1** dark. It used to be plain text, because a translucent
    /// inline background has no ratio to state; an opaque one does.
    private var secondLine: AttributedString {
        var out = AttributedString()
        if !node.subtitle.isEmpty { out += AttributedString(node.subtitle) }
        for tag in node.tags {
            if !out.characters.isEmpty { out += AttributedString("  ") }
            var run = AttributedString(" \(tag) ")
            run.backgroundColor = Color.manualCodeWash
            out += run
        }
        return out
    }
}

// MARK: - the name that scrambles into place

/// The alphabet a scrambling name is drawn from (`GLYPHS`,
/// `ManualScreen.kt:169`), character for character.
///
/// Every one of them is a single ASCII glyph, which is what makes the effect
/// safe here: see ``ManualScrambleName``.
let manualScrambleGlyphs = Array("ABCDEFGHKMNPRSTVXZ0123456789#%&*/<>")

/// How fast a name scrambles: nine frames, 24 ms apart (`ScrambleText`,
/// `:688` — `steps = 8`, `delay(24)`), so 216 ms in all.
enum ManualScrambleClock {

    static let steps = 8

    /// 24 ms, unless a DEBUG launch argument slows it down.
    static var frame: Duration { .milliseconds(frameMS) }

    /// `-domovoi-manual-scramble <ms>`: a UI case cannot assert anything
    /// about a 216 ms effect it will never be inside, so it can ask for a
    /// frame it has time to look at. Read **once**, and only in DEBUG, which
    /// is ``AuditStrip``'s rule for the same kind of knob; in a build that
    /// could ship this is always 24.
    private static let frameMS: Int = {
        #if DEBUG
        let args = ProcessInfo.processInfo.arguments
        if let at = args.firstIndex(of: "-domovoi-manual-scramble"),
           at + 1 < args.count,
           let ms = Int(args[at + 1]),
           ms > 0, ms <= 5_000 {
            return ms
        }
        #endif
        return 24
    }()
}

/// A node's name, retyped from the glyph alphabet as its level opens
/// (`ScrambleText`, `ManualScreen.kt:683`) — **answer 30**, "bring the
/// scramble back (it is meant to impress), leaving the accessibility label
/// alone".
///
/// ### Only the drawn glyphs change
///
/// The effect is `redacted`-style: it replaces characters in the **string
/// being drawn** and nothing else. Two things make that safe, and they are the
/// two objections the first port left it out over:
///
///  * **VoiceOver never reads gibberish.** The accessibility label of this
///    `Text` is the real name, and the label of the control it sits in is
///    `"<title>: <subtitle>"` set on the ``ManualMapNode`` button, so the
///    scrambled run is not any element's label for a single frame. Android's
///    scramble *is* its node's text, which is why its `contentDescription`
///    being set at `:650` is the thing that saves it there too — but here it
///    is said twice, because a case that reaches a node by its name
///    (``manualNode``, every map case in this item) must not be racing an
///    animation.
///  * **The glyph run is exactly as wide as the name.** The name is drawn
///    monospaced (`.system(.footnote, design: .monospaced)`), every glyph in
///    ``manualScrambleGlyphs`` is one ASCII character, spaces are left where
///    they are, and the scrambled string has the same character count as the
///    real one — so it occupies the same advance, wraps at the same place, and
///    the card cannot change height while it runs. That was the other reason
///    it was dropped: in a proportional face a run of `#%&*` *is* wider than
///    the name, and a node whose height is a `minHeight` would have grown
///    under it.
///
/// Under Reduce Motion the name is drawn straight — the same cut the 280 ms
/// zoom takes (``ManualMapStage``), and Android's own `reduce` branch
/// (`:686`).
struct ManualScrambleName: View {

    let text: String
    let levelKey: String
    let reduceMotion: Bool

    /// What is on screen. Empty until the first frame, and empty means the
    /// real name: a node must never draw blank, not even for one frame.
    @State private var shown = ""

    var body: some View {
        Text(shown.isEmpty ? text : shown)
            .font(.system(.footnote, design: .monospaced).weight(.semibold))
            .multilineTextAlignment(.leading)
            .fixedSize(horizontal: false, vertical: true)
            // The real name, always — see this view's note.
            .accessibilityLabel(text)
            // `LaunchedEffect(key, text)` (`:685`): the name scrambles again
            // when the level changes, and when the document changes under it,
            // and at no other time. A `.task(id:)` is cancelled and restarted
            // on exactly those two.
            .task(id: "\(levelKey)\u{1}\(text)") { await scramble() }
    }

    private func scramble() async {
        guard !reduceMotion else {
            shown = text
            return
        }
        let letters = Array(text)
        let steps = ManualScrambleClock.steps
        for frame in 0...steps {
            // `i < frame / steps * length` (`:692`): the name settles from
            // the left, a slice at a time.
            let settled = Int(Double(frame) / Double(steps) * Double(letters.count))
            shown = String(
                letters.enumerated().map { at, letter in
                    at < settled || letter == " "
                        ? letter
                        : (manualScrambleGlyphs.randomElement() ?? letter)
                }
            )
            do {
                try await Task.sleep(for: ManualScrambleClock.frame)
            } catch {
                // Cancelled — the level closed or the document changed. The
                // real name is restored below either way, so a cancelled
                // scramble never leaves glyphs on screen.
                break
            }
        }
        shown = text
    }
}

/// A piece's `preview` and `plugin` words, in the document's own wording
/// (`MapNode`'s folded pills, `ManualScreen.kt:660`, and `PiecePage`'s,
/// `:755`).
func manualPieceTags(_ piece: ManualPiece, _ doc: ManualDoc) -> [String] {
    var tags: [String] = []
    if piece.preview {
        tags.append(doc.label("preview", "text", fallback: ManualStrings.Fallback.preview))
    }
    if piece.plugin != nil {
        tags.append(doc.label("plugin", "text", fallback: ManualStrings.Fallback.plugin))
    }
    return tags
}

/// A topic's glyph (`TOPIC_ICONS`, `ManualScreen.kt:171`).
///
/// **A choice, not a transcription**, for ``Route/systemImage``'s reason:
/// there is no mapping from Material's names to SF Symbols, so each one is
/// the nearest symbol Apple ships, picked against the Material glyph. A topic
/// the document adds later falls to the same `HelpOutline` the Kotlin falls
/// to.
func manualTopicIcon(_ topic: String) -> String {
    switch topic {
    case "how-it-works": return "cpu"
    case "things-to-say": return "bubble.left"
    case "rooms-and-devices": return "dot.radiowaves.left.and.right"
    case "your-phone": return "iphone"
    case "music-and-media": return "music.note"
    case "chat-files-calendar": return "folder"
    case "chat-and-help": return "bubble.left.and.bubble.right"
    case "documents-and-letters": return "doc.text"
    case "people-and-privacy": return "person.2"
    case "keeping-it-running": return "wrench.and.screwdriver"
    default: return "questionmark.circle"
    }
}
