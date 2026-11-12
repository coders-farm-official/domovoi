import DomovoiKit
import SwiftUI

// ---------------------------------------------------------------------------
// The map: the house as a diagram you can zoom into.
//
// `ManualScreen.kt:481` (`ring`), `:503` (`ManualMap`) and `:641` (`MapNode`).
// Two levels — the centre with its ring of topics, and one topic's cluster of
// pieces — and a camera that moves between them.
//
// **The arithmetic is ported line for line**, and so is the ornament: the
// name scrambles into place (``ManualScrambleName``, answer 30), on every
// move down and up (answer 76); the cards float (``ManualMapNode``'s
// `floatPeriod`, answer 31); and the camera glides between the map and a
// cluster over 280 ms (``ManualMapStage``, answer 73). All three stop under
// Reduce Motion, as the Kotlin's `reduce` branches do (`ManualScreen.kt:549`,
// `:560`, `:687`).
//
// One thing of Android's is **not** here, measured and written up where it
// happens: the wires do not follow the float (``ManualMapWires``). They do
// follow the camera.
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
///
/// **One stage for the map and every cluster** (the owner's answer 73):
/// ``ManualLevelView`` keeps this view's identity across a move between them,
/// so `focus` changes in place and the camera glides from one to the other.
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
            let hub = focus.flatMap { geo.at["h:\($0.id)"] }
            let camera = ManualCamera.looking(
                atHubX: hub.map { Double($0.x) },
                y: hub.map { Double($0.y) },
                cluster: Double(manualCluster)
            )
            ZStack(alignment: .topLeading) {
                ManualMapWires(
                    camera: camera,
                    lines: worldWires(geo),
                    size: space.size
                )
                nodes(geo, shown: shownIDs, nodeWidth: nw, nodeHeight: nh)
                    .frame(width: w, height: h, alignment: .topLeading)
                    .modifier(ManualCameraEffect(camera: camera))
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
        // **The 280 ms camera glide** — Android's three `animateFloatAsState`s
        // on `k`, `cx` and `cy` with `tween(MOVE_MS, easing = EASE)`
        // (`ManualScreen.kt:549-552`), keyed on the one thing that moves the
        // camera. The camera is animated as those three numbers
        // (``ManualCameraEffect``, ``ManualMapWires``), not as a scale and an
        // offset, so its path is Android's (``ManualCamera`` has why that is
        // a different path). Under Reduce Motion it is `nil` — no animation,
        // the instant cut Android's `snap()` is.
        .animation(reduceMotion ? nil : .manualGlide, value: focus?.id)
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

    /// The level's wires in **world** coordinates; ``ManualMapWires`` puts
    /// them on the stage through the camera, every frame of a glide.
    private func worldWires(_ geo: ManualMapGeo) -> [ManualMapWire] {
        edgeIDs.compactMap { pair in
            guard let pa = geo.at[pair.0], let pb = geo.at[pair.1] else { return nil }
            return ManualMapWire(from: CGPoint(x: pa.x, y: pa.y), to: CGPoint(x: pb.x, y: pb.y))
        }
    }

    /// Only the nodes of the level on screen are built — Android's `id in
    /// shown` (`:621`), with the fade in and out it puts on each one.
    ///
    /// Android composes every node of every level and fades the others to
    /// nothing (`AnimatedVisibility`). Drawing the other level at zero opacity
    /// here would leave it in the accessibility tree, where VoiceOver would
    /// read a piece that is not on screen and a test asking "is the radio node
    /// gone" would be answered by a node nobody can see. So a node is
    /// inserted and removed, and the **transition** is Android's fade:
    /// `FADE_MS` with Compose's default easing (``ManualGlide/fade``), or
    /// nothing at all under Reduce Motion (`snap()`, `:622-623`). A node on
    /// its way out stays in the tree for those 180 ms.
    ///
    /// A move between the map and a cluster draws a different set of ids
    /// (`domovoi`/`t:<id>` against `h:<id>`/`p:<id>`), so every card of the
    /// level arrived at is a new view and its name retypes from the start —
    /// the owner's answer 76, on the way down and on the way up.
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
                        // `5.5 + (i % 4) * 0.9` seconds (`:562`), or zero —
                        // Reduce Motion, or a UI case that turned the float
                        // off. Both gates are in
                        // ``manualNodeFloatPeriod(at:reduceMotion:floatOn:)``,
                        // in DomovoiKit rather than in this ternary, because a
                        // decision no test could reach is how review 2's S3
                        // came to ship broken.
                        floatPeriod: manualNodeFloatPeriod(
                            at: at,
                            reduceMotion: reduceMotion,
                            floatOn: ManualFloatClock.on
                        ),
                        // Android phases the sine by `i * 0.7` seconds
                        // (`:563`). Here it is a one-shot delay on a
                        // repeating animation, so the wrap in
                        // ``manualFloatPhase(at:)`` is load-bearing: it is
                        // how long a card stands still before its first bob.
                        floatPhase: manualFloatPhase(at: at)
                    ) {
                        manual.go(node.opens)
                    }
                    .frame(width: nw)
                    .scaleEffect(pos.s)
                    .offset(x: pos.x - nw / 2, y: pos.y - nh / 2)
                    .transition(reduceMotion ? AnyTransition.identity : AnyTransition.opacity.animation(.manualFade))
                }
            }
        }
    }

    /// The key the names' scramble restarts on: **which level, and which
    /// arrival at it** — the Kotlin's `levelKey` (`:593`, handed to every
    /// `MapNode` at `:633`) with a count after it.
    ///
    /// Android retypes every node on every move, down and up (the owner's answer
    /// 76), because `levelKey` changes inside one composition and every
    /// node's `LaunchedEffect(key, text)` (`:686`) re-fires. Here a move
    /// between the map and a cluster inserts new cards (``nodes``), so the
    /// retype starts with them; the arrival count
    /// (``ManualModel/arrivals(of:)``) changes the key on every arrival as
    /// well, so a card SwiftUI ever kept across a move would still retype.
    ///
    /// `#` separates them because a topic's id is a slug and cannot contain
    /// one, so `map#2` is unambiguous and readable in a log.
    private var levelKey: String {
        "\(focus?.id ?? "map")#\(manual.arrivals(of: manual.place))"
    }

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

