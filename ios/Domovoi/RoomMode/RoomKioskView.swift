import Combine
import DomovoiKit
import SwiftUI
import UIKit
import UIKit.UIGestureRecognizerSubclass

/// The room screen (`roommode/RoomModeActivity.kt`, ROOMMODE-MAP §3.4):
/// dark and plain so the phone reads as a room, not as the app — the room's
/// name and a clock, a state ring, an always-visible mic chip, a big Mute,
/// and Exit (held, then the phone's own unlock). The content drifts a few
/// points every minute against burn-in, nothing moves while the room is
/// idle, and after a minute with nothing to show it dims itself.
///
/// Built before any household object (the app's scene switch), and it is
/// what starts and stops the room with the scene: iOS opens a microphone
/// only for the app on screen (§3.1).
struct RoomKioskScreen: View {
    let scene: RoomModeScene
    @StateObject private var model: RoomKioskModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var guidedAccessSheet = false
    #if DEBUG
    @StateObject private var perf = RoomPerfProbe()
    #endif

    init(scene: RoomModeScene) {
        self.scene = scene
        let store = scene.store
        let feed = scene.launch.feed
        _model = StateObject(wrappedValue: RoomKioskModel(
            store: store,
            makeEngine: { RoomModeRuntime.makeEngine(store: store, feed: feed) },
            lock: RoomModeRuntime.lockFacts(fixed: scene.launch.fixedLock),
            screen: RoomScreen(),
            notices: RoomNotices(),
            micGranted: { feed || RoomModeRuntime.micGranted },
            onLeave: { _ in scene.left() }
        ))
    }

    var body: some View {
        RoomKioskView(model: model, onGuidedAccess: { guidedAccessSheet = true }) {
            #if DEBUG
            Text(perf.line)
                .font(.caption2.monospacedDigit())
                .foregroundStyle(RoomInk.dim)
            #else
            EmptyView()
            #endif
        }
        .statusBarHidden()
        .persistentSystemOverlays(.hidden)
        .preferredColorScheme(.dark)
        .onChange(of: scenePhase, initial: true) { _, phase in
            switch phase {
            case .active: model.enteredForeground()
            case .background: model.enteredBackground()
            default: break   // Control Centre, a banner: nothing (§3.1)
            }
        }
        .onAppear {
            model.guidedAccessChanged(UIAccessibility.isGuidedAccessEnabled)
            model.thermalChanged(RoomThermal(ProcessInfo.processInfo.thermalState))
        }
        .onReceive(
            NotificationCenter.default.publisher(for: ProcessInfo.thermalStateDidChangeNotification)
                .receive(on: DispatchQueue.main)
        ) { _ in
            model.thermalChanged(RoomThermal(ProcessInfo.processInfo.thermalState))
        }
        .onReceive(
            NotificationCenter.default.publisher(for: UIAccessibility.guidedAccessStatusDidChangeNotification)
                .receive(on: DispatchQueue.main)
        ) { _ in
            model.guidedAccessChanged(UIAccessibility.isGuidedAccessEnabled)
        }
        .task {
            // Every five seconds: the dimming, the drift, the heat's return
            // and the interruption notice. Nothing on screen changes unless
            // one of them does.
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                model.tick()
                #if DEBUG
                perf.sample()
                #endif
            }
        }
        .sheet(isPresented: $guidedAccessSheet) { RoomGuidedAccessSheet() }
    }
}

/// Android's room inks (`RoomModeActivity.kt:309-312`) on black.
enum RoomInk {
    static let text1 = Color(red: 0xE9 / 255, green: 0xE5 / 255, blue: 0xDE / 255)
    static let text2 = Color(red: 0x8C / 255, green: 0x87 / 255, blue: 0x7F / 255)
    static let dim = Color(red: 0x3A / 255, green: 0x36 / 255, blue: 0x32 / 255)
    static let amber = Color(red: 0.9758, green: 0.6765, blue: 0.1471)
    static let red = Color(red: 1.0, green: 0.3718, blue: 0.3577)
}

struct RoomKioskView<Footer: View>: View {
    @ObservedObject var model: RoomKioskModel
    let onGuidedAccess: () -> Void
    let footer: Footer

    init(model: RoomKioskModel, onGuidedAccess: @escaping () -> Void, @ViewBuilder footer: () -> Footer) {
        self.model = model
        self.onGuidedAccess = onGuidedAccess
        self.footer = footer()
    }

    var body: some View {
        GeometryReader { proxy in
            // An old phone is about 640 points tall: everything a size down
            // there, so the room fits without scrolling.
            let compact = proxy.size.height < 720
            ScrollView {
                content(compact: compact)
                    .padding(24)
                    .frame(maxWidth: .infinity, minHeight: proxy.size.height)
                    .offset(x: model.drift.x, y: model.drift.y)
            }
            .scrollBounceBehavior(.basedOnSize)
        }
        .background(Color.black.ignoresSafeArea())
        // Any touch, for the dimming (D9) — watched by UIKit and never a
        // SwiftUI gesture: a `.simultaneousGesture(TapGesture())` here took
        // every tap the Mute button inside the ScrollView should have had
        // on iOS 18 (train I037: Mute tapped, nothing changed).
        .background(RoomTouchWatcher { model.touched() })
        .alert(RoomModeStrings.exitTitle, isPresented: presenting(.confirmNoLock)) {
            Button(RoomModeStrings.exitConfirm) { model.confirmExit() }
            Button(RoomModeStrings.stayARoom, role: .cancel) { model.dismissDialog() }
        } message: {
            Text(RoomStatusText.confirmNoLock)
        }
        .alert(RoomModeStrings.setALockExitTitle, isPresented: presenting(.setALock)) {
            Button(RoomModeStrings.okay, role: .cancel) { model.dismissDialog() }
        } message: {
            Text(RoomStatusText.setALockToExit)
        }
        .alert(RoomModeStrings.leaveTitle, isPresented: presenting(.keepOrForget)) {
            Button(RoomModeStrings.keepThisRoom) { model.leave(forget: false) }
            Button(RoomModeStrings.forgetThisRoom, role: .destructive) { model.leave(forget: true) }
            Button(RoomModeStrings.stayARoom, role: .cancel) { model.dismissDialog() }
        } message: {
            Text(RoomStatusText.keepOrForget)
        }
    }

    private func presenting(_ dialog: RoomKioskModel.Dialog) -> Binding<Bool> {
        Binding(
            get: { model.dialog == dialog },
            set: { if !$0, model.dialog == dialog { model.dismissDialog() } }
        )
    }

    @ViewBuilder
    private func content(compact: Bool) -> some View {
        let gap: CGFloat = compact ? 12 : 24
        VStack(spacing: 0) {
            HStack {
                Text(RoomModeStrings.roomBrand)
                    .font(.footnote)
                    .foregroundStyle(RoomInk.text2)
                Spacer()
                RoomMicChip(open: model.micOpen, live: model.micLive, muted: model.muted)
            }
            Spacer().frame(height: gap)
            Text(model.label)
                .font(.system(size: compact ? 30 : 40, weight: .semibold))
                .foregroundStyle(RoomInk.text1)
                .multilineTextAlignment(.center)
                .accessibilityAddTraits(.isHeader)
            // Once a minute, not once a second: Android's screen is off while
            // it ticks, this one is not (§3.4).
            TimelineView(.everyMinute) { context in
                Text(context.date.formatted(date: .omitted, time: .shortened))
                    .font(.system(size: compact ? 40 : 56, weight: .light))
                    .foregroundStyle(RoomInk.text2)
            }
            Spacer().frame(height: gap)
            RoomRing(ring: model.ring, animates: model.animates, size: compact ? 150 : 200)
            Spacer().frame(height: gap * 2 / 3)
            if let detail = model.detail {
                Text(detail)
                    .font(.system(size: compact ? 14 : 16))
                    .foregroundStyle(RoomInk.text2)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 8)
                    .accessibilityIdentifier("room-detail")
            }
            if let code = model.approvalCode {
                Spacer().frame(height: 12)
                Text(RoomModeStrings.approvalCodeShown(code))
                    .font(.system(size: compact ? 40 : 48, weight: .semibold).monospacedDigit())
                    .foregroundStyle(RoomInk.amber)
                    .accessibilityLabel(RoomModeStrings.approvalCodeDescription(code))
                    .accessibilityIdentifier("room-approval-code")
                RoomBigButton(title: RoomModeStrings.checkNow, fill: RoomInk.dim, ink: RoomInk.text1, compact: compact) {
                    model.checkNow()
                }
            }
            Spacer().frame(height: gap)
            bigButton(compact: compact)
            Spacer().frame(height: gap * 2 / 3)
            RoomExitControl { model.exitHeld() }
            if model.showsGuidedAccessTip {
                Spacer().frame(height: gap)
                HStack(spacing: 12) {
                    Button(RoomModeStrings.guidedAccessLine, action: onGuidedAccess)
                        .font(.footnote)
                        .foregroundStyle(RoomInk.text2)
                        .underline()
                    Button(RoomModeStrings.guidedAccessDismiss) { model.dismissGuidedAccessTip() }
                        .font(.footnote)
                        .foregroundStyle(RoomInk.text2)
                }
                .multilineTextAlignment(.center)
            }
            Spacer().frame(height: gap)
            footer
        }
    }

    @ViewBuilder
    private func bigButton(compact: Bool) -> some View {
        switch model.action {
        case .mute:
            RoomBigButton(title: RoomModeStrings.mute, fill: RoomInk.dim, ink: RoomInk.text1, compact: compact) {
                model.perform(.mute)
            }
        case .unmute:
            RoomBigButton(title: RoomModeStrings.unmute, fill: RoomInk.amber, ink: .black, compact: compact) {
                model.perform(.unmute)
            }
        case .resume:
            RoomBigButton(title: RoomModeStrings.resume, fill: RoomInk.amber, ink: .black, compact: compact) {
                model.perform(.resume)
            }
        case .openSettings:
            RoomBigButton(title: RoomModeStrings.openAppSettings, fill: RoomInk.amber, ink: .black, compact: compact) {
                model.perform(.openSettings)
                if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
            }
        case .none:
            EmptyView()
        }
    }
}