extension Animation {

    /// The camera's glide: `tween(MOVE_MS = 280, easing = CubicBezierEasing(
    /// 0.2f, 0f, 0f, 1f))` (`ManualScreen.kt:166-168`, `:549`).
    static var manualGlide: Animation {
        .timingCurve(
            ManualGlide.curve.x1, ManualGlide.curve.y1,
            ManualGlide.curve.x2, ManualGlide.curve.y2,
            duration: ManualGlide.duration
        )
    }

    /// A node's fade: `tween(FADE_MS = 180)` (`:167`, `:622-623`), whose
    /// easing is Compose's default `FastOutSlowInEasing`.
    static var manualFade: Animation {
        .timingCurve(
            ManualGlide.fadeCurve.x1, ManualGlide.fadeCurve.y1,
            ManualGlide.fadeCurve.x2, ManualGlide.fadeCurve.y2,
            duration: ManualGlide.fade
        )
    }
}

/// The camera, as the transform of the layer the nodes sit in — Android's
/// `graphicsLayer { scaleX = k; scaleY = k; translationX = (w / 2 - cx * k) … }`
/// (`ManualScreen.kt:585-591`).
///
/// A `GeometryEffect` whose animatable data is the camera's **three numbers**,
/// so a glide interpolates `k`, `cx` and `cy` one by one and the transform is
/// composed from them every frame, as Android's is. A `scaleEffect` beside an
/// `offset` would interpolate the scale and the translation instead, and the
/// translation is a product of the two — a different path across the stage.
struct ManualCameraEffect: GeometryEffect {

    var camera: ManualCamera

    nonisolated var animatableData: AnimatablePair<Double, AnimatablePair<Double, Double>> {
        get { AnimatablePair(camera.k, AnimatablePair(camera.x, camera.y)) }
        set { camera = ManualCamera(k: newValue.first, x: newValue.second.first, y: newValue.second.second) }
    }

    nonisolated func effectValue(size: CGSize) -> ProjectionTransform {
        let shift = camera.translation(width: Double(size.width), height: Double(size.height))
        return ProjectionTransform(
            CGAffineTransform(
                a: CGFloat(camera.k), b: 0,
                c: 0, d: CGFloat(camera.k),
                tx: CGFloat(shift.x), ty: CGFloat(shift.y)
            )
        )
    }
}

/// The dashed cubic wires (`:567`), put on the stage through the camera.
///
/// **They follow the camera, every frame of a glide**: this view is
/// `Animatable` over the same three numbers as ``ManualCameraEffect``, so
/// SwiftUI hands it each intermediate camera and the `Canvas` redraws — which
/// is what Android's `Canvas` does reading `k`, `cx` and `cy` (`:573-576`).
///
/// **They do not follow the float.** Android also reads its bob inside that
/// `Canvas` and so redraws every wire 60 times a second for as long as the map
/// is open; here the float is one `repeatForever` offset per card on the
/// render server, and the wires stand still between glides. A wire runs
/// centre to centre between two ~104-point cards, so the part of it that
/// would move is the part covered by the cards. That is the one piece of the
/// float given up, deliberately (answer 31's battery cost).
///
/// Hidden from VoiceOver — it is a drawing of the links the nodes beside it
/// already are.
struct ManualMapWires: View, Animatable {