/// The ring and its word (`RoomModeActivity.kt:362-386`). It moves only
/// during a turn (the pulse, the thinking arc), never while idle, and never
/// when the phone is hot: a paused timeline draws nothing new.
struct RoomRing: View {
    let ring: RoomStatusText.Ring
    let animates: Bool
    let size: CGFloat

    private var color: Color {
        switch ring {
        case .micBlocked: return RoomInk.red
        case .muted, .offline: return RoomInk.dim
        case .idle: return RoomInk.amber.opacity(0.35)
        default: return RoomInk.amber
        }
    }

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: !animates)) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            let pulse = animates ? 0.55 + 0.45 * (0.5 + 0.5 * sin(t * .pi / 0.9)) : 1
            let turn = animates ? (t.truncatingRemainder(dividingBy: 1.4) / 1.4) * 360 : 0
            ZStack {
                switch ring {
                case .thinking:
                    Circle().stroke(color.opacity(0.2), lineWidth: 10)
                    Circle()
                        .trim(from: 0, to: 0.25)
                        .stroke(color, lineWidth: 10)
                        .rotationEffect(.degrees(turn))
                case .listening, .call:
                    Circle().stroke(color.opacity(pulse), lineWidth: 10)
                case .speaking:
                    Circle().stroke(color, lineWidth: 16)
                default:
                    Circle().stroke(color, lineWidth: 10)
                }
                Text(RoomStatusText.label(ring))
                    .font(.system(size: 22))
                    .foregroundStyle(ring == .micBlocked ? RoomInk.red : RoomInk.text1)
            }
            .frame(width: size, height: size)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(RoomModeStrings.stateDescription(RoomStatusText.label(ring)))
        .accessibilityAddTraits(.updatesFrequently)
        .accessibilityIdentifier("room-ring")
    }
}

/// Follows the recorder itself: "mic off" only once no recorder is open,
/// whatever the Mute button says; "mic live" while what it hears goes to
/// the server (`RoomModeActivity.kt:418-446`).
struct RoomMicChip: View {
    let open: Bool
    let live: Bool
    let muted: Bool

    var body: some View {
        let off = !open
        let ink: Color = off ? RoomInk.dim : (live ? RoomInk.red : RoomInk.text2)
        HStack(spacing: 8) {
            Circle()
                .fill(live && !off ? RoomInk.red : ink)
                .frame(width: 10, height: 10)
            Text(off ? RoomModeStrings.micOff : (live ? RoomModeStrings.micLive : RoomModeStrings.micListening))
                .font(.footnote)
                .foregroundStyle(ink)
                .strikethrough(off && muted)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .overlay(Capsule().stroke(ink, lineWidth: 1))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(RoomModeStrings.micChipDescription(off: off, live: live))
        .accessibilityIdentifier("room-mic-chip")
    }
}

/// Tells the room screen that someone touched it, wherever they did, without
/// taking the touch: a UIKit recognizer on the window that reports a touch
/// as it begins and then fails, so it never claims, delays or cancels one.
/// The buttons and the scroll get every tap exactly as they would without
/// it (unlike a SwiftUI gesture on the screen, which competes with them).
struct RoomTouchWatcher: UIViewRepresentable {
    let onTouch: () -> Void

    func makeUIView(context: Context) -> RoomTouchWatcherView {
        let view = RoomTouchWatcherView()
        view.isUserInteractionEnabled = false
        view.onTouch = onTouch
        return view
    }

    func updateUIView(_ view: RoomTouchWatcherView, context: Context) {
        view.onTouch = onTouch
    }

    static func dismantleUIView(_ view: RoomTouchWatcherView, coordinator: ()) {
        view.detach()
    }
}

/// Installs ``RoomTouchBegan`` on whichever window it is in, and takes it
/// off again when it leaves.
final class RoomTouchWatcherView: UIView {
    var onTouch: (() -> Void)?
    private var recognizer: RoomTouchBegan?

    override func didMoveToWindow() {
        super.didMoveToWindow()
        detach()
        guard let window else { return }
        let r = RoomTouchBegan(target: nil, action: nil)
        r.onTouch = { [weak self] in self?.onTouch?() }
        r.cancelsTouchesInView = false
        r.delaysTouchesBegan = false
        r.delaysTouchesEnded = false
        window.addGestureRecognizer(r)
        recognizer = r
    }

    func detach() {
        if let r = recognizer { r.view?.removeGestureRecognizer(r) }
        recognizer = nil
    }
}

/// Reports the first touch of a sequence and fails at once: a recognizer
/// that has failed can neither win nor block another.
final class RoomTouchBegan: UIGestureRecognizer {
    var onTouch: (() -> Void)?

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
        onTouch?()
        state = .failed
    }

    override func canPrevent(_ preventedGestureRecognizer: UIGestureRecognizer) -> Bool { false }

    override func canBePrevented(by preventingGestureRecognizer: UIGestureRecognizer) -> Bool { false }
}

struct RoomBigButton: View {
    let title: String
    let fill: Color
    let ink: Color
    let compact: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 24, weight: .medium))
                .foregroundStyle(ink)
                .frame(maxWidth: 420)
                .frame(minHeight: compact ? 56 : 72)
                .background(fill, in: Capsule())
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 32)
    }
}

/// A tap does nothing (a room is not left by accident); a hold of 0.8 s, or
/// VoiceOver's "exit room mode" action, asks for the phone's unlock.
struct RoomExitControl: View {
    let onExit: () -> Void

    var body: some View {
        Text(RoomModeStrings.exitHold)
            .font(.subheadline)
            .foregroundStyle(RoomInk.text2)
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(RoomInk.dim, lineWidth: 1))
            .contentShape(Rectangle())
            .onLongPressGesture(minimumDuration: 0.8, perform: onExit)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(RoomModeStrings.exitHint)
            .accessibilityAddTraits(.isButton)
            .accessibilityAction(named: RoomModeStrings.exitAction, onExit)
            .accessibilityIdentifier("room-exit")
    }
}

/// How to keep the phone in Domovoi (ROOMMODE-MAP §3.5). Advice, never
/// required.
struct RoomGuidedAccessSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Text(RoomModeStrings.guidedAccessIntro)
                        .fixedSize(horizontal: false, vertical: true)
                    ForEach(Array(RoomModeStrings.guidedAccessSteps.enumerated()), id: \.offset) { index, step in
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            Text("\(index + 1).")
                                .font(.body.weight(.semibold))
                                .foregroundStyle(Color.brandInk)
                            Text(step)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Text(RoomModeStrings.guidedAccessAfter)
                        .font(.subheadline)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(20)
            }
            .navigationTitle(RoomModeStrings.guidedAccessTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button(RoomModeStrings.done) { dismiss() }
                }
            }
        }
    }
}