    var camera: ManualCamera
    /// In world coordinates.
    let lines: [ManualMapWire]
    let size: CGSize

    nonisolated var animatableData: AnimatablePair<Double, AnimatablePair<Double, Double>> {
        get { AnimatablePair(camera.k, AnimatablePair(camera.x, camera.y)) }
        set { camera = ManualCamera(k: newValue.first, x: newValue.second.first, y: newValue.second.second) }
    }

    var body: some View {
        // The points are worked out **before** the renderer, so the closure
        // captures a list of numbers and not this view.
        let onStage = lines.map { line in
            let a = camera.onStage(
                x: Double(line.from.x), y: Double(line.from.y),
                width: Double(size.width), height: Double(size.height)
            )
            let b = camera.onStage(
                x: Double(line.to.x), y: Double(line.to.y),
                width: Double(size.width), height: Double(size.height)
            )
            return ManualMapWire(from: CGPoint(x: a.x, y: a.y), to: CGPoint(x: b.x, y: b.y))
        }
        return Canvas { context, _ in
            for line in onStage {
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
/// (``floatAnimation``) — answer 31. Both are ornament and both stop under
/// Reduce Motion.
struct ManualMapNode: View {

    let node: ManualMapNodeContent
    let height: CGFloat

    /// Which level this card belongs to **and which arrival at it**, so the
    /// name scrambles again each time a level opens — on the way up as well
    /// as down — and not on every redraw. The Kotlin's `levelKey` (`:593`,
    /// passed to `MapNode` at `:633`), with an arrival count;
    /// ``ManualMapStage``'s `levelKey` has the reasoning.
    let levelKey: String

    /// Whether this person asked for less movement: no scramble, no float.
    let reduceMotion: Bool

    /// How long one bob takes, and how far into it this card starts
    /// (`bob`, `:559`: `5.5 + (i % 4) * 0.9` seconds at `:562`, phased by
    /// `i * 0.7` at `:563`). **Zero period means the card is still** —
    /// Reduce Motion, or a UI case with the float off, and the caller decides
    /// which (``ManualMapStage``).
    let floatPeriod: Double
    let floatPhase: Double

    let action: () -> Void

    /// The glyph's width is scaled, never a fixed `frame(width:)` — the
    /// standing rule, and the icon sits beside a name that grows.
    @ScaledMetric(relativeTo: .body) private var glyph: CGFloat = 15

    /// Flipped whenever ``floats`` changes, so the repeating animation below
    /// has a value change to animate — ``PrinterStatusDot``'s shape, which is
    /// this app's one other thing that never stops moving.
    @State private var floating = false

    /// Whether the name is mid-scramble, written by ``ManualScrambleName``.
    ///
    /// It is here, on the control, and not kept private inside that view
    /// because the *control* is the only thing a UI case can see: a node's
    /// name is drawn inside a `Button`, and SwiftUI merges a button's children
    /// into one accessibility element, so an identifier or a label on the
    /// inner `Text` is not queryable. See ``scrambleState``.
    @State private var nameScrambling = false

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
                        reduceMotion: reduceMotion,
                        scrambling: $nameScrambling
                    )
                }
                if !node.subtitle.isEmpty || !node.tags.isEmpty {
                    Text(secondLine)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                        // **Hidden, and the hide is a no-op here — measured
                        // three ways in train I005.** It is kept for one
                        // reason, below.
                        //
                        // What I005's `manual-audit-topic` and
                        // `manual-audit-map` trees show (the audit cases'
                        // attachments):
                        //
                        //  1. this line is **in** the tree, with its hide
                        //     applied: `StaticText … label: 'where it listens'`
                        //     under the card's `Button`;
                        //  2. so is the icon above, whose own
                        //     `.accessibilityHidden(true)` is older than this
                        //     branch: `Image … label: 'Radio'`;
                        //  3. and Apple's audit **measured** this element on
                        //     the topic level, hide and all — the one finding
                        //     in train I005's manual was against the piece
                        //     node's second line
                        //     (`'one room, end to end   preview '`,
                        //     `frame=(25.0, 461.3, 99.7, 30.3)`).
                        //
                        // Inside a `Button`'s label SwiftUI keeps the leaves
                        // and drops what was said about them —
                        // `ios/Domovoi/News/NewsPeopleChipRow.swift:94` has
                        // the app-wide measurement, including that
                        // `accessibilityElement(children: .ignore)` only adds
                        // a wrapper around the control. That, and the three
                        // witnesses in this one tree, are what the claim
                        // rests on.
                        //
                        // It was written here that the wires were a fourth
                        // witness — same modifier, outside any `Button`,
                        // absent from the same tree. They are not:
                        // ``ManualMapWires`` is a bare `Canvas` with no
                        // accessibility content of its own, so it is absent
                        // from an XCUITest tree with the modifier and
                        // without it. Its absence says nothing either way.
                        //
                        // **Why it stays.** Between I004 and I005 this
                        // element's frame went back from the whole
                        // `{177.7 × 104}` card to its own `{85.3 × 14.3}`
                        // glyphs, and this modifier is the only edit it took
                        // in between (I004 flagged the card-sized rectangle
                        // twice at 1.35:1; I005's map audit is clean). That
                        // is a correlation and not a mechanism — nothing here
                        // can run a `NavigationStack` or an audit — so the
                        // modifier is left where it is rather than removed to
                        // find out on the owner's screen.
                        //
                        // **What VoiceOver reads is the card's own label**
                        // below, `"<title>: <subtitle>"`
                        // (``manualNodeReading(title:subtitle:)``), which is
                        // Android's `contentDescription` (`ManualScreen.kt:655`):
                        // a `Button` is one stop whatever its label content
                        // enumerates as. A piece node's `preview`/`plugin`
                        // tags are in neither — they are runs of this line
                        // and the label never had them — as TalkBack does not
                        // announce them either (`:655` joins the title and
                        // the subtitle and stops).
                        .accessibilityHidden(true)
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
        // .joinToString(": ")` (`:655`), so a node reads as one thing rather
        // than as two labels and a pair of pills.
        //
        // **This is what keeps the scramble out of VoiceOver's way, and it
        // is the only thing that does.** The label is the node's real name,
        // set here on the control, and a `Button` is **one** VoiceOver stop:
        // whatever its label content enumerates as in an XCUITest tree, the
        // reading is this string. So a case that reaches a node by its name
        // reaches it mid-scramble exactly as it does at rest
        // (``testManual_ANodeIsNamedWhileItsNameScrambles``).
        //
        // It was written here that ``ManualScrambleName`` being hidden made
        // the drawn glyphs "nobody's label". Train I005 says otherwise: with
        // that hide in place the glyph frame is still a `StaticText` of its
        // own under this `Button` (`'Domovoi'`, `'Domovo9'` and
        // `'your home'` in the card at
        // `testManual_ANodeIsNamedWhileItsNameScrambles`'s failure, which is
        // what that case counted). The hide is a no-op inside a `Button`'s
        // label — the second line above has the three witnesses — so this
        // label is the belt and there are no braces.
        //
        // It is the only reading of the second line too (above), so the join
        // is a rule with a test of its own rather than a ternary in a view
        // body: ``manualNodeReading(title:subtitle:)``. A label that lost
        // half of itself would leave a node announced by its title alone
        // with nothing objecting.
        .accessibilityLabel(manualNodeReading(title: node.title, subtitle: node.subtitle))
        // The one thing a UI case can read about the scramble — see
        // ``scrambleState``. The **empty** string in every build that could
        // ship, and in a DEBUG build nobody passed the argument to, which is
        // no value at all: VoiceOver never hears it and the label above,
        // which is the node's real name, is untouched either way.
        .accessibilityValue(scrambleState)
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
        // between glides (see ``ManualMapWires``). Almost all
        // of a wire is behind the two cards it joins — they are ~104 points
        // tall and it runs centre to centre — so what moves is hidden and what
        // shows does not move.
        //
        // Nothing moves under Reduce Motion: the caller passes a zero period,
        // which is the same cut Android's own `bob` takes (`:560`,
        // `if (reduce || id !in shown) return 0f`).
        //
        // The zero arm of the offset is not decoration either — a still card
        // has to sit **exactly** where the geometry put it, and without it
        // `floating` flipping unanimated would leave every node 5 points
        // above its own position.
        .offset(y: floats ? (floating ? -5 : 5) : 0)
        .animation(floatAnimation, value: floating)
        // **`onChange` and not `onAppear`.** ``floatPeriod`` can go to zero
        // and back over one card's lifetime — Reduce Motion switched on and
        // off while the map is open — so
        // `floats` goes false and true again, and an `onAppear` that set
        // `floating = true` once would leave the card pinned at the top of
        // its arc with nothing left to animate. Driving `floating` *from*
        // `floats` gives the repeating animation a value change every time
        // the card starts bobbing again; `initial: true` is the first
        // appearance, exactly what `onAppear` did.
        .onChange(of: floats, initial: true) { _, now in floating = now }
    }

    /// Whether this card bobs at all. Zero period is a still card — Reduce
    /// Motion, or a UI case that turned the float off.
    private var floats: Bool { floatPeriod > 0 }

    /// One bob, forever, or nothing at all.
    ///
    /// **`delay` goes outside `repeatForever`, and the order is the bug the
    /// review caught.** `.easeInOut(…).delay(phase).repeatForever(…)` makes
    /// the delay part of the unit that repeats, so it is spent on *every*
    /// cycle: a card with a 4.9 s phase would hold still at the top or the
    /// bottom of its arc for 4.9 s between bobs, which is a stutter, not a
    /// stagger. Android's bob is a continuous sine with the phase inside it
    /// (`5f * sin(2π(t + i*0.7)/dur)`, `ManualScreen.kt:562-563`) and never
    /// pauses. Delaying the repeating animation as a whole is the closest
    /// SwiftUI gets: the card waits once, then bobs forever.
    ///
    /// Written out rather than built inside `.animation(…)`: a chain of
    /// implicit members in the arm of a ternary whose other arm is `nil` is
    /// more inference than this needs to ask for.
    private var floatAnimation: Animation? {
        guard floats else { return nil }
        return .easeInOut(duration: floatPeriod)
            .repeatForever(autoreverses: true)
            .delay(floatPhase)
    }

    /// `"scrambling"` or `"settled"` while a UI case has asked for a slow
    /// scramble, and `""` the rest of the time.
    ///
    /// ### Why there is a seam here at all
    ///
    /// The scramble's whole point is that the drawn glyphs are not the
    /// **control's** label and not its value (see ``ManualScrambleName``):
    /// the card reads as the node's real name throughout, so nothing a UI
    /// case can reach the card by says whether the effect is running. (A
    /// *leaf* under the card does carry the glyph run — train I005 measured
    /// `'Domovo9'` there — but it is a decoration's frame, 216 ms old and
    /// gone, and a case that waited on one would be waiting on `Random`.)
    /// The review's finding was the consequence:
    /// `testManual_ANodeIsNamedWhileItsNameScrambles` passed with
    /// ``ManualScrambleName`` deleted outright, because the label it checks
    /// predates the effect. A case that cannot fail is not a test.
    ///
    /// So one bit of state is exposed — *whether* a scramble is in flight,
    /// never what it is drawing — and only to a process that asked for
    /// `-domovoi-manual-scramble`, which is DEBUG-only
    /// (``ManualScrambleClock``). In anything that could ship this property is
    /// the empty string and this file behaves exactly as it reads.
    ///
    /// It is a value and not a label on purpose: a label is the node's name
    /// and that claim is the other half of the same case.
    private var scrambleState: String {
        guard ManualScrambleClock.slowed else { return "" }
        return nameScrambling ? "scrambling" : "settled"
    }

    /// The subtitle with the `preview` and `plugin` words folded into it
    /// (`:660`), each washed as the Kotlin washes them
    /// (`SpanStyle(background = c.idleSoft)`, `:668`).
    ///
    /// The wash is ``Color/manualNodeWash`` and **not** the
    /// ``Color/manualCodeWash`` a page's inline runs take. That is not a
    /// style preference: this card is `secondarySystemBackground`, the page's
    /// prose is on the page, and `--sunken` on a `secondarySystemBackground`
    /// card measures **1.008:1** — a wash with no visible tint at all, which
    /// is how the first version of this shipped and what the review caught.
    /// Android has two tokens here for the same reason (`idleSoft` over
    /// `card` at `:668`, `sunken` over the page at `:872`), and this is its
    /// first one, composited opaque. **1.115:1** against the card light and
    /// **1.206:1** dark, with `fgMuted` over it at **5.98:1** and **5.68:1**;
    /// body text needs 4.5:1. It used to be plain text, because a translucent
    /// inline background has no ratio to state; an opaque one does.
    private var secondLine: AttributedString {
        var out = AttributedString()
        if !node.subtitle.isEmpty { out += AttributedString(node.subtitle) }
        for tag in node.tags {
            if !out.characters.isEmpty { out += AttributedString("  ") }
            var run = AttributedString(" \(tag) ")
            run.backgroundColor = Color.manualNodeWash
            out += run
        }
        return out
    }
}

// MARK: - the name that scrambles into place

/// How fast a name scrambles: nine frames, 24 ms apart (`ScrambleText`,
/// `:688` — `steps = 8`, `delay(24)`), so 216 ms of retyping, starting the
/// moment the level arrives, as Android's does.
///
/// The alphabet and the per-frame arithmetic are **not** here. They are
/// ``manualScrambleGlyphs`` and
/// ``manualScrambleFrame(of:frame:steps:glyph:)`` in DomovoiKit, where a
/// package test can reach them: with the loop inlined in a view's `.task`,
/// the settle arithmetic — the half of answer 30 that can be wrong without
/// anybody seeing it on a screenshot — had no test anywhere, which is what
/// the review found.
///
/// There used to be a 350 ms **lead-in**, frame 0 held for the length of a
/// navigation push, because a level was a pushed screen and 216 ms of
/// retyping started at the push was spent while the page was still sliding
/// (review 3's F3). Since answer 73 nothing slides: a move to the map or a
/// piece is a cut, and a move between the map and a cluster is Android's
/// 280 ms glide, during which Android's own names retype in full view
/// (`focus` changes inside one composition, `:593`). So the lead-in is gone
/// and the timing is Android's.
enum ManualScrambleClock {

    static let steps = manualScrambleSteps

    /// 24 ms, unless a DEBUG launch argument slows it down.
    static var frame: Duration { .milliseconds(frameMS) }

    /// Whether a case asked for a slow scramble. The only state in which a
    /// node publishes ``ManualMapNode/scrambleState``.
    static var slowed: Bool { frameMS != 24 }

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

/// Whether the cards bob at all — the float's one knob.
///
/// ### Why a UI test turns it off
///
/// Ten cards each carrying a `repeatForever` animation are ten animations
/// that never finish, and XCUITest's every query waits for the app under test
/// to go idle first. This app already ships forever-animating layers
/// (``PrinterStatusDot``, every `ProgressView`), but one small dot is not ten
/// cards filling the screen a whole suite of cases is driving, and the cost of
/// being wrong is every Manual case idle-timing-out on a shared train.
///
/// So the float is **off** for the cases in `StubHouseUITests+Manual.swift`,
/// which all launch through `manualLaunch(…)` and pass
/// `-domovoi-manual-float-off`, and **on** everywhere else — including in one
/// case of its own (`testManual_TheMapWorksWhileItsNodesFloat`) that passes
/// `-domovoi-manual-float-on` and drives the map with the cards moving. If
/// forever-animating nodes do hang XCUITest, that is the one case that hangs
/// and the rest of the screen stays testable; if they do not, the knob costs
/// nothing.
///
/// Both arguments are DEBUG-only and read once at launch, like
/// ``ManualScrambleClock`` and ``AuditStrip``: a shipping build always floats.
enum ManualFloatClock {

    static let on: Bool = {
        #if DEBUG
        let args = ProcessInfo.processInfo.arguments
        if args.contains("-domovoi-manual-float-off") { return false }
        if args.contains("-domovoi-manual-float-on") { return true }
        #endif
        return true
    }()
}

/// A node's name, retyped from the glyph alphabet as its level opens
/// (`ScrambleText`, `ManualScreen.kt:683`) — **answer 30**, "bring the
/// scramble back (it is meant to impress), leaving the accessibility label
/// alone".
///
/// ### The real name is what the card is measured by
///
/// The glyph frame is drawn in an **overlay** over the real name, which is
/// what occupies the layout; the real name is only made transparent. So the
/// card's width, its height and its wrap points come from `text` and from
/// nothing else, at every frame, and a scramble **cannot** reflow the card.
///
/// That is the review's finding, and the first version of this argued its way
/// past it: it drew the glyph frame as the text, and reasoned that a
/// monospaced face plus single-ASCII glyphs plus an unchanged character count
/// meant an unchanged layout. The arithmetic was right and the conclusion was
/// wrong — `/` is in ``manualScrambleGlyphs`` and opens a line-break
/// opportunity where the letter it replaced had none, so a two-line name near
/// its wrap boundary could take a third line mid-effect, and this card is a
/// `minHeight` with no `lineLimit`, so it would have grown and taken the
/// stage with it. Android cannot have that bug because its node is a fixed
/// height and its name is `maxLines = 2, overflow = Ellipsis`
/// (`ManualScreen.kt:653`, `:703`); this port keeps neither, on purpose, so
/// that a name can grow with Dynamic Type. The overlay is how it keeps both
/// things.
///
/// A glyph frame that *would* have needed more room is clipped to the real
/// name's box rather than allowed to push anything — 216 ms of a clipped
/// glyph, against a card that resizes under the reader's finger.
///
/// ### VoiceOver never reads gibberish — and train I004 said the first
/// version of this did
///
/// The effect replaces characters in the **string being drawn** and nothing
/// else, and the control these sit in carries `"<title>: <subtitle>"`
/// (``ManualMapNode``), which is the label every case in this item reaches a
/// node by.
///
/// That was argued here before it was measured, and the measurement came back
/// against it. `overnight-trains/I004/ALL/testManual_AuditsClean.log`, the
/// `manual-audit-map` tree, has **two** static texts per node at one frame:
///
///     StaticText, {{53.1, 416.3}, {120.7, 15.7}}, label: 'Rooms & devices'
///     StaticText, {{53.1, 416.3}, {120.7, 15.7}}, label: 'Rooms & deX76H0'
///
/// — the laid-out name and the glyph overlay, in the tree together, with the
/// overlay's own `.accessibilityHidden(true)` not having removed it. So the
/// drawn glyphs *are* a leaf's label — train I005 measured the same thing
/// again (`['Domovoi', 'Domovo9', 'your home']`) — and what answer 30's half
/// ("leaving the accessibility label alone") is kept by is the control's
/// label, not the leaves: see the section below, and DECISIONS D-HX2 for why
/// there is nothing to remove them with. The same
/// audit returned two `Contrast failed` findings it could not attribute to any
/// element.
///
/// **Those findings are not this view's, and the first account of them said
/// they were.** It claimed a transparent text and a second text in its frame
/// were "the only two things that changed about that tree". A third thing
/// changed, and it is where the arithmetic lands: the node's **second line**
/// went from a `String` to an `AttributedString` with answer 34, and its
/// accessibility frame went from `{85.3 × 14.3}` to the whole `{177.7 × 104}`
/// card with it — in the scrambling tree and the settled one alike
/// (``ManualMapNode``'s second line has the measurement and the ratios).
/// Sampled off I004's own screenshot, the glyph frames measure
/// `(0,0,0)` on `(242,242,247)`, **18.82:1**, the same ratio I002's clean
/// audit measured in the same rectangles — the gibberish is the same black as
/// the name, in a different number of pixels (2305 against 2130), so the
/// rectangles are the same picture outside the name's own frame and not
/// inside it. A re-lettering cannot fail a contrast check, so the hide below
/// was never the audit's fix. Nor, it turns out, was the second line's: train
/// I005 audited a map with both hides in place and came back clean, and in
/// the same train Apple's audit **measured** the hidden second line on the
/// topic level. What changed between the two audits is the frame
/// (``ManualMapNode``'s second line has it, as a correlation).
///
/// The modifier below was the answer to that, and **train I005 measured that
/// it is a no-op**: with the hide on the composed name, the glyph frame is
/// still a static text under the card. I005's
/// `testManual_ANodeIsNamedWhileItsNameScrambles` counted a card holding
/// `["Domovoi", "Domovo9", "your home"]`, and the same tree holds the icon,
/// which has carried `.accessibilityHidden(true)` since long before any of
/// this. Inside a `Button`'s label SwiftUI keeps the leaves and drops what
/// was said about them; ``ManualMapNode``'s second line has the three
/// witnesses in that one tree, and `NewsPeopleChipRow.swift:94` the app-wide
/// measurement.
///
/// So the reading is ``ManualMapNode``'s label and nothing else, and that is
/// enough for answer 30's half: a `Button` is **one** VoiceOver stop, so what
/// a reader hears on a card is `"<title>: <subtitle>"` whether or not the
/// tree enumerates the glyphs beneath it. The modifier stays — it is free, it
/// is the right thing to say about a decoration, and it would matter the day
/// this name is drawn outside a control — but nothing rests on it, and no
/// case asserts it works. What the two counting cases hold now is the thing
/// that *can* be wrong: no leaf under a card carries a word the card does not
/// draw. Mid-scramble that allows one frame of the name's own glyph run,
/// because I005 measured it in the tree and no modifier takes it out
/// (`StubHouseUITests+Manual.swift`'s `manualIsAScrambleFrame(_:of:)`); at
/// rest it allows nothing but the card's two words, where a glyph run means
/// the effect never settled.
///
/// It stays in the **layout** and in the picture: `.accessibilityHidden` is
/// not `.hidden()`, so the card is still measured by the real name and the
/// scramble still draws.
///
/// Under Reduce Motion the name is drawn straight — Android's own `reduce`
/// branch (`:687`), and the same cut the float takes
/// (``ManualMapNode/floatPeriod``).
struct ManualScrambleName: View {

    let text: String
    let levelKey: String
    let reduceMotion: Bool

    /// Raised while a frame of glyphs is on screen, for the control that owns
    /// this name to publish: ``ManualMapNode/scrambleState`` is the only thing
    /// a UI case can read about the effect, and a `Button`'s children are
    /// merged into one accessibility element, so it cannot live down here.
    @Binding var scrambling: Bool

    /// The frame on screen, or `""` for "the real name, as laid out".
    @State private var glyphs = ""

    var body: some View {
        name(text)
            // Transparent, not absent: it is still the thing being measured.
            .opacity(glyphs.isEmpty ? 1 : 0)
            .overlay(alignment: .topLeading) {
                if !glyphs.isEmpty {
                    name(glyphs)
                        // The overlay is proposed the real name's size; this
                        // frame takes all of it and the clip keeps a glyph
                        // frame that wanted more lines inside it.
                        .frame(
                            maxWidth: .infinity,
                            maxHeight: .infinity,
                            alignment: .topLeading
                        )
                        .clipped()
                }
            }
            // Both copies, said once from out here — and a no-op inside a
            // `Button`'s label, which train I005 measured and this view's
            // note has. ``ManualMapNode``'s label is what the card reads as.
            .accessibilityHidden(true)
            // `LaunchedEffect(key, text)` (`:686`): the name scrambles again
            // when the level changes, and when the document changes under it,
            // and at no other time. A `.task(id:)` is cancelled and restarted
            // on exactly those two.
            .task(id: "\(levelKey)\u{1}\(text)") { await scramble() }
    }

    /// The name, drawn. One function, so the laid-out copy and the overlay
    /// cannot be given different fonts — which would make the overlay a
    /// different size from the box it is clipped to.
    private func name(_ string: String) -> some View {
        Text(string)
            .font(.system(.footnote, design: .monospaced).weight(.semibold))
            .multilineTextAlignment(.leading)
            .fixedSize(horizontal: false, vertical: true)
    }

    /// Nine frames of ``manualScrambleFrame(of:frame:steps:glyph:)``, then the
    /// real name.
    ///
    /// The loop is written **here** rather than in DomovoiKit because it is
    /// the timing; the arithmetic inside it is in DomovoiKit because it is the
    /// behaviour.
    ///
    /// ### A cancelled scramble writes nothing
    ///
    /// `try?` and then `guard !Task.isCancelled else { return }` — a **return**
    /// and not a `break` that falls through to the tail, which is what review 3
    /// found. Before S2, a cancellation meant the level had changed and a
    /// *new* stage was being built, so the stale task's two tail writes landed
    /// on a view that was going away and the real name came back on the new
    /// one. S2 changed that premise on purpose: ``ManualMapStage/levelKey``
    /// now changes **in place** on a mounted node, so the cancelled task and
    /// the one that replaced it share this view's `glyphs` and `scrambling`.
    /// The cancelled one resumes from the throwing `Task.sleep` as its own
    /// main-actor continuation, so a tail that ran would blank the live name
    /// for a frame and — worse — publish ``ManualMapNode/scrambleState`` as
    /// `"settled"` while the replacement was mid-scramble, which is the value
    /// a UI case reads. Nothing is left stuck `true` by returning early:
    /// `.task(id:)` cancels on an id change, which starts the replacement that
    /// sets it, or on the view going away, which takes the state with it.
    ///
    /// `@MainActor` and not nonisolated: it writes `@State` and a `@Binding`,
    /// and a nonisolated `async` method of a view that does that is the one
    /// shape Swift 6's isolation checking has an opinion about. `.task`
    /// inherits the actor context, so this is called without a hop.
    @MainActor private func scramble() async {
        guard !reduceMotion else { return }
        let letters = Array(text)
        guard !letters.isEmpty else { return }
        scrambling = true
        for frame in 0...ManualScrambleClock.steps {
            glyphs = manualScrambleFrame(of: letters, frame: frame) {
                // `#` and not `" "` for the never-taken arm: the alphabet is
                // a non-empty literal (``manualScrambleGlyphs``) so
                // `randomElement()` cannot be nil, but a **space** is the one
                // character the effect's own invariant forbids inserting —
                // the spaces never move — and a fallback that contradicts an
                // invariant two files away is a trap for whoever edits the
                // alphabet next. `#` is in the alphabet.
                manualScrambleGlyphs.randomElement() ?? "#"
            }
            try? await Task.sleep(for: ManualScrambleClock.frame)
            guard !Task.isCancelled else { return }
        }
        glyphs = ""
        scrambling = false
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
